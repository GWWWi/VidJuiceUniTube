"""解析工作线程。

在 ``QThreadPool`` 子线程中执行阻塞的 ``YtDlpClient.extract_info``，
结果通过 ``WorkerSignals`` 回传主线程。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRunnable, Slot

from src.core.ytdlp_client import YtDlpClient
from src.workers.signals import WorkerSignals

logger = logging.getLogger(__name__)


class ParseWorker(QRunnable):
    """执行一次链接解析的可运行任务。

    Attributes:
        signals: 信号载体，由外部连接。
    """

    def __init__(self, url: str, client: YtDlpClient) -> None:
        """初始化解析任务。

        Args:
            url: 待解析的视频链接。
            client: yt-dlp 客户端。
        """
        super().__init__()
        self.signals: WorkerSignals = WorkerSignals()
        self._url: str = str(url or "").strip()
        self._client: YtDlpClient = client
        self.setAutoDelete(False)  # 由调用方持有引用，避免提前回收

    @Slot()
    def run(self) -> None:
        """子线程入口：整体 try/except 包裹，杜绝异常逃逸导致静默崩溃。"""
        try:
            self.signals.log_message.emit(f"正在解析：{self._url}")
            info = self._client.extract_info(self._url)
            self.signals.parse_finished.emit(info)
            self.signals.log_message.emit(f"解析成功：{info.title}")
        except Exception as exc:  # noqa: BLE001 - 子线程必须吞掉所有异常
            # 错误翻译自身也可能失败（客户端为测试替身等），再包一层兜底，
            # 保证 parse_failed 信号必定发出，主线程不会一直停在「解析中」。
            try:
                getter = getattr(self._client, "cookie_path", None)
                cookie_path = getter() if callable(getter) else None
                message = YtDlpClient.probe_error(exc, cookie_path=cookie_path)
            except Exception:  # noqa: BLE001
                logger.error("错误翻译失败，回退为原始异常文案", exc_info=True)
                message = f"解析失败：{exc}"
            logger.warning("ParseWorker 解析失败 %s：%s", self._url, exc, exc_info=False)
            try:
                self.signals.parse_failed.emit(self._url, message)
            except RuntimeError:  # 目标对象已销毁（应用退出中）
                logger.debug("parse_failed 信号发送失败：接收者已销毁")


__all__ = ["ParseWorker"]
