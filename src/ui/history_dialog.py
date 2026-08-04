"""历史记录对话框。

展示已完成的下载记录，支持搜索、打开文件、打开目录、重新下载、
删除单条与清空全部。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.format_utils import human_size, human_time, truncate
from src.core.history_store import HistoryStore
from src.utils.paths import open_file, open_in_explorer

logger = logging.getLogger(__name__)

_COLUMNS: tuple[str, ...] = ("标题", "画质", "大小", "完成时间", "保存路径")
_COL_TITLE = 0
_COL_QUALITY = 1
_COL_SIZE = 2
_COL_TIME = 3
_COL_PATH = 4

#: 存放整行原始数据的自定义 role
_ROLE_RECORD = int(Qt.ItemDataRole.UserRole) + 1


class HistoryDialog(QDialog):
    """下载历史对话框。

    Signals:
        redownload_requested: ``(str)`` —— 请求重新下载该链接。
    """

    redownload_requested = Signal(str)

    def __init__(self, history: HistoryStore, parent: QWidget | None = None) -> None:
        """构建界面并加载数据。

        Args:
            history: 历史记录仓库。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._history: HistoryStore = history

        self.setObjectName("historyDialog")
        self.setWindowTitle("下载历史")
        self.resize(920, 520)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索标题或链接…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)

        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_open_file)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_QUALITY, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)

        self._count_label = QLabel("共 0 条记录", self)
        self._count_label.setObjectName("historyCount")

        self._open_file_btn = QPushButton("打开文件", self)
        self._open_dir_btn = QPushButton("打开目录", self)
        self._redownload_btn = QPushButton("重新下载", self)
        self._delete_btn = QPushButton("删除记录", self)
        self._clear_btn = QPushButton("清空全部", self)
        self._close_btn = QPushButton("关闭", self)

        self._open_file_btn.clicked.connect(self._on_open_file)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._redownload_btn.clicked.connect(self._on_redownload)
        self._delete_btn.clicked.connect(self._on_delete)
        self._clear_btn.clicked.connect(self._on_clear)
        self._close_btn.clicked.connect(self.accept)

        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        """组装布局。"""
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self._search, 1)
        top.addWidget(self._count_label, 0)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self._open_file_btn)
        bottom.addWidget(self._open_dir_btn)
        bottom.addWidget(self._redownload_btn)
        bottom.addWidget(self._delete_btn)
        bottom.addWidget(self._clear_btn)
        bottom.addStretch(1)
        bottom.addWidget(self._close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self._table, 1)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def refresh(self, keyword: str = "") -> None:
        """按关键词刷新列表。

        Args:
            keyword: 搜索关键词，空表示全部。
        """
        records = self._history.search(keyword) if keyword else self._history.list_all()

        self._table.setRowCount(0)
        for record in records:
            self._append_row(record)

        self._count_label.setText(f"共 {len(records)} 条记录")

    def _append_row(self, record: dict[str, Any]) -> None:
        """向表格追加一行。

        Args:
            record: ``HistoryStore`` 返回的记录字典。
        """
        row = self._table.rowCount()
        self._table.insertRow(row)

        title_item = QTableWidgetItem(truncate(str(record.get("title") or ""), 80))
        title_item.setData(_ROLE_RECORD, record)
        title_item.setToolTip(str(record.get("url") or ""))

        cells = [
            title_item,
            QTableWidgetItem(str(record.get("resolution") or "--")),
            QTableWidgetItem(human_size(record.get("filesize"))),
            QTableWidgetItem(human_time(record.get("finished_at"))),
            QTableWidgetItem(str(record.get("filepath") or "--")),
        ]
        for column, item in enumerate(cells):
            if column != _COL_TITLE:
                item.setToolTip(item.text())
            self._table.setItem(row, column, item)

    def _current_record(self) -> dict[str, Any] | None:
        """返回当前选中行的原始记录。"""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, _COL_TITLE)
        if item is None:
            return None
        record = item.data(_ROLE_RECORD)
        return record if isinstance(record, dict) else None

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_search_changed(self, text: str) -> None:
        """搜索框内容变化。"""
        self.refresh(text.strip())

    def _on_open_file(self) -> None:
        """打开选中记录对应的文件。"""
        record = self._require_selection()
        if record is None:
            return
        path = str(record.get("filepath") or "")
        if not open_file(path):
            QMessageBox.warning(self, "打开失败", "文件不存在或已被移动、删除。")

    def _on_open_dir(self) -> None:
        """在资源管理器中定位选中记录。"""
        record = self._require_selection()
        if record is None:
            return
        path = str(record.get("filepath") or "")
        if not open_in_explorer(path):
            QMessageBox.warning(self, "打开失败", "目录不存在或无法访问。")

    def _on_redownload(self) -> None:
        """请求重新下载选中记录。"""
        record = self._require_selection()
        if record is None:
            return
        url = str(record.get("url") or "").strip()
        if not url:
            QMessageBox.warning(self, "无法重新下载", "该记录缺少原始链接。")
            return
        self.redownload_requested.emit(url)
        self.accept()

    def _on_delete(self) -> None:
        """删除选中记录。"""
        record = self._require_selection()
        if record is None:
            return
        record_id = record.get("id")
        if record_id is None:
            return
        if QMessageBox.question(self, "删除记录", "确定要删除这条历史记录吗？") != QMessageBox.StandardButton.Yes:
            return
        self._history.delete(int(record_id))
        self.refresh(self._search.text().strip())

    def _on_clear(self) -> None:
        """清空全部记录。"""
        if QMessageBox.question(self, "清空历史", "确定要清空全部下载历史吗？此操作不可撤销。") != QMessageBox.StandardButton.Yes:
            return
        self._history.clear()
        self.refresh()

    def _require_selection(self) -> dict[str, Any] | None:
        """获取选中记录；未选中时提示用户。"""
        record = self._current_record()
        if record is None:
            QMessageBox.information(self, "未选择", "请先选择一条历史记录。")
        return record


__all__ = ["HistoryDialog"]
