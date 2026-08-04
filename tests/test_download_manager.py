"""``src.core.download_manager`` 单元测试。

策略：不真正启动线程池 Worker（用 ``_launch`` 桩替换），直接驱动 Manager 的
槽函数，从而在无事件循环的情况下确定性地验证调度、重试与统计逻辑。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.download_manager import DownloadManager
from src.core.history_store import HistoryStore
from src.core.models import MediaFormat, MediaInfo, ProgressInfo, TaskStatus

pytestmark = pytest.mark.qt


class SignalRecorder:
    """把信号发射记录成列表，便于断言。"""

    def __init__(self) -> None:
        """初始化各信号的记录容器。"""
        self.task_added: list[Any] = []
        self.task_updated: list[str] = []
        self.task_removed: list[str] = []
        self.stats: list[dict[str, Any]] = []
        self.notify: list[tuple[str, str]] = []
        self.parse_ready: list[Any] = []
        self.parse_error: list[str] = []

    def bind(self, manager: DownloadManager) -> "SignalRecorder":
        """连接到 Manager 的全部信号。"""
        manager.task_added.connect(self.task_added.append)
        manager.task_updated.connect(self.task_updated.append)
        manager.task_removed.connect(self.task_removed.append)
        manager.stats_updated.connect(self.stats.append)
        manager.notify.connect(lambda title, msg: self.notify.append((title, msg)))
        manager.parse_ready.connect(self.parse_ready.append)
        manager.parse_error.connect(self.parse_error.append)
        return self


@pytest.fixture()
def recorder(manager: DownloadManager) -> SignalRecorder:
    """已绑定到 manager 的信号记录器。"""
    return SignalRecorder().bind(manager)


@pytest.fixture()
def launched(manager: DownloadManager, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """用桩替换 ``_launch``，只记录被拉起的 task_id 并置为下载中。"""
    records: list[str] = []

    def _fake_launch(task_id: str) -> None:
        task = manager.task(task_id)
        if task is None:
            return
        records.append(task_id)
        task.status = TaskStatus.DOWNLOADING
        manager.task_updated.emit(task_id)

    monkeypatch.setattr(manager, "_launch", _fake_launch)
    return records


def _add(manager: DownloadManager, info: MediaInfo, fmt: MediaFormat, title: str = "") -> str:
    """便捷地新增一个任务。"""
    target = info
    if title:
        target = MediaInfo(
            url=f"{info.url}&t={title}",
            title=title,
            uploader=info.uploader,
            duration=info.duration,
            thumbnail=info.thumbnail,
            extractor=info.extractor,
            formats=info.formats,
        )
    return manager.add_task(target, fmt)


# ======================================================================
# 任务新增与调度
# ======================================================================
class TestAddAndSchedule:
    """新增任务与并发闸门测试。"""

    def test_add_task_registers_and_emits(
        self,
        manager: DownloadManager,
        recorder: SignalRecorder,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """新增任务后应注册到表并发出 ``task_added``。"""
        task_id = manager.add_task(sample_info, sample_format)

        assert manager.task(task_id) is not None
        assert len(recorder.task_added) == 1
        assert recorder.task_added[0].task_id == task_id
        assert launched == [task_id]

    def test_add_task_uses_config_defaults(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
        download_dir: str,
    ) -> None:
        """任务的输出目录与重试上限来自配置。"""
        task = manager.task(manager.add_task(sample_info, sample_format, "mkv"))
        assert task is not None
        assert task.output_dir == download_dir
        assert task.max_retries == 2
        assert task.ext == "mkv"

    def test_concurrency_gate(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """并发上限为 2 时，5 个任务只应拉起 2 个。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(5)]

        assert len(launched) == 2
        assert launched == ids[:2]
        assert manager.global_stats()["queued"] == 3

    def test_slot_released_on_completion(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """任务完成后应立即拉起下一个排队任务。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(4)]
        assert len(launched) == 2

        manager._on_task_finished(ids[0], "C:/tmp/a.mp4")
        assert len(launched) == 3
        assert launched[2] == ids[2]

    def test_apply_concurrency_launches_more(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """调高并发上限后立即重新调度。"""
        for i in range(5):
            _add(manager, sample_info, sample_format, f"视频{i}")
        assert len(launched) == 2

        manager.config.set_max_concurrent(4)
        manager.apply_concurrency()
        assert len(launched) == 4

    def test_all_tasks_keeps_order(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """``all_tasks`` 按加入顺序返回。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        assert [task.task_id for task in manager.all_tasks()] == ids


# ======================================================================
# 暂停 / 恢复 / 取消 / 移除
# ======================================================================
class TestTaskCommands:
    """任务控制命令测试。"""

    def test_pause_queued_task(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """排队中的任务可直接置为已暂停。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        queued_id = ids[2]

        manager.pause_task(queued_id)
        task = manager.task(queued_id)
        assert task is not None and task.status is TaskStatus.PAUSED

    def test_resume_paused_task(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """恢复后重新入队并在有名额时被拉起。"""
        task_id = _add(manager, sample_info, sample_format, "单个视频")
        manager._on_task_status_changed(task_id, TaskStatus.PAUSED.value)
        assert (manager.task(task_id) or {}).status is TaskStatus.PAUSED  # type: ignore[union-attr]

        launched.clear()
        manager.resume_task(task_id)
        assert launched == [task_id]

    def test_resume_ignores_non_paused(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """非暂停态调用 resume 无副作用。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        launched.clear()
        manager.resume_task(task_id)
        assert launched == []

    def test_cancel_queued_task(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """取消排队任务直接置为已取消。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        manager.cancel_task(ids[2])

        task = manager.task(ids[2])
        assert task is not None
        assert task.status is TaskStatus.CANCELED
        assert task.finished_at is not None

    def test_cancel_terminal_task_is_noop(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """终态任务再取消无效果。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        manager._on_task_finished(task_id, "C:/tmp/a.mp4")
        manager.cancel_task(task_id)
        assert (manager.task(task_id) or {}).status is TaskStatus.COMPLETED  # type: ignore[union-attr]

    def test_remove_task(
        self,
        manager: DownloadManager,
        recorder: SignalRecorder,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """移除任务并发出 ``task_removed``。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        assert manager.remove_task(task_id) is True
        assert manager.task(task_id) is None
        assert recorder.task_removed == [task_id]
        assert manager.remove_task(task_id) is False

    def test_retry_task_resets_counter(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """手动重试会清零重试计数并重新入队。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        manager._on_task_failed(task_id, "网络连接超时，请检查网络或代理设置")
        task = manager.task(task_id)
        assert task is not None
        task.retry_count = 9
        task.status = TaskStatus.FAILED

        launched.clear()
        manager.retry_task(task_id)
        assert task.retry_count == 0
        assert launched == [task_id]

    def test_clear_finished(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """清除所有终态任务。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        manager._on_task_finished(ids[0], "C:/tmp/a.mp4")
        manager._on_task_failed(ids[1], "该视频为私享视频，无法下载")

        assert manager.clear_finished() == 2
        assert manager.task(ids[0]) is None
        assert manager.task(ids[1]) is None
        assert manager.task(ids[2]) is not None

    def test_open_folder_unknown_task(self, manager: DownloadManager) -> None:
        """未知任务返回 False。"""
        assert manager.open_folder("nope") is False


# ======================================================================
# Worker 回调
# ======================================================================
class TestWorkerCallbacks:
    """Worker 信号回调测试。"""

    def test_progress_updates_task(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """进度回调写回任务并累计体积。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        payload = ProgressInfo(percent=42.0, speed=2048.0, downloaded=420, total=1000).to_dict()

        manager._on_task_progress(task_id, payload)

        task = manager.task(task_id)
        assert task is not None
        assert task.progress.percent == 42.0
        assert task.filesize == 1000

    def test_progress_unknown_task_is_safe(self, manager: DownloadManager) -> None:
        """未知任务的进度包被忽略。"""
        manager._on_task_progress("nope", ProgressInfo().to_dict())

    def test_finished_writes_history_and_notifies(
        self,
        manager: DownloadManager,
        recorder: SignalRecorder,
        launched: list[str],
        history: HistoryStore,
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """完成回调：置终态 + 落历史 + 发通知。"""
        task_id = _add(manager, sample_info, sample_format, "完成的视频")
        manager._on_task_finished(task_id, "C:/tmp/done.mp4")

        task = manager.task(task_id)
        assert task is not None
        assert task.status is TaskStatus.COMPLETED
        assert task.filepath == "C:/tmp/done.mp4"
        assert task.progress.percent == 100.0

        assert history.count() == 1
        assert history.list_all()[0]["title"] == "完成的视频"
        assert recorder.notify and recorder.notify[-1][0] == "下载完成"

    def test_status_changed_paused_releases_slot(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """暂停会释放并发名额，拉起下一个排队任务。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        assert len(launched) == 2

        manager._on_task_status_changed(ids[0], TaskStatus.PAUSED.value)
        assert len(launched) == 3

    def test_status_changed_merging_keeps_slot(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """合并中仍占用并发名额。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(3)]
        manager._on_task_status_changed(ids[0], TaskStatus.MERGING.value)

        assert len(launched) == 2
        assert manager.global_stats()["active"] == 2


# ======================================================================
# 重试策略
# ======================================================================
class TestRetryPolicy:
    """失败重试策略测试。"""

    def test_retryable_error_increments_counter(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """可重试错误：计数 +1 并回到排队态（等待退避）。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        manager._on_task_failed(task_id, "网络连接超时，请检查网络或代理设置")

        task = manager.task(task_id)
        assert task is not None
        assert task.retry_count == 1
        assert task.status is TaskStatus.QUEUED
        assert task.error_message == "网络连接超时，请检查网络或代理设置"

    def test_non_retryable_error_fails_immediately(
        self,
        manager: DownloadManager,
        recorder: SignalRecorder,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """不可重试错误直接置为失败并通知。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        manager._on_task_failed(task_id, "该平台使用 DRM 加密保护，本软件不支持下载")

        task = manager.task(task_id)
        assert task is not None
        assert task.status is TaskStatus.FAILED
        assert task.retry_count == 0
        assert recorder.notify and recorder.notify[-1][0] == "下载失败"

    def test_exhausted_retries_fail(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """超过 ``max_retries`` 后置为失败（配置为 2 次）。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        message = "网络连接超时，请检查网络或代理设置"

        manager._on_task_failed(task_id, message)  # retry 1
        manager._on_task_failed(task_id, message)  # retry 2
        manager._on_task_failed(task_id, message)  # 超限

        task = manager.task(task_id)
        assert task is not None
        assert task.retry_count == 2
        assert task.status is TaskStatus.FAILED

    def test_requeue_after_backoff(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """退避结束后重新入队会被调度。"""
        task_id = _add(manager, sample_info, sample_format, "视频")
        manager._on_task_failed(task_id, "网络连接超时，请检查网络或代理设置")

        launched.clear()
        manager._requeue_after_backoff(task_id)
        assert launched == [task_id]

    def test_requeue_ignores_removed_task(self, manager: DownloadManager, launched: list[str]) -> None:
        """任务已被移除时退避回调静默返回。"""
        manager._requeue_after_backoff("nope")
        assert launched == []


# ======================================================================
# 解析
# ======================================================================
class TestParse:
    """解析流程测试。"""

    def test_empty_url_emits_error(self, manager: DownloadManager, recorder: SignalRecorder) -> None:
        """空链接立即报错。"""
        assert manager.parse("  ") is False
        assert recorder.parse_error == ["请输入视频链接"]

    def test_concurrent_parse_rejected(self, manager: DownloadManager, recorder: SignalRecorder) -> None:
        """同一时刻只允许一个解析任务。"""
        manager._parsing = True
        assert manager.parse("https://example.com/x") is False
        assert recorder.parse_error == ["正在解析中，请稍候"]

    def test_parse_finished_emits_ready(
        self, manager: DownloadManager, recorder: SignalRecorder, sample_info: MediaInfo
    ) -> None:
        """解析成功回调透传 ``MediaInfo``。"""
        manager._parsing = True
        manager._on_parse_finished(sample_info)

        assert manager.is_parsing is False
        assert recorder.parse_ready == [sample_info]

    def test_parse_finished_with_bad_payload(self, manager: DownloadManager, recorder: SignalRecorder) -> None:
        """非法载荷转为错误信号。"""
        manager._on_parse_finished("not a MediaInfo")
        assert recorder.parse_error == ["解析结果格式异常"]

    def test_parse_failed_emits_error(self, manager: DownloadManager, recorder: SignalRecorder) -> None:
        """解析失败回调透传中文消息。"""
        manager._parsing = True
        manager._on_parse_failed("https://example.com/x", "视频不存在、已删除或在当前地区不可用")

        assert manager.is_parsing is False
        assert recorder.parse_error == ["视频不存在、已删除或在当前地区不可用"]


# ======================================================================
# 统计与关闭
# ======================================================================
class TestStatsAndShutdown:
    """全局统计与关闭流程测试。"""

    def test_global_stats(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """各状态计数与总速度汇总。"""
        ids = [_add(manager, sample_info, sample_format, f"视频{i}") for i in range(4)]
        manager._on_task_progress(ids[0], ProgressInfo(percent=10.0, speed=1000.0).to_dict())
        manager._on_task_progress(ids[1], ProgressInfo(percent=20.0, speed=2000.0).to_dict())
        manager._on_task_failed(ids[3], "该视频为私享视频，无法下载")

        stats = manager.global_stats()
        assert stats["total"] == 4
        assert stats["active"] == 2
        assert stats["failed"] == 1
        assert stats["total_speed"] == 3000.0

    def test_has_active_tasks(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """活跃任务判定。"""
        assert manager.has_active_tasks() is False
        task_id = _add(manager, sample_info, sample_format, "视频")
        assert manager.has_active_tasks() is True

        manager._on_task_finished(task_id, "C:/tmp/a.mp4")
        assert manager.has_active_tasks() is False

    def test_stats_emitted_on_add(
        self,
        manager: DownloadManager,
        recorder: SignalRecorder,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """新增任务会触发一次统计刷新。"""
        _add(manager, sample_info, sample_format, "视频")
        assert recorder.stats and recorder.stats[-1]["total"] == 1

    def test_shutdown_is_idempotent(self, manager: DownloadManager) -> None:
        """重复关闭不报错。"""
        manager.shutdown()
        manager.shutdown()

    def test_no_scheduling_after_shutdown(
        self,
        manager: DownloadManager,
        launched: list[str],
        sample_info: MediaInfo,
        sample_format: MediaFormat,
    ) -> None:
        """关闭后不再拉起新任务。"""
        manager.shutdown()
        launched.clear()
        _add(manager, sample_info, sample_format, "视频")
        assert launched == []
