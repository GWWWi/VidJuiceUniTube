"""下载列表视图。

组合 ``DownloadTableModel`` + 两个自绘 Delegate，并提供右键菜单。
所有用户操作统一通过 ``action_triggered(task_id, action)`` 向上转发，
由 ``MainWindow`` 翻译为 ``DownloadManager`` 的方法调用。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView, QWidget

from src.constants import (
    ACTION_CANCEL,
    ACTION_OPEN_FOLDER,
    ACTION_PAUSE,
    ACTION_REMOVE,
    ACTION_RESUME,
    ACTION_RETRY,
    ACTION_TEXT_MAP,
    COL_ACTION,
    COL_PROGRESS,
    COL_QUALITY,
    COL_SPEED,
    COL_STATUS,
    COL_TITLE,
)
from src.core.models import DownloadTask, TaskStatus
from src.ui.delegates import STATUS_ACTIONS, ActionButtonDelegate, ProgressBarDelegate
from src.ui.table_model import ROLE_TASK, DownloadTableModel

logger = logging.getLogger(__name__)


class DownloadTableView(QTableView):
    """下载任务表格视图。

    Signals:
        action_triggered: ``(str, str)`` —— ``task_id`` 与动作标识。
        copy_url_requested: ``(str)`` —— 请求复制链接。
    """

    action_triggered = Signal(str, str)
    copy_url_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化视图与模型。"""
        super().__init__(parent)
        self._model: DownloadTableModel = DownloadTableModel(self)
        self.setModel(self._model)

        self._progress_delegate = ProgressBarDelegate(self)
        self._action_delegate = ActionButtonDelegate(self)
        self.setItemDelegateForColumn(COL_PROGRESS, self._progress_delegate)
        self.setItemDelegateForColumn(COL_ACTION, self._action_delegate)
        self._action_delegate.action_triggered.connect(self.action_triggered)

        self._setup_appearance()
        self._setup_context_menu()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _setup_appearance(self) -> None:
        """配置表格外观与选择行为。"""
        self.setObjectName("downloadTable")
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(True)  # 让操作列按钮有 hover 效果
        self.setWordWrap(False)
        self.setSortingEnabled(False)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_QUALITY, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_SPEED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.Fixed)

        self.setColumnWidth(COL_QUALITY, 150)
        self.setColumnWidth(COL_PROGRESS, 180)
        self.setColumnWidth(COL_SPEED, 100)
        self.setColumnWidth(COL_STATUS, 100)
        self.setColumnWidth(COL_ACTION, 170)

    def _setup_context_menu(self) -> None:
        """启用右键菜单。"""
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def model_ref(self) -> DownloadTableModel:
        """返回底层数据模型。"""
        return self._model

    def add_task(self, task: DownloadTask) -> None:
        """新增一行任务。"""
        self._model.add_task(task)

    def update_task(self, task_id: str) -> None:
        """刷新指定任务行。"""
        self._model.update_task(task_id)

    def remove_task(self, task_id: str) -> None:
        """移除指定任务行。"""
        self._model.remove_task(task_id)

    def current_task(self) -> DownloadTask | None:
        """返回当前选中的任务。"""
        index = self.currentIndex()
        if not index.isValid():
            return None
        task = index.data(ROLE_TASK)
        return task if isinstance(task, DownloadTask) else None

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos: QPoint) -> None:
        """构建并弹出右键菜单。

        Args:
            pos: 视口内的鼠标位置。
        """
        index = self.indexAt(pos)
        if not index.isValid():
            return

        task = index.data(ROLE_TASK)
        if not isinstance(task, DownloadTask):
            return

        menu = QMenu(self)
        for action_key in STATUS_ACTIONS.get(task.status, ()):
            item = QAction(ACTION_TEXT_MAP.get(action_key, action_key), menu)
            item.triggered.connect(
                lambda _checked=False, tid=task.task_id, act=action_key: self.action_triggered.emit(tid, act)
            )
            menu.addAction(item)

        if task.status != TaskStatus.COMPLETED:
            open_dir = QAction(ACTION_TEXT_MAP[ACTION_OPEN_FOLDER], menu)
            open_dir.triggered.connect(
                lambda _checked=False, tid=task.task_id: self.action_triggered.emit(tid, ACTION_OPEN_FOLDER)
            )
            menu.addAction(open_dir)

        menu.addSeparator()
        copy_action = QAction("复制链接", menu)
        copy_action.triggered.connect(lambda _checked=False, url=task.url: self.copy_url_requested.emit(url))
        menu.addAction(copy_action)

        menu.exec(self.viewport().mapToGlobal(pos))


__all__ = [
    "ACTION_CANCEL",
    "ACTION_PAUSE",
    "ACTION_REMOVE",
    "ACTION_RESUME",
    "ACTION_RETRY",
    "DownloadTableView",
]
