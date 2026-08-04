"""解析结果面板。

展示标题 / UP 主 / 时长 / 缩略图，并提供画质与容器下拉框。
解析前整体隐藏；缩略图异步加载，失败时留空且不影响主流程。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import SUPPORTED_CONTAINERS
from src.core.format_utils import human_duration, truncate
from src.core.models import MediaFormat, MediaInfo

logger = logging.getLogger(__name__)

_THUMB_WIDTH = 160
_THUMB_HEIGHT = 90


class ParsePanel(QFrame):
    """解析结果展示与格式选择面板。

    Signals:
        download_requested: ``()`` —— 用户点击面板内的「加入下载」。
    """

    download_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """构建界面。"""
        super().__init__(parent)
        self.setObjectName("parsePanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._info: MediaInfo | None = None
        self._network: QNetworkAccessManager = QNetworkAccessManager(self)
        self._thumb_reply: QNetworkReply | None = None

        self._thumb = QLabel(self)
        self._thumb.setObjectName("thumbnail")
        self._thumb.setFixedSize(_THUMB_WIDTH, _THUMB_HEIGHT)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setText("无预览")

        self._title = QLabel("--", self)
        self._title.setObjectName("parseTitle")
        self._title.setWordWrap(True)

        self._meta = QLabel("--", self)
        self._meta.setObjectName("parseMeta")

        self._quality_combo = QComboBox(self)
        self._quality_combo.setObjectName("qualityCombo")
        self._quality_combo.setMinimumWidth(280)

        self._container_combo = QComboBox(self)
        self._container_combo.setObjectName("containerCombo")
        self._container_combo.setMinimumWidth(120)

        self._add_btn = QPushButton("加入下载", self)
        self._add_btn.setObjectName("addToQueueButton")
        self._add_btn.setProperty("variant", "primary")
        self._add_btn.clicked.connect(self.download_requested)

        self._build_layout()
        self.setVisible(False)

    def _build_layout(self) -> None:
        """组装布局。"""
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        text_box.addWidget(self._title)
        text_box.addWidget(self._meta)

        form = QFormLayout()
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(8)
        form.addRow("画质：", self._quality_combo)
        form.addRow("格式：", self._container_combo)
        text_box.addLayout(form)

        right_box = QVBoxLayout()
        right_box.addStretch(1)
        right_box.addWidget(self._add_btn)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)
        layout.addWidget(self._thumb, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_box, 1)
        layout.addLayout(right_box, 0)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_info(self, info: MediaInfo, default_container: str = "mp4") -> None:
        """填充解析结果并显示面板。

        Args:
            info: 解析得到的媒体信息。
            default_container: 默认容器格式。
        """
        self._info = info

        self._title.setText(truncate(info.title or "未命名视频", 120))
        meta_parts: list[str] = []
        if info.uploader:
            meta_parts.append(f"UP 主：{info.uploader}")
        if info.duration:
            meta_parts.append(f"时长：{human_duration(info.duration)}")
        if info.extractor:
            meta_parts.append(f"来源：{info.extractor}")
        self._meta.setText("　".join(meta_parts) if meta_parts else "--")

        # 画质下拉：默认选中第一项（伪「最佳画质（自动）」）
        self._quality_combo.clear()
        for fmt in info.filter_video_formats():
            self._quality_combo.addItem(fmt.label or fmt.display_text(), fmt)
        if self._quality_combo.count() > 0:
            self._quality_combo.setCurrentIndex(0)

        # 容器下拉：站点提供的容器 + 通用容器
        self._container_combo.clear()
        containers: list[str] = []
        for ext in list(info.available_containers()) + list(SUPPORTED_CONTAINERS):
            if ext and ext not in containers and ext in SUPPORTED_CONTAINERS:
                containers.append(ext)
        if not containers:
            containers = list(SUPPORTED_CONTAINERS)
        self._container_combo.addItems(containers)
        if default_container in containers:
            self._container_combo.setCurrentText(default_container)

        self._load_thumbnail(info.thumbnail)
        self.setVisible(True)

    def selected_format(self) -> MediaFormat | None:
        """返回当前选中的格式。

        Returns:
            ``MediaFormat``；无解析结果时返回 ``None``。
        """
        if self._info is None:
            return None
        data = self._quality_combo.currentData()
        if isinstance(data, MediaFormat):
            return data
        return self._info.best_format()

    def selected_container(self) -> str:
        """返回当前选中的容器格式。"""
        return self._container_combo.currentText().strip() or "mp4"

    def media_info(self) -> MediaInfo | None:
        """返回当前面板承载的解析结果。"""
        return self._info

    def clear(self) -> None:
        """清空面板并隐藏。"""
        self._abort_thumbnail()
        self._info = None
        self._title.setText("--")
        self._meta.setText("--")
        self._quality_combo.clear()
        self._container_combo.clear()
        self._thumb.setPixmap(QPixmap())
        self._thumb.setText("无预览")
        self.setVisible(False)

    # ------------------------------------------------------------------
    # 缩略图异步加载
    # ------------------------------------------------------------------
    def _load_thumbnail(self, url: str | None) -> None:
        """异步下载并显示缩略图（失败静默）。

        Args:
            url: 缩略图地址。
        """
        self._abort_thumbnail()
        self._thumb.setPixmap(QPixmap())
        self._thumb.setText("加载中…")

        if not url or not str(url).lower().startswith("http"):
            self._thumb.setText("无预览")
            return

        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._network.get(request)
        self._thumb_reply = reply
        reply.finished.connect(lambda r=reply: self._on_thumbnail_finished(r))

    def _on_thumbnail_finished(self, reply: QNetworkReply) -> None:
        """缩略图下载完成回调。"""
        try:
            if reply is not self._thumb_reply:
                return
            self._thumb_reply = None

            if reply.error() != QNetworkReply.NetworkError.NoError:
                logger.debug("缩略图下载失败：%s", reply.errorString())
                self._thumb.setText("无预览")
                return

            data: QByteArray = reply.readAll()
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self._thumb.setText("无预览")
                return

            scaled = pixmap.scaled(
                _THUMB_WIDTH,
                _THUMB_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb.setText("")
            self._thumb.setPixmap(scaled)
        except RuntimeError as exc:  # 控件已销毁
            logger.debug("缩略图回调时控件已销毁：%s", exc)
        finally:
            reply.deleteLater()

    def _abort_thumbnail(self) -> None:
        """中止进行中的缩略图请求。"""
        if self._thumb_reply is not None:
            try:
                self._thumb_reply.abort()
            except RuntimeError:
                pass
            self._thumb_reply = None


__all__ = ["ParsePanel"]
