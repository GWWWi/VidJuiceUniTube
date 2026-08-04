"""日志配置。

控制台 + 滚动文件（5MB × 3）双通道输出，日志格式包含线程名，
便于排查跨线程问题。全项目禁止使用 ``print``。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Final

from src.utils.paths import log_file_path

#: 日志格式：时间 | 级别 | 线程 | 模块:行号 | 消息
_LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)-7s | %(threadName)-16s | %(name)s:%(lineno)d | %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES: Final[int] = 5 * 1024 * 1024
_BACKUP_COUNT: Final[int] = 3

_configured: bool = False


def setup_logging(level: int = logging.INFO, to_file: bool = True) -> logging.Logger:
    """初始化根日志器（幂等，重复调用不会重复添加 handler）。

    Args:
        level: 日志级别，默认 ``INFO``。
        to_file: 是否写入滚动日志文件。

    Returns:
        配置完成的根日志器。
    """
    global _configured

    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 控制台通道（打包为 windowed 模式时 stderr 可能为 None）
    stream = sys.stderr or sys.stdout
    if stream is not None:
        console = logging.StreamHandler(stream)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    # 文件通道
    if to_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file_path(),
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:  # 磁盘不可写时不能因此让程序起不来
            root.warning("日志文件初始化失败，仅使用控制台输出：%s", exc)

    # 抑制第三方库的噪声日志
    for noisy in ("urllib3", "requests", "PIL", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.debug("日志系统初始化完成，级别=%s", logging.getLevelName(level))
    return root


def get_logger(name: str) -> logging.Logger:
    """获取指定名字的日志器（等价于 ``logging.getLogger``，便于统一入口）。"""
    return logging.getLogger(name)


def install_excepthook() -> None:
    """安装全局未捕获异常钩子，把主线程崩溃写入日志。"""
    logger = logging.getLogger("uncaught")

    def _hook(exc_type, exc_value, exc_tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook


__all__ = ["get_logger", "install_excepthook", "setup_logging"]
