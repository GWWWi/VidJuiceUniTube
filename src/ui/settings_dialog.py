"""设置对话框。

所有配置读写都通过 ``ConfigStore`` 的语义化 getter/setter，
不出现裸字符串 key。``accept()`` 落盘后由 ``MainWindow`` 同步并发上限。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.constants import (
    MAX_CONCURRENT_LIMIT,
    MAX_RETRIES_LIMIT,
    MIN_CONCURRENT,
    MIN_RETRIES,
)
from src.core.config_store import ConfigStore

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """应用设置对话框。"""

    def __init__(self, config: ConfigStore, parent: QWidget | None = None) -> None:
        """构建界面并载入当前配置。

        Args:
            config: 配置存取器。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._config: ConfigStore = config

        self.setObjectName("settingsDialog")
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)

        self._dir_edit = QLineEdit(self)
        self._dir_browse = QPushButton("浏览…", self)
        self._dir_browse.clicked.connect(self._on_browse_dir)

        self._concurrent_spin = QSpinBox(self)
        self._concurrent_spin.setRange(MIN_CONCURRENT, MAX_CONCURRENT_LIMIT)

        self._retries_spin = QSpinBox(self)
        self._retries_spin.setRange(MIN_RETRIES, MAX_RETRIES_LIMIT)

        self._ffmpeg_edit = QLineEdit(self)
        self._ffmpeg_edit.setPlaceholderText("留空则自动查找（随包目录 / 系统 PATH）")
        self._ffmpeg_browse = QPushButton("浏览…", self)
        self._ffmpeg_browse.clicked.connect(self._on_browse_ffmpeg)

        self._cookie_edit = QLineEdit(self)
        self._cookie_edit.setPlaceholderText("Netscape 格式 cookies.txt，用于 YouTube 年龄限制/会员内容（需已登录账号）")
        self._cookie_browse = QPushButton("浏览…", self)
        self._cookie_browse.clicked.connect(self._on_browse_cookie)

        self._proxy_edit = QLineEdit(self)
        self._proxy_edit.setPlaceholderText("如 http://127.0.0.1:7890，留空表示直连")

        self._auto_open_check = QCheckBox("下载完成后自动打开所在目录", self)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        self._build_layout()
        self.load_values()

    def _build_layout(self) -> None:
        """组装布局。"""
        form = QFormLayout()
        form.setSpacing(10)

        form.addRow("下载目录：", self._with_button(self._dir_edit, self._dir_browse))
        form.addRow("最大并发数：", self._concurrent_spin)
        form.addRow("失败重试次数：", self._retries_spin)
        form.addRow("ffmpeg 路径：", self._with_button(self._ffmpeg_edit, self._ffmpeg_browse))
        form.addRow("cookie 文件：", self._with_button(self._cookie_edit, self._cookie_browse))
        form.addRow("代理地址：", self._proxy_edit)
        form.addRow("", self._auto_open_check)

        hint = QLabel(
            "提示：修改并发数后立即生效；cookie 用于 YouTube 年龄限制 / 会员内容，"
            "需导出已登录（满 18 岁）账号的 cookies.txt（Netscape 格式），本地保存不上传。",
            self,
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addStretch(1)
        layout.addWidget(self._buttons)

    @staticmethod
    def _with_button(edit: QLineEdit, button: QPushButton) -> QWidget:
        """把输入框与浏览按钮组合成一行控件。"""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(edit, 1)
        row.addWidget(button, 0)
        return container

    # ------------------------------------------------------------------
    # 数据读写
    # ------------------------------------------------------------------
    def load_values(self) -> None:
        """把配置载入到控件。"""
        self._dir_edit.setText(self._config.download_dir())
        self._concurrent_spin.setValue(self._config.max_concurrent())
        self._retries_spin.setValue(self._config.max_retries())
        self._ffmpeg_edit.setText(self._config.ffmpeg_path() or "")
        self._cookie_edit.setText(self._config.cookiefile() or "")
        self._proxy_edit.setText(self._config.proxy() or "")
        self._auto_open_check.setChecked(self._config.auto_open_folder())

    def accept(self) -> None:
        """把控件值写回配置并保存。"""
        self._config.set_download_dir(self._dir_edit.text().strip())
        self._config.set_max_concurrent(self._concurrent_spin.value())
        self._config.set_max_retries(self._retries_spin.value())
        self._config.set_ffmpeg_path(self._ffmpeg_edit.text().strip())
        self._config.set_cookiefile(self._cookie_edit.text().strip())
        self._config.set_proxy(self._proxy_edit.text().strip())
        self._config.set_auto_open_folder(self._auto_open_check.isChecked())
        self._config.save()
        logger.info("设置已保存")
        super().accept()

    # ------------------------------------------------------------------
    # 浏览按钮
    # ------------------------------------------------------------------
    def _on_browse_dir(self) -> None:
        """选择下载目录。"""
        path = QFileDialog.getExistingDirectory(self, "选择下载目录", self._dir_edit.text().strip())
        if path:
            self._dir_edit.setText(path)

    def _on_browse_ffmpeg(self) -> None:
        """选择 ffmpeg 可执行文件。"""
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg 可执行文件", self._ffmpeg_edit.text().strip(), "可执行文件 (*.exe);;所有文件 (*)"
        )
        if path:
            self._ffmpeg_edit.setText(path)

    def _on_browse_cookie(self) -> None:
        """选择 cookie 文件。"""
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择 cookie 文件", self._cookie_edit.text().strip(), "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self._cookie_edit.setText(path)


__all__ = ["SettingsDialog"]
