"""核心数据模型。

全部使用 ``@dataclass``，纯 Python 实现，不依赖 Qt，可直接单测。
所有模型均支持 ``to_dict()`` / ``from_dict()`` JSON 往返，
用于历史记录持久化与未来的任务队列恢复。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.constants import (
    BEST_FORMAT_ID,
    BEST_FORMAT_LABEL,
    DEFAULT_MAX_RETRIES,
    PHASE_DOWNLOADING,
)
from src.core.format_utils import human_size, resolution_label


class TaskStatus(str, Enum):
    """下载任务状态枚举（值为英文小写，UI 展示走 ``STATUS_TEXT_MAP``）。"""

    QUEUED = "queued"
    PARSING = "parsing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @classmethod
    def from_value(cls, value: str | TaskStatus | None) -> "TaskStatus":
        """从字符串安全构造枚举，无法识别时回落到 ``QUEUED``。"""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except (ValueError, TypeError):
            return cls.QUEUED


#: 处于「运行中」的状态集合（占用并发名额）
RUNNING_STATUSES: frozenset[TaskStatus] = frozenset({TaskStatus.DOWNLOADING, TaskStatus.MERGING})
#: 处于「终态」的状态集合（不再变化）
TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
)


@dataclass
class MediaFormat:
    """单个可下载格式。

    Attributes:
        format_id: yt-dlp 的格式标识。
        ext: 容器扩展名（mp4 / webm / m4a ...）。
        resolution: 分辨率描述（如 ``1920x1080``）。
        height: 视频高度，音频流为 ``None``。
        fps: 帧率。
        filesize: 预估体积（字节）。
        vcodec: 视频编码，``none`` 表示纯音频。
        acodec: 音频编码，``none`` 表示纯视频（需合并）。
        needs_merge: 是否需要与音频流合并。
        label: 面向用户的展示文案。
    """

    format_id: str = ""
    ext: str = "mp4"
    resolution: str = ""
    height: int | None = None
    fps: int | None = None
    filesize: float | None = None
    vcodec: str = "none"
    acodec: str = "none"
    needs_merge: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        """补齐派生字段（resolution / label）。"""
        if not self.resolution:
            self.resolution = resolution_label(self.height)
        # 纯视频流（有画面无音轨）必然需要合并音频，兜底推导
        if self.is_video_only:
            self.needs_merge = True
        if not self.label:
            self.label = self.display_text()

    @staticmethod
    def from_ydl_dict(d: dict[str, Any]) -> "MediaFormat":
        """从 yt-dlp 原始格式 dict 构造 ``MediaFormat``。

        Args:
            d: yt-dlp ``info["formats"]`` 中的单项。

        Returns:
            归一化后的 ``MediaFormat``。
        """
        vcodec = str(d.get("vcodec") or "none")
        acodec = str(d.get("acodec") or "none")
        height = d.get("height")
        try:
            height_int: int | None = int(height) if height else None
        except (TypeError, ValueError):
            height_int = None

        fps_raw = d.get("fps")
        try:
            fps_int: int | None = int(round(float(fps_raw))) if fps_raw else None
        except (TypeError, ValueError):
            fps_int = None

        filesize_raw = d.get("filesize") or d.get("filesize_approx")
        try:
            filesize_val: float | None = float(filesize_raw) if filesize_raw else None
        except (TypeError, ValueError):
            filesize_val = None

        # 纯视频流（无音轨）需要额外合并音频
        needs_merge = acodec == "none" and vcodec != "none"

        resolution = str(d.get("resolution") or "") or resolution_label(height_int)

        fmt = MediaFormat(
            format_id=str(d.get("format_id") or ""),
            ext=str(d.get("ext") or "mp4"),
            resolution=resolution,
            height=height_int,
            fps=fps_int,
            filesize=filesize_val,
            vcodec=vcodec,
            acodec=acodec,
            needs_merge=needs_merge,
            label="",
        )
        fmt.label = fmt.display_text()
        return fmt

    @staticmethod
    def make_best() -> "MediaFormat":
        """构造伪「最佳画质（自动）」格式，作为解析结果的默认置顶项。"""
        return MediaFormat(
            format_id=BEST_FORMAT_ID,
            ext="mp4",
            resolution="自动",
            height=None,
            fps=None,
            filesize=None,
            vcodec="unknown",
            acodec="unknown",
            needs_merge=True,
            label=BEST_FORMAT_LABEL,
        )

    @property
    def is_best(self) -> bool:
        """是否为伪「最佳画质」项。"""
        return self.format_id == BEST_FORMAT_ID

    @property
    def is_audio_only(self) -> bool:
        """是否为纯音频流。"""
        return self.vcodec == "none" and self.acodec != "none"

    @property
    def is_video_only(self) -> bool:
        """是否为纯视频流（无音轨）。"""
        return self.vcodec != "none" and self.acodec == "none"

    def display_text(self) -> str:
        """生成展示文案，如 ``1080p60 · mp4 · 128.4 MB``。"""
        if self.format_id == BEST_FORMAT_ID:
            return BEST_FORMAT_LABEL

        parts: list[str] = []
        if self.is_audio_only:
            parts.append("仅音频")
        else:
            quality = resolution_label(self.height)
            if self.fps and self.fps > 30:
                quality = f"{quality}{self.fps}"
            parts.append(quality)

        parts.append(self.ext)

        if self.filesize:
            parts.append(human_size(self.filesize))
        if self.is_video_only:
            parts.append("需合并")

        return " · ".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的 dict。"""
        return {
            "format_id": self.format_id,
            "ext": self.ext,
            "resolution": self.resolution,
            "height": self.height,
            "fps": self.fps,
            "filesize": self.filesize,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "needs_merge": self.needs_merge,
            "label": self.label,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MediaFormat":
        """从 ``to_dict()`` 的结果还原。"""
        data = dict(d or {})
        return MediaFormat(
            format_id=str(data.get("format_id") or ""),
            ext=str(data.get("ext") or "mp4"),
            resolution=str(data.get("resolution") or ""),
            height=data.get("height"),
            fps=data.get("fps"),
            filesize=data.get("filesize"),
            vcodec=str(data.get("vcodec") or "none"),
            acodec=str(data.get("acodec") or "none"),
            needs_merge=bool(data.get("needs_merge", False)),
            label=str(data.get("label") or ""),
        )


@dataclass
class MediaInfo:
    """一次解析得到的媒体信息。"""

    url: str = ""
    title: str = ""
    uploader: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    extractor: str = ""
    formats: list[MediaFormat] = field(default_factory=list)

    def best_format(self) -> MediaFormat:
        """返回默认应选中的格式。

        优先返回伪「最佳画质」项；不存在时返回第一个格式；
        列表为空时返回一个新建的「最佳画质」项，保证调用方永不拿到 ``None``。
        """
        for fmt in self.formats:
            if fmt.is_best:
                return fmt
        if self.formats:
            return self.formats[0]
        return MediaFormat.make_best()

    def filter_video_formats(self) -> list[MediaFormat]:
        """返回包含视频画面的格式（含伪「最佳画质」项，排除纯音频）。"""
        return [fmt for fmt in self.formats if fmt.is_best or not fmt.is_audio_only]

    def filter_audio_formats(self) -> list[MediaFormat]:
        """返回纯音频格式列表。"""
        return [fmt for fmt in self.formats if fmt.is_audio_only]

    def available_containers(self) -> list[str]:
        """返回去重后的可选容器扩展名列表（保持出现顺序）。"""
        seen: list[str] = []
        for fmt in self.formats:
            if fmt.is_best:
                continue
            if fmt.ext and fmt.ext not in seen:
                seen.append(fmt.ext)
        return seen

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的 dict。"""
        return {
            "url": self.url,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "extractor": self.extractor,
            "formats": [fmt.to_dict() for fmt in self.formats],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MediaInfo":
        """从 ``to_dict()`` 的结果还原。"""
        data = dict(d or {})
        return MediaInfo(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            uploader=data.get("uploader"),
            duration=data.get("duration"),
            thumbnail=data.get("thumbnail"),
            extractor=str(data.get("extractor") or ""),
            formats=[MediaFormat.from_dict(item) for item in (data.get("formats") or [])],
        )


@dataclass
class ProgressInfo:
    """下载进度快照。

    ``to_dict()`` 的结构是全链路（Worker → Manager → UI）唯一的进度数据格式。
    """

    percent: float = 0.0
    speed: float = 0.0
    downloaded: int = 0
    total: int | None = None
    eta: float | None = None
    phase: str = PHASE_DOWNLOADING

    def to_dict(self) -> dict[str, Any]:
        """序列化为跨线程传输的进度 dict。"""
        return {
            "percent": float(self.percent),
            "speed": float(self.speed),
            "downloaded": int(self.downloaded),
            "total": self.total,
            "eta": self.eta,
            "phase": self.phase,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ProgressInfo":
        """从进度 dict 还原。"""
        data = dict(d or {})
        return ProgressInfo(
            percent=float(data.get("percent") or 0.0),
            speed=float(data.get("speed") or 0.0),
            downloaded=int(data.get("downloaded") or 0),
            total=data.get("total"),
            eta=data.get("eta"),
            phase=str(data.get("phase") or PHASE_DOWNLOADING),
        )

    @staticmethod
    def from_hook(d: dict[str, Any]) -> "ProgressInfo":
        """从 yt-dlp ``progress_hooks`` 回调 dict 构造进度快照。

        Args:
            d: yt-dlp 回调参数。

        Returns:
            归一化的 ``ProgressInfo``；``total`` 未知时 ``percent`` 为 ``-1.0``。
        """
        data = dict(d or {})

        def _to_int(value: Any) -> int | None:
            try:
                if value is None:
                    return None
                return int(float(value))
            except (TypeError, ValueError):
                return None

        def _to_float(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        downloaded = _to_int(data.get("downloaded_bytes")) or 0
        total = _to_int(data.get("total_bytes"))
        if total is None:
            total = _to_int(data.get("total_bytes_estimate"))

        status = str(data.get("status") or "")
        if status == "finished":
            percent = 100.0
            if total is None:
                total = downloaded or None
        elif total and total > 0:
            percent = max(0.0, min(100.0, downloaded * 100.0 / total))
        else:
            percent = -1.0  # 总量未知，UI 显示忙碌样式

        speed = _to_float(data.get("speed")) or 0.0
        eta = _to_float(data.get("eta"))

        return ProgressInfo(
            percent=percent,
            speed=max(0.0, speed),
            downloaded=downloaded,
            total=total,
            eta=eta,
            phase=PHASE_DOWNLOADING,
        )


@dataclass
class DownloadTask:
    """一个下载任务的完整状态。

    写操作只发生在主线程（``DownloadManager`` 槽函数内）；
    Worker 只读取 ``url`` / ``format_id`` / ``output_dir`` 等静态字段。
    """

    url: str = ""
    title: str = ""
    format_id: str = BEST_FORMAT_ID
    ext: str = "mp4"
    resolution: str = ""
    output_dir: str = ""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filepath: str | None = None
    status: TaskStatus = TaskStatus.QUEUED
    progress: ProgressInfo = field(default_factory=ProgressInfo)
    error_message: str | None = None
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    needs_merge: bool = False
    filesize: int | None = None

    @staticmethod
    def from_media(
        url: str,
        title: str,
        fmt: MediaFormat,
        output_dir: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        container: str | None = None,
    ) -> "DownloadTask":
        """由解析结果构造下载任务（对应类图中的 ``__init__(url, title, fmt, output_dir)``）。

        Args:
            url: 视频链接。
            title: 视频标题。
            fmt: 用户选中的格式。
            output_dir: 输出目录。
            max_retries: 最大自动重试次数。
            container: 用户显式指定的容器格式（优先于 ``fmt.ext``）。

        Returns:
            初始状态为 ``QUEUED`` 的任务。
        """
        ext = (container or fmt.ext or "mp4").lower()
        return DownloadTask(
            url=url,
            title=title or url,
            format_id=fmt.format_id or BEST_FORMAT_ID,
            ext=ext,
            resolution=fmt.label or fmt.resolution or resolution_label(fmt.height),
            output_dir=output_dir,
            status=TaskStatus.QUEUED,
            progress=ProgressInfo(),
            max_retries=int(max_retries),
            needs_merge=bool(fmt.needs_merge),
            filesize=int(fmt.filesize) if fmt.filesize else None,
        )

    def is_active(self) -> bool:
        """是否正在占用并发名额（下载中或合并中）。"""
        return self.status in RUNNING_STATUSES

    def is_terminal(self) -> bool:
        """是否已到终态（完成 / 失败 / 取消）。"""
        return self.status in TERMINAL_STATUSES

    def can_resume(self) -> bool:
        """是否可以恢复下载（已暂停）。"""
        return self.status == TaskStatus.PAUSED

    def can_retry(self) -> bool:
        """是否可以手动重试。"""
        return self.status in (TaskStatus.FAILED, TaskStatus.CANCELED)

    def can_pause(self) -> bool:
        """是否可以暂停。"""
        return self.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING)

    def display_name(self) -> str:
        """返回用于表格展示的名称。"""
        return self.title or self.url

    def reset_for_retry(self) -> None:
        """为重新入队重置运行态字段（保留 ``retry_count``）。"""
        self.status = TaskStatus.QUEUED
        self.error_message = None
        self.finished_at = None
        self.progress = ProgressInfo()

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的 dict。"""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "title": self.title,
            "format_id": self.format_id,
            "ext": self.ext,
            "resolution": self.resolution,
            "output_dir": self.output_dir,
            "filepath": self.filepath,
            "status": self.status.value,
            "progress": self.progress.to_dict(),
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "needs_merge": self.needs_merge,
            "filesize": self.filesize,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DownloadTask":
        """从 ``to_dict()`` 的结果还原（字段缺失时使用默认值）。"""
        data = dict(d or {})
        task = DownloadTask(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            format_id=str(data.get("format_id") or BEST_FORMAT_ID),
            ext=str(data.get("ext") or "mp4"),
            resolution=str(data.get("resolution") or ""),
            output_dir=str(data.get("output_dir") or ""),
            filepath=data.get("filepath"),
            status=TaskStatus.from_value(data.get("status")),
            progress=ProgressInfo.from_dict(data.get("progress") or {}),
            error_message=data.get("error_message"),
            retry_count=int(data.get("retry_count") or 0),
            max_retries=int(data.get("max_retries") or DEFAULT_MAX_RETRIES),
            created_at=float(data.get("created_at") or time.time()),
            finished_at=data.get("finished_at"),
            needs_merge=bool(data.get("needs_merge", False)),
            filesize=data.get("filesize"),
        )
        if data.get("task_id"):
            task.task_id = str(data["task_id"])
        return task


__all__ = [
    "RUNNING_STATUSES",
    "TERMINAL_STATUSES",
    "DownloadTask",
    "MediaFormat",
    "MediaInfo",
    "ProgressInfo",
    "TaskStatus",
]
