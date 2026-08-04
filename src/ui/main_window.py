"""主窗口（集成中枢）。

职责：
- 组装四段式布局（输入栏 / 解析面板 / 下载列表 / 状态条）；
- 连接 ``DownloadManager`` 的全部信号到 ``on_<signal_name>`` 槽；
- 把表格的操作按钮事件翻译为 Manager 的命令调用；
- 菜单、托盘提示、窗口几何恢复与关闭确认。
"""

from __future__ import annotations

import base64
import binascii
import logging

from PySide6.QtCore import QByteArray, Qt, Slot
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from src.constants import (
    ACTION_CANCEL,
    ACTION_OPEN_FOLDER,
    ACTION_PAUSE,
    ACTION_REMOVE,
    ACTION_RESUME,
    ACTION_RETRY,
    APP_DISPLAY_NAME,
    APP_ICON_REL,
    APP_VERSION,
)
from src.core.download_manager import DownloadManager
from src.core.config_store import ConfigStore
from src.core.models import DownloadTask, MediaInfo
from src.ui.download_table import DownloadTableView
from src.ui.history_dialog import HistoryDialog
from src.ui.parse_panel import ParsePanel
from src.ui.settings_dialog import SettingsDialog
from src.ui.status_bar import StatusBarWidget
from src.ui.url_input_bar import UrlInputBar, looks_like_url
from src.utils.paths import open_in_explorer, resource_path

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, manager: DownloadManager, config: ConfigStore) -> None:
        """构建界面并连接信号。

        Args:
            manager: 下载调度器。
            config: 配置存取器。
        """
        super().__init__()
        self._manager: DownloadManager = manager
        self._config: ConfigStore = config
        self._tray: QSystemTrayIcon | None = None

        self.setObjectName("mainWindow")
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        self.resize(1080, 660)
        self.setMinimumSize(900, 520)
        self._apply_window_icon()

        self._url_bar = UrlInputBar(self)
        self._parse_panel = ParsePanel(self)
        self._table = DownloadTableView(self)
        self._status = StatusBarWidget(self)

        self._build_layout()
        self._build_menu()
        self._connect_signals()
        self._setup_tray()

        self._restore_geometry()
        self._url_bar.try_fill_from_clipboard()
        self._status.show_message("就绪", 3000)

    # ==================================================================
    # 界面构建
    # ==================================================================
    def _build_layout(self) -> None:
        """组装四段式主布局。"""
        central = QWidget(self)
        central.setObjectName("centralWidget")

        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 8)
        layout.setSpacing(10)
        layout.addWidget(self._url_bar, 0)
        layout.addWidget(self._parse_panel, 0)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status, 0)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        """构建菜单栏。"""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("文件(&F)")

        open_dir_action = QAction("打开下载目录", self)
        open_dir_action.setShortcut(QKeySequence("Ctrl+D"))
        open_dir_action.triggered.connect(self.open_download_dir)
        file_menu.addAction(open_dir_action)

        history_action = QAction("历史记录", self)
        history_action.setShortcut(QKeySequence("Ctrl+H"))
        history_action.triggered.connect(self.open_history)
        file_menu.addAction(history_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menu_bar.addMenu("工具(&T)")

        settings_action = QAction("设置", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.open_settings)
        tools_menu.addAction(settings_action)

        login_action = QAction("YouTube 登录（内置浏览器）", self)
        login_action.setObjectName("youtubeLoginAction")
        login_action.triggered.connect(self.open_youtube_login)
        tools_menu.addAction(login_action)

        clear_action = QAction("清除已结束任务", self)
        clear_action.triggered.connect(self.clear_finished_tasks)
        tools_menu.addAction(clear_action)

        help_menu = menu_bar.addMenu("帮助(&H)")

        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _connect_signals(self) -> None:
        """连接所有信号与槽。"""
        # UI → Manager
        self._url_bar.parse_requested.connect(self.on_parse_clicked)
        self._url_bar.download_requested.connect(self.on_download_clicked)
        self._parse_panel.download_requested.connect(self.on_download_clicked)
        self._table.action_triggered.connect(self.on_task_action)
        self._table.copy_url_requested.connect(self.on_copy_url)

        # Manager → UI
        self._manager.parse_ready.connect(self.on_parse_finished)
        self._manager.parse_error.connect(self.on_parse_error)
        self._manager.task_added.connect(self.on_task_added)
        self._manager.task_updated.connect(self.on_task_updated)
        self._manager.task_removed.connect(self.on_task_removed)
        self._manager.stats_updated.connect(self.on_stats_updated)
        self._manager.notify.connect(self.on_notify)

    def _setup_tray(self) -> None:
        """初始化系统托盘图标（不可用时静默跳过）。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.debug("系统托盘不可用，跳过托盘初始化")
            return
        icon = self.windowIcon()
        if icon.isNull():
            return
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(APP_DISPLAY_NAME)
        self._tray.show()

    def _apply_window_icon(self) -> None:
        """设置窗口图标（文件缺失时跳过）。"""
        icon_path = resource_path(APP_ICON_REL)
        icon = QIcon(icon_path)
        if not icon.isNull():
            self.setWindowIcon(icon)

    # ==================================================================
    # 槽函数：解析
    # ==================================================================
    @Slot(str)
    def on_parse_clicked(self, url: str = "") -> None:
        """处理解析请求。

        Args:
            url: 待解析链接；为空时取输入框内容。
        """
        target = str(url or self._url_bar.url()).strip()
        if not target:
            QMessageBox.information(self, "提示", "请先粘贴视频链接。")
            return
        if not looks_like_url(target):
            QMessageBox.warning(self, "链接无效", "请输入以 http:// 或 https:// 开头的完整链接。")
            return

        self._parse_panel.clear()
        self._url_bar.set_download_enabled(False)
        self._url_bar.set_parsing(True)
        self._status.show_message("正在解析链接…", 0)

        if not self._manager.parse(target):
            self._url_bar.set_parsing(False)
            self._status.show_message("", 1)

    @Slot(object)
    def on_parse_finished(self, info: object) -> None:
        """解析成功回调。

        Args:
            info: ``MediaInfo`` 对象。
        """
        self._url_bar.set_parsing(False)
        if not isinstance(info, MediaInfo):
            self._status.show_message("解析结果异常", 5000)
            return

        self._parse_panel.set_info(info, self._config.default_container())
        self._url_bar.set_download_enabled(True)
        self._status.show_message(f"解析成功：{info.title}", 5000)

    @Slot(str)
    def on_parse_error(self, message: str) -> None:
        """解析失败回调。

        Args:
            message: 中文错误消息。
        """
        self._url_bar.set_parsing(False)
        self._url_bar.set_download_enabled(False)
        self._status.show_message("解析失败", 5000)
        QMessageBox.warning(self, "解析失败", str(message))

    # ==================================================================
    # 槽函数：下载
    # ==================================================================
    @Slot()
    def on_download_clicked(self) -> None:
        """把当前解析结果加入下载队列。"""
        info = self._parse_panel.media_info()
        if info is None:
            QMessageBox.information(self, "提示", "请先解析链接。")
            return

        fmt = self._parse_panel.selected_format()
        if fmt is None:
            QMessageBox.information(self, "提示", "没有可用的画质选项。")
            return

        container = self._parse_panel.selected_container()
        self._config.set_default_container(container)

        task_id = self._manager.add_task(info, fmt, container)
        logger.info("已加入下载队列：%s（%s）", info.title, task_id)

        self._status.show_message(f"已加入下载队列：{info.title}", 4000)
        self._parse_panel.clear()
        self._url_bar.clear()
        self._url_bar.set_download_enabled(False)
        self._url_bar.focus_input()

    @Slot(object)
    def on_task_added(self, task: object) -> None:
        """新任务加入回调。

        Args:
            task: ``DownloadTask`` 对象。
        """
        if isinstance(task, DownloadTask):
            self._table.add_task(task)

    @Slot(str)
    def on_task_updated(self, task_id: str) -> None:
        """任务状态或进度更新回调。"""
        self._table.update_task(task_id)

    @Slot(str)
    def on_task_removed(self, task_id: str) -> None:
        """任务被移除回调。"""
        self._table.remove_task(task_id)

    @Slot(dict)
    def on_stats_updated(self, stats: dict) -> None:
        """全局统计更新回调。"""
        self._status.update_stats(stats)

    @Slot(str, str)
    def on_notify(self, title: str, message: str) -> None:
        """展示完成 / 失败提示（状态栏 + 托盘气泡）。"""
        self._status.show_message(f"{title}：{message}", 6000)
        if self._tray is not None and QSystemTrayIcon.supportsMessages():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    @Slot(str, str)
    def on_task_action(self, task_id: str, action: str) -> None:
        """把表格操作按钮事件翻译为 Manager 命令。

        Args:
            task_id: 任务 id。
            action: 动作标识（见 ``constants.ACTION_*``）。
        """
        if action == ACTION_PAUSE:
            self._manager.pause_task(task_id)
        elif action == ACTION_RESUME:
            self._manager.resume_task(task_id)
        elif action == ACTION_CANCEL:
            self._manager.cancel_task(task_id)
        elif action == ACTION_RETRY:
            self._manager.retry_task(task_id)
        elif action == ACTION_REMOVE:
            self._manager.remove_task(task_id)
        elif action == ACTION_OPEN_FOLDER:
            if not self._manager.open_folder(task_id):
                QMessageBox.warning(self, "打开失败", "文件或目录不存在。")
        else:
            logger.warning("未知的表格操作：%s", action)

    @Slot(str)
    def on_copy_url(self, url: str) -> None:
        """复制链接到剪贴板。"""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(url or ""))
            self._status.show_message("链接已复制到剪贴板", 3000)

    # ==================================================================
    # 菜单动作
    # ==================================================================
    def open_settings(self) -> None:
        """打开设置对话框，确认后立即同步并发上限。"""
        dialog = SettingsDialog(self._config, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._manager.apply_concurrency()
            self._status.show_message("设置已保存", 3000)

    def open_youtube_login(self) -> None:
        """打开内置浏览器登录 YouTube，登录后自动导出 cookies。

        懒加载 ``LoginBrowserDialog``：若该环境缺少 QtWebEngine 模块，
        ``import`` 或实例化会失败，此处捕获并提示用户，不影响其他功能。
        """
        try:
            from src.ui.login_browser import LoginBrowserDialog
        except ImportError as exc:
            logger.warning("内置浏览器模块加载失败：%s", exc)
            QMessageBox.warning(self, "功能不可用", f"内置浏览器模块加载失败：{exc}")
            return

        try:
            dialog = LoginBrowserDialog(self._config, self)
        except RuntimeError as exc:
            logger.warning("无法打开内置浏览器：%s", exc)
            QMessageBox.warning(self, "无法打开内置浏览器", str(exc))
            return

        dialog.exec()

    def open_history(self) -> None:
        """打开历史记录对话框。"""
        dialog = HistoryDialog(self._manager.history, self)
        dialog.redownload_requested.connect(self.on_redownload_requested)
        dialog.exec()

    @Slot(str)
    def on_redownload_requested(self, url: str) -> None:
        """历史记录请求重新下载：填入输入框并自动解析。"""
        self._url_bar.set_url(url)
        self.on_parse_clicked(url)

    def open_download_dir(self) -> None:
        """在资源管理器中打开下载目录。"""
        if not open_in_explorer(self._config.download_dir()):
            QMessageBox.warning(self, "打开失败", "下载目录不存在或无法访问。")

    def clear_finished_tasks(self) -> None:
        """清除所有已结束的任务行。"""
        count = self._manager.clear_finished()
        self._status.show_message(f"已清除 {count} 条已结束任务", 3000)

    def show_about(self) -> None:
        """显示关于对话框。"""
        QMessageBox.about(
            self,
            "关于",
            f"<b>{APP_DISPLAY_NAME}</b> v{APP_VERSION}<br><br>"
            "基于 Python + PySide6 + yt-dlp 的桌面视频下载器。<br>"
            "本软件不支持 DRM 加密平台，请仅用于下载已获授权的内容。",
        )

    # ==================================================================
    # 窗口几何与关闭
    # ==================================================================
    def _restore_geometry(self) -> None:
        """从配置恢复窗口几何。"""
        encoded = self._config.window_geometry()
        if not encoded:
            return
        try:
            raw = base64.b64decode(encoded.encode("ascii"))
            self.restoreGeometry(QByteArray(raw))
        except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
            logger.debug("恢复窗口几何失败：%s", exc)

    def _save_geometry(self) -> None:
        """把窗口几何保存到配置。"""
        try:
            raw = bytes(self.saveGeometry())
            self._config.set_window_geometry(base64.b64encode(raw).decode("ascii"))
        except (TypeError, ValueError) as exc:
            logger.debug("保存窗口几何失败：%s", exc)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        """关闭窗口：有活跃任务时确认，随后收尾。"""
        if self._manager.has_active_tasks():
            answer = QMessageBox.question(
                self,
                "确认退出",
                "仍有任务正在下载或排队，退出将中断这些任务。确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._save_geometry()
        try:
            self._manager.shutdown()
        except Exception as exc:  # noqa: BLE001 - 退出流程不应再抛异常
            logger.error("关闭过程出错：%s", exc)

        if self._tray is not None:
            self._tray.hide()

        event.accept()


__all__ = ["MainWindow"]
