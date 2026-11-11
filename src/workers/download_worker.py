"""下载工作线程。

在 ``QThreadPool`` 子线程中执行阻塞下载，并通过 ``threading.Event``
响应主线程的暂停 / 取消请求（yt-dlp 原生不支持中断，只能在
``progress_hooks`` 中主动抛异常打断）。
"""

from __future__ import annotations

import glob
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRunnable, Slot

from src.constants import PHASE_MERGING, PROGRESS_EMIT_INTERVAL
from src.core.exceptions import DownloadAbort, DownloadPause, MergeError
from src.core.ffmpeg_merger import FfmpegMerger
from src.core.models import DownloadTask, ProgressInfo, TaskStatus
from src.core.ytdlp_client import YtDlpClient
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

        self._abort: threading.Event = threading.Event()
        self._pause: threading.Event = threading.Event()
        self._last_emit: float = 0.0
        self._finished: bool = False
        # 本任务实际触达的媒体 id 与目标文件名：取消时据此精确清理临时文件，
        # 不再靠「标题前 10 个字符」猜——那样会把同名前缀的其他任务一起删掉。
        self._media_ids: set[str] = set()
        self._seen_files: set[str] = set()

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

        # 记录本次下载的真实身份：id 用于精确匹配临时文件，filename 兜底。
        payload = d or {}
        info = payload.get("info_dict") or {}
        media_id = info.get("id")
        if media_id:
            self._media_ids.add(str(media_id))
        filename = payload.get("filename")
        if filename:
            self._seen_files.add(os.path.abspath(str(filename)))

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

            if self._abort.is_set():
                raise DownloadAbort(task_id=task.task_id)

            # yt-dlp 通常已内部合并；若产物缺失且本任务需要合并，再用 ffmpeg 兜底。
            if task.needs_merge and not self._file_ready(filepath):
                filepath = self._fallback_merge(filepath)

            # 产物校验对所有格式生效。合流格式（format 18、bilibili durl 等）
            # needs_merge=False，之前完全不校验：一旦扩展名推断错，就会把一个
            # 不存在的路径当成功上报，界面显示「已完成」而目录里什么都没有。
            if not self._file_ready(filepath):
                logger.error("下载结束但未找到产物：%s | 推断路径=%s", task.title, filepath)
                self._emit_failed("下载结束但未找到产物文件，请重试；若反复出现请检查下载目录权限与磁盘空间")
                return

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
            # 注意：这里是失败**兜底**路径，自身绝不能再抛异常。
            # 原实现直接访问 self._client._config，客户端一旦是替身或实现变更，
            # AttributeError 会逃出 run()，task_failed 永不发出 → 任务永久停在
            # DOWNLOADING，并发名额泄漏且只能重启。
            try:
                getter = getattr(self._client, "cookie_path", None)
                cookie_path = getter() if callable(getter) else None
                message = YtDlpClient.probe_error(exc, cookie_path=cookie_path)
            except Exception:  # noqa: BLE001
                logger.error("错误翻译失败，回退为原始异常文案", exc_info=True)
                message = f"下载失败：{exc}"
            logger.error("下载失败：%s | %s", task.title, exc, exc_info=True)
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

        out_path = self._merger.merge(video_part, audio_part, expected_path,
                                      cancel_event=self._abort)
        self._merger.cleanup_parts(video_part, audio_part)
        return out_path

    def _cleanup_temp_files(self) -> None:
        """取消后清理**本任务**的 ``.part`` / ``.ytdl`` 临时文件。

        匹配依据（按可靠性排序）：
          1. yt-dlp 在 progress hook 里回报的媒体 id —— ``OUTPUT_TEMPLATE``
             形如 ``%(title).150B [%(id)s].%(ext)s``，``[id]`` 全局唯一；
          2. 本次实际触达的目标文件名（``d["filename"]``）。

        绝不按标题前缀猜：原实现比较标题前 10 个字符，而 yt-dlp 会自行净化
        文件名，「Python 教程 第1集」与「第2集」前 10 字符完全相同——取消 A
        会把处于暂停态（无文件句柄、Windows 不拦截删除）的 B 的已下载进度
        静默删掉，B 恢复时从 0 重下。
        """
        output_dir = self._task.output_dir
        if not output_dir or not os.path.isdir(output_dir):
            return

        targets: set[str] = set()
        for media_id in self._media_ids:
            # glob.escape 处理 ``[`` / ``*`` / ``?``，避免把 [id] 当成字符类
            tag = glob.escape(f"[{media_id}]")
            targets.update(glob.glob(os.path.join(output_dir, f"*{tag}*.part*")))
            targets.update(glob.glob(os.path.join(output_dir, f"*{tag}*.ytdl")))

        for filename in self._seen_files:
            literal = glob.escape(filename)
            targets.update(glob.glob(literal + ".part*"))
            targets.update(glob.glob(literal + ".ytdl"))
            # 分片下载的目标形如 ``xxx.f137.mp4``，其 .part 位于同一 stem 下
            stem = glob.escape(os.path.splitext(filename)[0])
            targets.update(glob.glob(stem + "*.part*"))

        if not targets:
            logger.debug("未识别到本任务的临时文件，跳过清理（不按标题猜测）")
            return

        for path in targets:
            try:
                os.remove(path)
                logger.debug("已清理临时文件：%s", path)
            except OSError as exc:
                logger.debug("清理临时文件失败 %s：%s", path, exc)


__all__ = ["DownloadWorker"]
