"""下载调度核心。

``DownloadManager`` 是 UI 与 Worker 之间的**唯一中介**：
- UI 只调用它的公开方法（命令），只监听它的信号（事件）；
- Worker 只通过 ``WorkerSignals`` 把结果回传给它。

并发策略、重试策略、全局统计全部集中于此，未来扩展（限速、批量导入）
只需改这一个文件。

注意：本文件是 ``src/core/`` 下唯一允许 import Qt 的模块，且**只允许**
使用 ``PySide6.QtCore``，禁止 ``QtWidgets``。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from src.constants import (
    SHUTDOWN_WAIT_MS,
    STATS_REFRESH_INTERVAL_MS,
    THREAD_POOL_EXTRA,
)
from src.core.config_store import ConfigStore
from src.core.ffmpeg_merger import FfmpegMerger
from src.core.history_store import HistoryStore
from src.core.models import DownloadTask, MediaFormat, MediaInfo, ProgressInfo, TaskStatus
from src.core.ytdlp_client import YtDlpClient, is_non_retryable
from src.utils.paths import open_in_explorer
from src.workers.download_worker import DownloadWorker
from src.workers.parse_worker import ParseWorker

logger = logging.getLogger(__name__)


class DownloadManager(QObject):
    """任务生命周期与并发调度管理器。

    Signals:
        parse_ready: ``(object)`` —— 解析成功，携带 ``MediaInfo``。
        parse_error: ``(str)`` —— 解析失败，携带中文错误消息。
        task_added: ``(object)`` —— 新任务已加入，携带 ``DownloadTask``。
        task_updated: ``(str)`` —— 任务状态或进度变化，携带 ``task_id``。
        task_removed: ``(str)`` —— 任务已从列表移除，携带 ``task_id``。
        stats_updated: ``(dict)`` —— 全局统计刷新。
        notify: ``(str, str)`` —— 需要提示给用户的消息（标题, 内容）。
    """

    parse_ready = Signal(object)
    parse_error = Signal(str)
    task_added = Signal(object)
    task_updated = Signal(str)
    task_removed = Signal(str)
    stats_updated = Signal(dict)
    notify = Signal(str, str)

    def __init__(
        self,
        config: ConfigStore,
        client: YtDlpClient,
        merger: FfmpegMerger,
        history: HistoryStore,
        parent: QObject | None = None,
    ) -> None:
        """初始化调度器。

        Args:
            config: 配置存取器。
            client: yt-dlp 客户端。
            merger: ffmpeg 合并器。
            history: 历史记录仓库。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._config: ConfigStore = config
        self._client: YtDlpClient = client
        self._merger: FfmpegMerger = merger
        self._history: HistoryStore = history

        self._tasks: dict[str, DownloadTask] = {}
        self._workers: dict[str, DownloadWorker] = {}
        self._queue: deque[str] = deque()
        self._order: list[str] = []

        self._pool: QThreadPool = QThreadPool(self)
        self._pool.setMaxThreadCount(self._config.max_concurrent() + THREAD_POOL_EXTRA)

        self._parsing: bool = False
        self._parse_worker: ParseWorker | None = None
        self._shutting_down: bool = False

        # 1 秒定时刷新全局统计，避免每个进度包都重算
        self._stats_timer: QTimer = QTimer(self)
        self._stats_timer.setInterval(STATS_REFRESH_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._emit_stats)
        self._stats_timer.start()

        logger.info("DownloadManager 初始化完成（最大并发=%d）", self._config.max_concurrent())

    # ==================================================================
    # 只读访问
    # ==================================================================
    @property
    def config(self) -> ConfigStore:
        """配置存取器。"""
        return self._config

    @property
    def history(self) -> HistoryStore:
        """历史记录仓库。"""
        return self._history

    @property
    def is_parsing(self) -> bool:
        """当前是否有解析任务在进行。"""
        return self._parsing

    def task(self, task_id: str) -> DownloadTask | None:
        """按 id 获取任务。

        Args:
            task_id: 任务 id。

        Returns:
            任务对象；不存在返回 ``None``。
        """
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[DownloadTask]:
        """按加入顺序返回全部任务。"""
        return [self._tasks[tid] for tid in self._order if tid in self._tasks]

    def has_active_tasks(self) -> bool:
        """是否存在运行中或排队中的任务。"""
        return any(
            task.status in (TaskStatus.DOWNLOADING, TaskStatus.MERGING, TaskStatus.QUEUED)
            for task in self._tasks.values()
        )

    # ==================================================================
    # 解析
    # ==================================================================
    def parse(self, url: str) -> bool:
        """异步解析链接（同一时刻只允许一个解析任务）。

        Args:
            url: 视频链接。

        Returns:
            是否成功发起解析。
        """
        clean_url = str(url or "").strip()
        if not clean_url:
            self.parse_error.emit("请输入视频链接")
            return False

        if self._parsing:
            self.parse_error.emit("正在解析中，请稍候")
            return False

        self._parsing = True
        worker = ParseWorker(clean_url, self._client)
        worker.signals.parse_finished.connect(self._on_parse_finished)
        worker.signals.parse_failed.connect(self._on_parse_failed)
        self._parse_worker = worker  # 持有引用，避免被 GC
        self._pool.start(worker)
        logger.info("已提交解析任务：%s", clean_url)
        return True

    @Slot(object)
    def _on_parse_finished(self, info: object) -> None:
        """解析成功回调（主线程）。"""
        self._parsing = False
        self._parse_worker = None
        if isinstance(info, MediaInfo):
            self.parse_ready.emit(info)
        else:  # pragma: no cover - 理论上不会发生
            self.parse_error.emit("解析结果格式异常")

    @Slot(str, str)
    def _on_parse_failed(self, url: str, message: str) -> None:
        """解析失败回调（主线程）。"""
        self._parsing = False
        self._parse_worker = None
        logger.warning("解析失败 %s：%s", url, message)
        self.parse_error.emit(message)

    # ==================================================================
    # 任务管理
    # ==================================================================
    def add_task(self, info: MediaInfo, fmt: MediaFormat, container: str | None = None) -> str:
        """新增下载任务并尝试调度。

        Args:
            info: 解析得到的媒体信息。
            fmt: 用户选中的格式。
            container: 用户指定的容器格式（可选）。

        Returns:
            新任务的 ``task_id``。
        """
        task = DownloadTask.from_media(
            url=info.url,
            title=info.title,
            fmt=fmt,
            output_dir=self._config.download_dir(),
            max_retries=self._config.max_retries(),
            container=container,
        )
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        self._queue.append(task.task_id)

        logger.info("新增任务 %s：%s（%s）", task.task_id, task.title, task.resolution)
        self.task_added.emit(task)
        self._schedule()
        self._emit_stats()
        return task.task_id

    def remove_task(self, task_id: str) -> bool:
        """从任务列表中移除任务（运行中的先取消）。

        Args:
            task_id: 任务 id。

        Returns:
            是否成功移除。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False

        if task.is_active():
            self.cancel_task(task_id)

        self._tasks.pop(task_id, None)
        self._workers.pop(task_id, None)
        if task_id in self._order:
            self._order.remove(task_id)
        self._drop_from_queue(task_id)

        self.task_removed.emit(task_id)
        self._schedule()
        self._emit_stats()
        return True

    def clear_finished(self) -> int:
        """清除所有已完成 / 失败 / 取消的任务。

        Returns:
            被清除的任务数量。
        """
        targets = [tid for tid, task in self._tasks.items() if task.is_terminal()]
        for task_id in targets:
            self.remove_task(task_id)
        return len(targets)

    def start_task(self, task_id: str) -> None:
        """把任务放入队列并触发调度（用于手动启动）。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None or task.is_active():
            return
        task.reset_for_retry()
        self._enqueue(task_id)
        self.task_updated.emit(task_id)
        self._schedule()

    def pause_task(self, task_id: str) -> None:
        """暂停任务。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return

        worker = self._workers.get(task_id)
        if worker is not None and task.is_active():
            worker.pause()
            logger.info("请求暂停任务：%s", task.title)
            return

        # 仍在队列中未启动，直接置为暂停
        if task.status == TaskStatus.QUEUED:
            self._drop_from_queue(task_id)
            task.status = TaskStatus.PAUSED
            self.task_updated.emit(task_id)
            self._schedule()
            self._emit_stats()

    def resume_task(self, task_id: str) -> None:
        """恢复已暂停的任务（断点续传）。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None or task.status != TaskStatus.PAUSED:
            return

        self._workers.pop(task_id, None)  # 丢弃旧 worker，重新创建
        task.status = TaskStatus.QUEUED
        task.error_message = None
        self._enqueue(task_id)
        self.task_updated.emit(task_id)
        self._schedule()
        self._emit_stats()

    def cancel_task(self, task_id: str) -> None:
        """取消任务。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal():
            return

        worker = self._workers.get(task_id)
        if worker is not None and task.is_active():
            worker.cancel()
            logger.info("请求取消任务：%s", task.title)
            return

        # 未启动的任务直接置为已取消
        self._drop_from_queue(task_id)
        self._workers.pop(task_id, None)
        task.status = TaskStatus.CANCELED
        task.finished_at = time.time()
        self.task_updated.emit(task_id)
        self._schedule()
        self._emit_stats()

    def retry_task(self, task_id: str) -> None:
        """手动重试失败 / 取消的任务（重置重试计数）。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None or task.is_active():
            return

        task.retry_count = 0
        task.reset_for_retry()
        self._workers.pop(task_id, None)
        self._enqueue(task_id)
        self.task_updated.emit(task_id)
        self._schedule()
        self._emit_stats()
        logger.info("手动重试任务：%s", task.title)

    def open_folder(self, task_id: str) -> bool:
        """在资源管理器中定位任务产物。

        Args:
            task_id: 任务 id。

        Returns:
            是否成功发起打开操作。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        target = task.filepath or task.output_dir
        return open_in_explorer(target)

    def apply_concurrency(self) -> None:
        """配置变更后同步并发上限并立即重新调度。"""
        max_concurrent = self._config.max_concurrent()
        self._pool.setMaxThreadCount(max_concurrent + THREAD_POOL_EXTRA)
        logger.info("并发上限已更新为 %d", max_concurrent)
        self._schedule()

    # ==================================================================
    # 调度核心
    # ==================================================================
    def _enqueue(self, task_id: str) -> None:
        """把任务加入等待队列（去重）。"""
        if task_id not in self._queue:
            self._queue.append(task_id)

    def _drop_from_queue(self, task_id: str) -> None:
        """从等待队列中移除任务（不存在则忽略）。"""
        try:
            self._queue.remove(task_id)
        except ValueError:
            pass

    def _running_count(self) -> int:
        """当前占用并发名额的任务数。"""
        return sum(1 for task in self._tasks.values() if task.is_active())

    def _schedule(self) -> None:
        """并发闸门：在不超过最大并发数的前提下拉起排队任务。"""
        if self._shutting_down:
            return

        max_concurrent = self._config.max_concurrent()
        running = self._running_count()

        while running < max_concurrent and self._queue:
            task_id = self._queue.popleft()
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.QUEUED:
                continue
            self._launch(task_id)
            running += 1

    def _launch(self, task_id: str) -> None:
        """创建并启动 ``DownloadWorker``。

        Args:
            task_id: 任务 id。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return

        worker = DownloadWorker(task, self._client, self._merger)
        worker.signals.task_progress.connect(self._on_task_progress)
        worker.signals.task_status_changed.connect(self._on_task_status_changed)
        worker.signals.task_finished.connect(self._on_task_finished)
        worker.signals.task_failed.connect(self._on_task_failed)

        self._workers[task_id] = worker
        task.status = TaskStatus.DOWNLOADING
        self.task_updated.emit(task_id)

        self._pool.start(worker)
        logger.info("启动下载任务：%s（运行中 %d/%d）", task.title, self._running_count(), self._config.max_concurrent())

    # ==================================================================
    # Worker 信号回调（均在主线程执行）
    # ==================================================================
    @Slot(str, dict)
    def _on_task_progress(self, task_id: str, progress: dict[str, Any]) -> None:
        """进度更新回调。"""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.progress = ProgressInfo.from_dict(progress)
        if task.progress.total:
            task.filesize = int(task.progress.total)
        self.task_updated.emit(task_id)

    @Slot(str, str)
    def _on_task_status_changed(self, task_id: str, status_value: str) -> None:
        """Worker 主动上报的状态变更回调。"""
        task = self._tasks.get(task_id)
        if task is None:
            return

        status = TaskStatus.from_value(status_value)
        task.status = status

        if status in (TaskStatus.PAUSED, TaskStatus.CANCELED):
            if status == TaskStatus.CANCELED:
                task.finished_at = time.time()
            self._workers.pop(task_id, None)
            self._drop_from_queue(task_id)
            self.task_updated.emit(task_id)
            self._schedule()  # 释放并发名额
            self._emit_stats()
            return

        self.task_updated.emit(task_id)

    @Slot(str, str)
    def _on_task_finished(self, task_id: str, filepath: str) -> None:
        """下载完成回调：落历史、发通知、拉起下一个任务。"""
        task = self._tasks.get(task_id)
        if task is None:
            return

        task.status = TaskStatus.COMPLETED
        task.filepath = filepath or None
        task.finished_at = time.time()
        task.error_message = None
        task.progress.percent = 100.0
        task.progress.speed = 0.0

        self._workers.pop(task_id, None)
        self._drop_from_queue(task_id)

        try:
            self._history.add(task)
        except Exception as exc:  # noqa: BLE001 - 历史写入失败不应影响主流程
            logger.error("写入历史记录失败：%s", exc)

        self.task_updated.emit(task_id)
        self.notify.emit("下载完成", task.title)

        if self._config.auto_open_folder():
            open_in_explorer(task.filepath or task.output_dir)

        self._schedule()
        self._emit_stats()

    @Slot(str, str)
    def _on_task_failed(self, task_id: str, message: str) -> None:
        """下载失败回调：按策略自动重试或置为失败。"""
        task = self._tasks.get(task_id)
        if task is None:
            return

        self._workers.pop(task_id, None)
        self._drop_from_queue(task_id)
        task.error_message = message

        can_retry = (
            not self._shutting_down
            and task.retry_count < task.max_retries
            and not is_non_retryable(message)
        )

        if can_retry:
            task.retry_count += 1
            task.status = TaskStatus.QUEUED
            task.progress = ProgressInfo()
            delay_ms = (2 ** task.retry_count) * 1000  # 指数退避
            logger.warning(
                "任务失败将重试（%d/%d，%.1fs 后）：%s | %s",
                task.retry_count, task.max_retries, delay_ms / 1000, task.title, message,
            )
            self.task_updated.emit(task_id)
            QTimer.singleShot(delay_ms, lambda tid=task_id: self._requeue_after_backoff(tid))
        else:
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()
            logger.error("任务最终失败：%s | %s", task.title, message)
            self.task_updated.emit(task_id)
            self.notify.emit("下载失败", f"{task.title}：{message}")

        self._schedule()
        self._emit_stats()

    def _requeue_after_backoff(self, task_id: str) -> None:
        """指数退避结束后重新入队（任务可能已被用户取消或移除）。"""
        task = self._tasks.get(task_id)
        if task is None or task.status != TaskStatus.QUEUED or self._shutting_down:
            return
        self._enqueue(task_id)
        self._schedule()

    # ==================================================================
    # 全局统计
    # ==================================================================
    def global_stats(self) -> dict[str, Any]:
        """汇总全局统计信息。

        Returns:
            ``{"active", "queued", "paused", "completed", "failed", "total", "total_speed"}``
        """
        active = queued = paused = completed = failed = 0
        total_speed = 0.0

        for task in self._tasks.values():
            if task.status in (TaskStatus.DOWNLOADING, TaskStatus.MERGING):
                active += 1
                if task.status == TaskStatus.DOWNLOADING:
                    total_speed += float(task.progress.speed or 0.0)
            elif task.status == TaskStatus.QUEUED:
                queued += 1
            elif task.status == TaskStatus.PAUSED:
                paused += 1
            elif task.status == TaskStatus.COMPLETED:
                completed += 1
            elif task.status == TaskStatus.FAILED:
                failed += 1

        return {
            "active": active,
            "queued": queued,
            "paused": paused,
            "completed": completed,
            "failed": failed,
            "total": len(self._tasks),
            "total_speed": total_speed,
        }

    def _emit_stats(self) -> None:
        """发送全局统计信号。"""
        if self._shutting_down:
            return
        self.stats_updated.emit(self.global_stats())

    # ==================================================================
    # 关闭
    # ==================================================================
    def shutdown(self) -> None:
        """应用退出时的收尾：中止活跃任务、等待线程池、关闭资源。"""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("DownloadManager 开始关闭…")

        self._stats_timer.stop()
        self._queue.clear()

        for worker in list(self._workers.values()):
            try:
                worker.cancel()
            except RuntimeError:
                pass

        self._pool.clear()
        if not self._pool.waitForDone(SHUTDOWN_WAIT_MS):
            logger.warning("仍有工作线程未在 %d ms 内结束，将强制退出", SHUTDOWN_WAIT_MS)

        self._workers.clear()

        try:
            self._history.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭历史数据库失败：%s", exc)

        try:
            self._config.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存配置失败：%s", exc)

        logger.info("DownloadManager 关闭完成")


__all__ = ["DownloadManager"]
