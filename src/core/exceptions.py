"""自定义异常体系。

所有面向用户的异常消息一律使用中文，可直接展示到 UI。
``DownloadPause`` / ``DownloadAbort`` 属于**控制流异常**（用于中断 yt-dlp
的阻塞下载循环），不是错误，调用方需单独分支处理。
"""

from __future__ import annotations


class AppError(Exception):
    """应用异常基类。

    Attributes:
        message: 面向用户的中文提示。
        detail: 供日志排查用的原始细节（可为空）。
    """

    def __init__(self, message: str = "发生未知错误", detail: str = "") -> None:
        super().__init__(message)
        self.message: str = message
        self.detail: str = detail

    def __str__(self) -> str:
        return self.message


class ParseError(AppError):
    """链接解析失败（不支持的站点、视频不存在、需要登录等）。"""

    def __init__(self, message: str = "解析失败", detail: str = "", url: str = "") -> None:
        super().__init__(message, detail)
        self.url: str = url


class DownloadError(AppError):
    """下载过程失败（网络中断、磁盘写入失败、服务端拒绝等）。"""

    def __init__(self, message: str = "下载失败", detail: str = "", task_id: str = "") -> None:
        super().__init__(message, detail)
        self.task_id: str = task_id


class DownloadPause(AppError):
    """控制流异常：用户请求暂停，用于中断 yt-dlp 下载循环。

    捕获后应保留 ``.part`` 文件以便断点续传。
    """

    def __init__(self, message: str = "下载已暂停", task_id: str = "") -> None:
        super().__init__(message)
        self.task_id: str = task_id


class DownloadAbort(AppError):
    """控制流异常：用户请求取消，用于中断 yt-dlp 下载循环。

    捕获后应清理 ``.part`` / ``.ytdl`` 临时文件。
    """

    def __init__(self, message: str = "下载已取消", task_id: str = "") -> None:
        super().__init__(message)
        self.task_id: str = task_id


class MergeError(AppError):
    """ffmpeg 音视频合并失败（二进制缺失、编码不兼容、超时等）。"""

    def __init__(self, message: str = "音视频合并失败", detail: str = "") -> None:
        super().__init__(message, detail)


class ConfigError(AppError):
    """配置读写失败。"""


class HistoryError(AppError):
    """历史记录数据库操作失败。"""


__all__ = [
    "AppError",
    "ConfigError",
    "DownloadAbort",
    "DownloadError",
    "DownloadPause",
    "HistoryError",
    "MergeError",
    "ParseError",
]
