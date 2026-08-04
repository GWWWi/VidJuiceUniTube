"""顶部链接输入栏。

功能：
- 启动时若剪贴板内容是 URL 则自动填入；
- 回车 = 触发解析；
- ``[解析]`` / ``[下载]`` 按钮状态由 ``MainWindow`` 控制。
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

logger = logging.getLogger(__name__)

#: 简易 URL 校验（够用即可，真正的合法性由 yt-dlp 判断）
_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def looks_like_url(text: str) -> bool:
    """判断文本是否像一个 http(s) 链接。

    Args:
        text: 待判断文本。

    Returns:
        是否为合法 URL 形态。
    """
    return bool(_URL_RE.match(str(text or "").strip()))


class UrlInputBar(QWidget):
    """链接输入栏控件。

    Signals:
        parse_requested: ``(str)`` —— 请求解析该链接。
        download_requested: ``()`` —— 请求下载当前解析结果。
    """

    parse_requested = Signal(str)
    download_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """构建界面并连接内部信号。"""
        super().__init__(parent)
        self.setObjectName("urlInputBar")

        self._input = QLineEdit(self)
        self._input.setObjectName("urlInput")
        self._input.setPlaceholderText("粘贴视频地址…（支持 YouTube / Bilibili 等站点）")
        self._input.setClearButtonEnabled(True)
        self._input.returnPressed.connect(self._on_parse_clicked)

        self._parse_btn = QPushButton("解析", self)
        self._parse_btn.setObjectName("parseButton")
        self._parse_btn.setProperty("variant", "primary")
        self._parse_btn.setMinimumWidth(88)
        self._parse_btn.clicked.connect(self._on_parse_clicked)

        self._download_btn = QPushButton("下载", self)
        self._download_btn.setObjectName("downloadButton")
        self._download_btn.setMinimumWidth(88)
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self.download_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._input, 1)
        layout.addWidget(self._parse_btn)
        layout.addWidget(self._download_btn)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def url(self) -> str:
        """返回输入框中的链接（已去空白）。"""
        return self._input.text().strip()

    def set_url(self, url: str) -> None:
        """设置输入框内容。"""
        self._input.setText(str(url or ""))

    def clear(self) -> None:
        """清空输入框。"""
        self._input.clear()

    def focus_input(self) -> None:
        """聚焦输入框并全选。"""
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)
        self._input.selectAll()

    def set_parsing(self, parsing: bool) -> None:
        """切换解析中状态（按钮置灰 + 文案变化）。

        Args:
            parsing: 是否正在解析。
        """
        self._parse_btn.setEnabled(not parsing)
        self._parse_btn.setText("解析中…" if parsing else "解析")
        self._input.setEnabled(not parsing)

    def set_download_enabled(self, enabled: bool) -> None:
        """启用 / 禁用下载按钮。"""
        self._download_btn.setEnabled(bool(enabled))

    def try_fill_from_clipboard(self) -> bool:
        """若剪贴板内容是 URL 且输入框为空，则自动填入。

        Returns:
            是否发生了填充。
        """
        if self._input.text().strip():
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        text = (clipboard.text() or "").strip()
        if looks_like_url(text):
            self._input.setText(text)
            logger.info("已从剪贴板自动填入链接")
            return True
        return False

    # ------------------------------------------------------------------
    # 内部槽
    # ------------------------------------------------------------------
    def _on_parse_clicked(self) -> None:
        """解析按钮 / 回车处理。"""
        url = self.url()
        if not url:
            self.focus_input()
            return
        self.parse_requested.emit(url)


__all__ = ["UrlInputBar", "looks_like_url"]
