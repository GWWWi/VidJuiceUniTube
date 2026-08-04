"""应用装配器（依赖注入）。

集中构建并串联所有核心对象，保证依赖方向单一、生命周期可控：

``ConfigStore`` → ``HistoryStore`` / ``YtDlpClient`` / ``FfmpegMerger`` → ``DownloadManager``
"""

from __future__ import annotations

import logging

from src.core.config_store import ConfigStore
from src.core.download_manager import DownloadManager
from src.core.ffmpeg_merger import FfmpegMerger
from src.core.history_store import HistoryStore
from src.core.ytdlp_client import YtDlpClient

logger = logging.getLogger(__name__)


def build_app(
    config_path: str | None = None,
    history_path: str | None = None,
) -> tuple[ConfigStore, DownloadManager]:
    """构建应用核心对象图。

    Args:
        config_path: 配置文件路径（测试可注入临时路径）。
        history_path: 历史数据库路径（测试可注入临时路径或 ``":memory:"``）。

    Returns:
        ``(config, manager)`` 二元组。``manager`` 内部持有 client / merger / history。
    """
    config = ConfigStore(config_path)
    config.load()

    history = HistoryStore(history_path)
    history.init_db()

    client = YtDlpClient(config)
    merger = FfmpegMerger(config.ffmpeg_path())

    if not merger.is_available():
        logger.warning("未检测到可用的 ffmpeg，高清视频将无法自动合并音视频")

    manager = DownloadManager(config, client, merger, history)
    logger.info("应用核心对象装配完成")
    return config, manager


__all__ = ["build_app"]
