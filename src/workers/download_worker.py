"""下载工作线程。

在 ``QThreadPool`` 子线程中执行阻塞下载，并通过 ``threading.Event``
响应主线程的暂停 / 取消请求（yt-dlp 原生不支持中断，只能在
``progress_hooks`` 中主动抛异常打断）。
"""

from __future__ import annotations

import glob
import logging
import os
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRunnable, Slot

from src.constants import PHASE_MERGING, PROGRESS_EMIT_INTERVAL
from src.core.exceptions import DownloadAbort, DownloadPause, MergeError
from src.core.ffmpeg_merger import FfmpegMerger
from src.core.models import DownloadTask, ProgressInfo, TaskStatus
from src.core.ytdlp_client import YtDlpClient
from src.utils.paths import sanitize_filename
from src.workers.signals import WorkerSignals

logger = logging.getLogger(__name__)


class DownloadWorker(QRunnable):
    """执行单个下载任务的可运行对象。

    Attributes:
        signals: 信号载体，由 ``DownloadManager`` 连接。
    """

    def __init__(self, task: DownloadTask, client: YtDlpClient, merger: FfmpegMerger) -> None:
        """初始化下载任务。

        Args:
            task: 下载任务（Worker 只读静态字段）。
            client: yt-dlp 客户端。
            merger: ffmpeg 合并器（兜底合并用）。
        """
        super().__init__()
        self.signals: WorkerSignals = WorkerSignals()
        self._task: DownloadTask = task
        self._client: YtDlpClient = client
        self._merger: FfmpegMerger = merger

        import threading

        self._abort: threading.Event = threading.Event()
        self._pause: threading.Event = threading.Event()
        self._last_emit: float = 0.0
        self._finished: bool = False

        self.setAutoDelete(False)  # 暂停后仍需保留对象，由 Manager 管理生命周期

    # ------------------------------------------------------------------
    # 外部控制（主线程调用）
    # ------------------------------------------------------------------
    @property
    def task_id(self) -> str:
        """所属任务 id。"""
        return self._task.task_id

    def pause(self) -> None:
        """请求暂停（保留 ``.part`` 以便断点续传）。"""
        self._pause.set()

    def cancel(self) -> None:
        """请求取消（结束后清理临时文件）。"""
        self._abort.set()

    def is_finished(self) -> bool:
        """``run()`` 是否已结束。"""
        return self._finished

    def abort_flag(self) -> Any:
        """返回取消标志对象，供 ``YtDlpClient.download`` 提前检查。"""
        return self._abort

    # ------------------------------------------------------------------
    # 进度回调（子线程内被 yt-dlp 调用）
    # ------------------------------------------------------------------
    def _on_progress(self, d: dict[str, Any]) -> None:
        """yt-dlp 进度回调：检查中断标志 + 节流后发送进度信号。

        Args:
            d: yt-dlp 回调参数。

        Raises:
            DownloadAbort: 用户请求取消。
            DownloadPause: 用户请求暂停。
        """
        if self._abort.is_set():
            raise DownloadAbort(task_id=self._task.task_id)
        if self._pause.is_set():
            raise DownloadPause(task_id=self._task.task_id)

        status = str((d or {}).get("status") or "")
        now = time.monotonic()
        if now - self._last_emit < PROGRESS_EMIT_INTERVAL and status != "finished":
            return
        self._last_emit = now

        progress = ProgressInfo.from_hook(d or {})
        self._emit_progress(progress)

    def _emit_progress(self, progress: ProgressInfo) -> None:
        """安全地发送进度信号（接收者已销毁时静默忽略）。"""
        try:
            self.signals.task_progress.emit(self._task.task_id, progress.to_dict())
        except RuntimeError:
            logger.debug("task_progress 信号发送失败：接收者已销毁")

    def _emit_status(self, status: TaskStatus) -> None:
        """安全地发送状态变更信号。"""
        try:
            self.signals.task_status_changed.emit(self._task.task_id, status.value)
        except RuntimeError:
            logger.debug("task_status_changed 信号发送失败：接收者已销毁")

    # ------------------------------------------------------------------
    # 线程入口
    # ------------------------------------------------------------------
    @Slot()
    def run(self) -> None:
        """子线程入口：整体 try/except 包裹，分支处理完成 / 暂停 / 取消 / 失败。"""
        task = self._task
        try:
            self._emit_status(TaskStatus.DOWNLOADING)
            filepath = self._client.download(task, self._on_progress, self._abort)

            # yt-dlp 通常已内部合并；若产物缺失则尝试用 FfmpegMerger 兜底
            if task.needs_merge and not self._file_ready(filepath):
                filepath = self._fallback_merge(filepath)

            if self._abort.is_set():
                raise DownloadAbort(task_id=task.task_id)

            self._emit_progress(ProgressInfo(percent=100.0, phase="downloading"))
            try:
                self.signals.task_finished.emit(task.task_id, str(filepath or ""))
            except RuntimeError:
                logger.debug("task_finished 信号发送失败：接收者已销毁")
            logger.info("下载完成：%s → %s", task.title, filepath)

        except DownloadPause:
            logger.info("任务已暂停：%s", task.title)
            self._emit_status(TaskStatus.PAUSED)

        except DownloadAbort:
            logger.info("任务已取消：%s", task.title)
            self._cleanup_temp_files()
            self._emit_status(TaskStatus.CANCELED)

        except MergeError as exc:
            logger.error("合并失败：%s | %s", task.title, exc.detail or exc.message)
            self._emit_failed(exc.message)

        except Exception as exc:  # noqa: BLE001 - 子线程必须吞掉所有异常
            cookie_path = self._client._config.cookiefile()  # type: ignore[attr-defined]
            message = YtDlpClient.probe_error(exc, cookie_path=cookie_path)
            logger.error("下载失败：%s | %s", task.title, exc, exc_info=False)
            self._emit_failed(message)

        finally:
            self._finished = True

    def _emit_failed(self, message: str) -> None:
        """安全地发送失败信号。"""
        try:
            self.signals.task_failed.emit(self._task.task_id, message)
        except RuntimeError:
            logger.debug("task_failed 信号发送失败：接收者已销毁")

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _file_ready(filepath: str | None) -> bool:
        """判断产物文件是否已存在且非空。"""
        if not filepath:
            return False
        try:
            target = Path(filepath)
            return target.is_file() and target.stat().st_size > 0
        except OSError:
            return False

    def _fallback_merge(self, expected_path: str) -> str:
        """yt-dlp 未产出合并文件时，用 ffmpeg 兜底合并分片。

        Args:
            expected_path: 期望的最终文件路径。

        Returns:
            合并后（或找到的）文件路径。

        Raises:
            MergeError: 找不到分片或合并失败。
        """
        self._emit_status(TaskStatus.MERGING)
        self._emit_progress(ProgressInfo(percent=-1.0, phase=PHASE_MERGING))

        stem, _ext = os.path.splitext(expected_path)
        candidates = sorted(glob.glob(f"{glob.escape(stem)}.f*"))
        video_part: str | None = None
        audio_part: str | None = None

        for path in candidates:
            if path.endswith((".part", ".ytdl")):
                continue
            lowered = path.lower()
            if video_part is None and lowered.endswith((".mp4", ".webm", ".mkv")):
                video_part = path
            elif audio_part is None and lowered.endswith((".m4a", ".webm", ".opus", ".aac", ".mp3")):
                audio_part = path

        if not video_part or not audio_part:
            raise MergeError(
                "音视频合并失败：未找到可合并的音视频分片",
                detail=f"expected={expected_path}, candidates={candidates}",
            )

        out_path = self._merger.merge(video_part, audio_part, expected_path)
        self._merger.cleanup_parts(video_part, audio_part)
        return out_path

    def _cleanup_temp_files(self) -> None:
        """取消后清理**本任务**的 ``.part`` / ``.ytdl`` 临时文件。

        为避免误删其他并发任务的临时文件，只清理文件名前缀与本任务标题
        匹配的项（yt-dlp 的 outtmpl 以标题开头）。
        """
        output_dir = self._task.output_dir
        if not output_dir or not os.path.isdir(output_dir):
            return

        prefix = sanitize_filename(self._task.title or "")[:40].strip()
        if not prefix:
            logger.debug("任务标题为空，跳过临时文件清理")
            return

        try:
            for pattern in ("*.part", "*.ytdl", "*.part-Frag*"):
                for path in glob.glob(os.path.join(output_dir, pattern)):
                    name = os.path.basename(path)
                    if not name.startswith(prefix[:10]):
                        continue  # 不属于本任务，跳过
                    try:
                        os.remove(path)
                        logger.debug("已清理临时文件：%s", path)
                    except OSError as exc:
                        logger.debug("清理临时文件失败 %s：%s", path, exc)
        except OSError as exc:
            logger.warning("清理临时文件时出错：%s", exc)


__all__ = ["DownloadWorker"]
