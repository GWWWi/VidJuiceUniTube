"""格式化工具。

全项目**唯一**的体积/速度/时长/分辨率格式化入口，
禁止在 UI 或其他模块中自行编写 ``/1024/1024`` 之类的换算。
"""

from __future__ import annotations

import time
from typing import Any

#: 体积单位阶梯
_SIZE_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB", "PB")
#: 分辨率高度 → 友好标签
_RESOLUTION_LABELS: tuple[tuple[int, str], ...] = (
    (4320, "8K"),
    (2160, "4K"),
    (1440, "2K"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
    (240, "240p"),
    (144, "144p"),
)


def human_size(num_bytes: float | int | None, precision: int = 1) -> str:
    """把字节数格式化为可读体积字符串。

    Args:
        num_bytes: 字节数，``None`` 或负数视为未知。
        precision: 小数位数。

    Returns:
        如 ``"128.4 MB"``；未知返回 ``"--"``。
    """
    if num_bytes is None:
        return "--"
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "--"
    if value < 0:
        return "--"
    if value < 1:
        return "0 B"

    unit_index = 0
    while value >= 1024.0 and unit_index < len(_SIZE_UNITS) - 1:
        value /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {_SIZE_UNITS[0]}"
    return f"{value:.{precision}f} {_SIZE_UNITS[unit_index]}"


def human_speed(bytes_per_sec: float | int | None, precision: int = 1) -> str:
    """把速度（字节/秒）格式化为可读字符串。

    Args:
        bytes_per_sec: 速度，``None`` 或 ``<=0`` 视为未知。
        precision: 小数位数。

    Returns:
        如 ``"1.2 MB/s"``；未知返回 ``"--"``。
    """
    if bytes_per_sec is None:
        return "--"
    try:
        value = float(bytes_per_sec)
    except (TypeError, ValueError):
        return "--"
    if value <= 0:
        return "--"
    return f"{human_size(value, precision)}/s"


def human_eta(seconds: float | int | None) -> str:
    """把剩余秒数格式化为 ``mm:ss`` / ``hh:mm:ss``。

    Args:
        seconds: 剩余秒数，``None`` 或负数视为未知。

    Returns:
        如 ``"03:21"`` / ``"01:12:05"``；未知返回 ``"--"``。
    """
    if seconds is None:
        return "--"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "--"
    if total < 0:
        return "--"

    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_duration(seconds: float | int | None) -> str:
    """把视频时长格式化为可读字符串（语义与 ``human_eta`` 相同，命名区分用途）。"""
    return human_eta(seconds)


def human_time(timestamp: float | int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """把 Unix 时间戳格式化为本地时间字符串。

    Args:
        timestamp: Unix 时间戳（秒）。
        fmt: ``time.strftime`` 格式串。

    Returns:
        格式化后的本地时间；无效输入返回 ``"--"``。
    """
    if not timestamp:
        return "--"
    try:
        return time.strftime(fmt, time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return "--"


def resolution_label(height: int | None) -> str:
    """把视频高度转换为友好清晰度标签。

    Args:
        height: 视频高度像素值。

    Returns:
        如 ``"1080p"`` / ``"4K"``；未知返回 ``"未知"``。
    """
    if not height:
        return "未知"
    try:
        value = int(height)
    except (TypeError, ValueError):
        return "未知"
    if value <= 0:
        return "未知"

    for threshold, label in _RESOLUTION_LABELS:
        if value >= threshold:
            return label
    return f"{value}p"


def quality_sort_key(fmt: Any) -> tuple[int, int, int, int]:
    """生成用于画质**降序**排序的键。

    优先级：高度 > 帧率 > 是否含音轨（合流优先）> 体积。

    Args:
        fmt: ``MediaFormat`` 实例或含同名属性的对象。

    Returns:
        可比较的四元组，配合 ``reverse=True`` 使用。
    """
    height = int(getattr(fmt, "height", 0) or 0)
    fps = int(getattr(fmt, "fps", 0) or 0)
    acodec = str(getattr(fmt, "acodec", "") or "")
    has_audio = 0 if acodec in ("", "none") else 1
    filesize = int(getattr(fmt, "filesize", 0) or 0)
    return (height, fps, has_audio, filesize)


def percent_text(percent: float | None) -> str:
    """把百分比数值格式化为展示文本。

    Args:
        percent: 0.0~100.0；负数或 ``None`` 表示未知。

    Returns:
        如 ``"45.6%"``；未知返回 ``"--"``。
    """
    if percent is None:
        return "--"
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return "--"
    if value < 0:
        return "--"
    return f"{value:.1f}%"


def truncate(text: str, max_chars: int = 60, suffix: str = "…") -> str:
    """截断过长文本用于表格展示。

    Args:
        text: 原始文本。
        max_chars: 最大字符数。
        suffix: 截断后缀。

    Returns:
        截断后的文本。
    """
    if not text:
        return ""
    value = str(text)
    if len(value) <= max_chars:
        return value
    return value[: max(1, max_chars - len(suffix))] + suffix


__all__ = [
    "human_duration",
    "human_eta",
    "human_size",
    "human_speed",
    "human_time",
    "percent_text",
    "quality_sort_key",
    "resolution_label",
    "truncate",
]
