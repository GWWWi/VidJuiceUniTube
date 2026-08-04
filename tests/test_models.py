"""``src.core.models`` 与 ``src.core.format_utils`` 单元测试。"""

from __future__ import annotations

from src.constants import BEST_FORMAT_ID, BEST_FORMAT_LABEL, PHASE_DOWNLOADING
from src.core.format_utils import (
    human_eta,
    human_size,
    human_speed,
    percent_text,
    quality_sort_key,
    resolution_label,
    truncate,
)
from src.core.models import (
    DownloadTask,
    MediaFormat,
    MediaInfo,
    ProgressInfo,
    TaskStatus,
)


class TestFormatUtils:
    """格式化工具测试。"""

    def test_human_size(self) -> None:
        """体积格式化覆盖各量级与非法输入。"""
        assert human_size(None) == "--"
        assert human_size(-1) == "--"
        assert human_size(0) == "0 B"
        assert human_size(512) == "512 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(1024 * 1024 * 128 + 419430) == "128.4 MB"

    def test_human_speed(self) -> None:
        """速度格式化。"""
        assert human_speed(None) == "--"
        assert human_speed(0) == "--"
        assert human_speed(1024 * 1024) == "1.0 MB/s"

    def test_human_eta(self) -> None:
        """剩余时间格式化。"""
        assert human_eta(None) == "--"
        assert human_eta(-5) == "--"
        assert human_eta(65) == "01:05"
        assert human_eta(3725) == "01:02:05"

    def test_resolution_label(self) -> None:
        """分辨率标签映射。"""
        assert resolution_label(None) == "未知"
        assert resolution_label(0) == "未知"
        assert resolution_label(1080) == "1080p"
        assert resolution_label(2160) == "4K"
        assert resolution_label(4320) == "8K"
        assert resolution_label(100) == "100p"

    def test_percent_text(self) -> None:
        """百分比文本：未知值降级为 ``--``，有效值保留一位小数。"""
        assert percent_text(None) == "--"
        assert percent_text(-1) == "--"
        assert percent_text(0) == "0.0%"
        assert percent_text(100) == "100.0%"
        assert percent_text(45.5) == "45.5%"
        assert percent_text(45.54) == "45.5%"
        assert percent_text(45.56) == "45.6%"

    def test_percent_text_binary_float_midpoint(self) -> None:
        """字面量 ``45.55`` 在 IEEE-754 下实际是 45.54999...，因此正确结果是 ``45.5%``。

        这不是舍入 Bug：``45.55`` 无法用二进制浮点精确表示，其真实存储值
        小于十进制中点，故向下舍入到 ``45.5`` 才是数学上正确的行为。
        请勿为了迎合"直觉上的 45.6"而改用 ``Decimal(str(x))`` half-up ——
        进度百分比属于展示用途，0.1% 的差异对用户无感知，不值得引入额外开销。
        """
        assert percent_text(45.55) == "45.5%"

    def test_truncate(self) -> None:
        """文本截断。"""
        assert truncate("", 10) == ""
        assert truncate("abc", 10) == "abc"
        assert truncate("abcdefghij", 5) == "abcd…"

    def test_quality_sort_key_desc(self, sample_format: MediaFormat) -> None:
        """排序键：高度优先，降序排序后高清在前。"""
        low = MediaFormat(format_id="18", ext="mp4", height=360, fps=30, vcodec="avc1", acodec="mp4a")
        formats = [low, sample_format]
        formats.sort(key=quality_sort_key, reverse=True)
        assert formats[0] is sample_format


class TestMediaFormat:
    """``MediaFormat`` 测试。"""

    def test_from_ydl_dict_video_only(self) -> None:
        """纯视频流应标记为需要合并。"""
        fmt = MediaFormat.from_ydl_dict(
            {
                "format_id": "137",
                "ext": "mp4",
                "height": 1080,
                "fps": 30,
                "filesize": 1000,
                "vcodec": "avc1.640028",
                "acodec": "none",
            }
        )
        assert fmt.needs_merge is True
        assert fmt.is_video_only is True
        assert fmt.is_audio_only is False
        assert "1080p" in fmt.label
        assert "需合并" in fmt.label

    def test_from_ydl_dict_audio_only(self) -> None:
        """纯音频流识别。"""
        fmt = MediaFormat.from_ydl_dict(
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}
        )
        assert fmt.is_audio_only is True
        assert fmt.needs_merge is False
        assert "仅音频" in fmt.label

    def test_from_ydl_dict_tolerates_dirty_values(self) -> None:
        """脏数据（字符串 / None）不应抛异常。"""
        fmt = MediaFormat.from_ydl_dict(
            {"format_id": "x", "height": "bad", "fps": "bad", "filesize": "bad"}
        )
        assert fmt.height is None
        assert fmt.fps is None
        assert fmt.filesize is None

    def test_make_best(self) -> None:
        """伪「最佳画质」项。"""
        fmt = MediaFormat.make_best()
        assert fmt.is_best is True
        assert fmt.format_id == BEST_FORMAT_ID
        assert fmt.label == BEST_FORMAT_LABEL

    def test_dict_roundtrip(self, sample_format: MediaFormat) -> None:
        """``to_dict`` / ``from_dict`` 往返一致。"""
        restored = MediaFormat.from_dict(sample_format.to_dict())
        assert restored == sample_format


class TestMediaInfo:
    """``MediaInfo`` 测试。"""

    def test_best_format_prefers_pseudo_best(self, sample_info: MediaInfo) -> None:
        """默认选中伪「最佳画质」。"""
        assert sample_info.best_format().is_best is True

    def test_best_format_never_none(self) -> None:
        """空格式列表也要返回可用对象。"""
        assert MediaInfo().best_format().is_best is True

    def test_filter_video_formats_excludes_audio(self, sample_info: MediaInfo) -> None:
        """过滤掉纯音频。"""
        formats = sample_info.filter_video_formats()
        assert all(not fmt.is_audio_only for fmt in formats)
        assert len(formats) == 2

    def test_available_containers(self, sample_info: MediaInfo) -> None:
        """容器列表去重且不含伪最佳项。"""
        assert sample_info.available_containers() == ["mp4", "m4a"]

    def test_dict_roundtrip(self, sample_info: MediaInfo) -> None:
        """序列化往返。"""
        restored = MediaInfo.from_dict(sample_info.to_dict())
        assert restored.title == sample_info.title
        assert len(restored.formats) == len(sample_info.formats)


class TestProgressInfo:
    """``ProgressInfo`` 测试。"""

    def test_to_dict_keys(self) -> None:
        """进度 dict 的键必须与全链路约定一致。"""
        keys = set(ProgressInfo().to_dict().keys())
        assert keys == {"percent", "speed", "downloaded", "total", "eta", "phase"}

    def test_from_hook_normal(self) -> None:
        """常规进度包。"""
        progress = ProgressInfo.from_hook(
            {
                "status": "downloading",
                "downloaded_bytes": 500,
                "total_bytes": 1000,
                "speed": 128.0,
                "eta": 4,
            }
        )
        assert progress.percent == 50.0
        assert progress.downloaded == 500
        assert progress.total == 1000
        assert progress.speed == 128.0
        assert progress.eta == 4.0
        assert progress.phase == PHASE_DOWNLOADING

    def test_from_hook_unknown_total(self) -> None:
        """总量未知时 percent 为 -1（UI 显示忙碌样式）。"""
        progress = ProgressInfo.from_hook({"status": "downloading", "downloaded_bytes": 500})
        assert progress.percent == -1.0

    def test_from_hook_estimate_total(self) -> None:
        """回落到 ``total_bytes_estimate``。"""
        progress = ProgressInfo.from_hook(
            {"status": "downloading", "downloaded_bytes": 250, "total_bytes_estimate": 1000}
        )
        assert progress.percent == 25.0

    def test_from_hook_finished(self) -> None:
        """finished 状态直接置 100%。"""
        progress = ProgressInfo.from_hook({"status": "finished", "downloaded_bytes": 800})
        assert progress.percent == 100.0
        assert progress.total == 800

    def test_from_hook_negative_speed_clamped(self) -> None:
        """速度不为负。"""
        progress = ProgressInfo.from_hook({"status": "downloading", "speed": -10})
        assert progress.speed == 0.0

    def test_dict_roundtrip(self) -> None:
        """序列化往返。"""
        origin = ProgressInfo(percent=33.3, speed=1024.0, downloaded=100, total=300, eta=2.0)
        assert ProgressInfo.from_dict(origin.to_dict()) == origin


class TestTaskStatus:
    """``TaskStatus`` 测试。"""

    def test_from_value(self) -> None:
        """字符串安全转换。"""
        assert TaskStatus.from_value("downloading") is TaskStatus.DOWNLOADING
        assert TaskStatus.from_value(TaskStatus.FAILED) is TaskStatus.FAILED
        assert TaskStatus.from_value("不存在的状态") is TaskStatus.QUEUED
        assert TaskStatus.from_value(None) is TaskStatus.QUEUED


class TestDownloadTask:
    """``DownloadTask`` 测试。"""

    def test_from_media(self, sample_task: DownloadTask) -> None:
        """由解析结果构造任务。"""
        assert sample_task.status is TaskStatus.QUEUED
        assert sample_task.ext == "mp4"
        assert sample_task.format_id == "137"
        assert sample_task.needs_merge is True
        assert len(sample_task.task_id) == 12

    def test_from_media_container_overrides_ext(self, sample_info: MediaInfo, sample_format: MediaFormat) -> None:
        """显式容器优先于格式自带扩展名。"""
        task = DownloadTask.from_media(
            url=sample_info.url,
            title=sample_info.title,
            fmt=sample_format,
            output_dir="C:/tmp",
            container="MKV",
        )
        assert task.ext == "mkv"

    def test_task_ids_unique(self, sample_info: MediaInfo, sample_format: MediaFormat) -> None:
        """任务 id 唯一。"""
        ids = {
            DownloadTask.from_media(sample_info.url, "t", sample_format, "C:/tmp").task_id
            for _ in range(50)
        }
        assert len(ids) == 50

    def test_state_predicates(self, sample_task: DownloadTask) -> None:
        """状态判定函数。"""
        sample_task.status = TaskStatus.DOWNLOADING
        assert sample_task.is_active() is True
        assert sample_task.can_pause() is True
        assert sample_task.is_terminal() is False

        sample_task.status = TaskStatus.PAUSED
        assert sample_task.can_resume() is True
        assert sample_task.is_active() is False

        sample_task.status = TaskStatus.FAILED
        assert sample_task.can_retry() is True
        assert sample_task.is_terminal() is True

        sample_task.status = TaskStatus.MERGING
        assert sample_task.is_active() is True

    def test_reset_for_retry_keeps_retry_count(self, sample_task: DownloadTask) -> None:
        """重置运行态但保留重试计数。"""
        sample_task.status = TaskStatus.FAILED
        sample_task.error_message = "boom"
        sample_task.retry_count = 2
        sample_task.progress = ProgressInfo(percent=42.0)

        sample_task.reset_for_retry()

        assert sample_task.status is TaskStatus.QUEUED
        assert sample_task.error_message is None
        assert sample_task.progress.percent == 0.0
        assert sample_task.retry_count == 2

    def test_dict_roundtrip(self, sample_task: DownloadTask) -> None:
        """序列化往返（含 task_id 与状态）。"""
        sample_task.status = TaskStatus.COMPLETED
        sample_task.filepath = "C:/tmp/a.mp4"
        restored = DownloadTask.from_dict(sample_task.to_dict())
        assert restored.task_id == sample_task.task_id
        assert restored.status is TaskStatus.COMPLETED
        assert restored.filepath == "C:/tmp/a.mp4"
