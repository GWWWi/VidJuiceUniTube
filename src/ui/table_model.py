"""下载列表数据模型。

性能约定：
- ``_index`` 维护 ``task_id → row`` 映射，保证 ``update_task`` 是 O(1)；
- 刷新只 emit 对应行的 ``dataChanged``，**禁止**使用 ``layoutChanged`` 或
  ``beginResetModel``（会导致选中态丢失与明显卡顿）。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from src.constants import (
    COL_ACTION,
    COL_PROGRESS,
    COL_QUALITY,
    COL_SPEED,
    COL_STATUS,
    COL_TITLE,
    STATUS_TEXT_MAP,
    TABLE_COLUMNS,
)
from src.core.format_utils import human_speed, percent_text, truncate
from src.core.models import DownloadTask, TaskStatus

logger = logging.getLogger(__name__)

#: 自定义 role：返回 ``DownloadTask`` 对象供 Delegate 使用
ROLE_TASK: int = int(Qt.ItemDataRole.UserRole) + 1
#: 自定义 role：返回 ``task_id``
ROLE_TASK_ID: int = int(Qt.ItemDataRole.UserRole) + 2


class DownloadTableModel(QAbstractTableModel):
    """下载任务表格模型。"""

    #: 列标题
    COLUMNS: list[str] = list(TABLE_COLUMNS)

    def __init__(self, parent: Any = None) -> None:
        """初始化空模型。"""
        super().__init__(parent)
        self._rows: list[DownloadTask] = []
        self._index: dict[str, int] = {}

    # ------------------------------------------------------------------
    # QAbstractTableModel 接口
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        """返回行数。"""
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        """返回列数。"""
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        """返回单元格数据。

        Args:
            index: 单元格索引。
            role: Qt 数据角色。

        Returns:
            对应角色的数据；无效索引返回 ``None``。
        """
        if not index.isValid():
            return None

        row = index.row()
        if row < 0 or row >= len(self._rows):
            return None

        task = self._rows[row]
        column = index.column()

        if role == ROLE_TASK:
            return task
        if role == ROLE_TASK_ID:
            return task.task_id

        if role == int(Qt.ItemDataRole.DisplayRole):
            return self._display_text(task, column)

        if role == int(Qt.ItemDataRole.ToolTipRole):
            return self._tooltip_text(task)

        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            if column in (COL_QUALITY, COL_SPEED, COL_STATUS, COL_ACTION):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        """返回表头数据。"""
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
            return None
        return section + 1

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """返回单元格标志（操作列需要可编辑以接收鼠标事件）。"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_ACTION:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
    def add_task(self, task: DownloadTask) -> None:
        """在表尾追加一行。

        Args:
            task: 新任务。
        """
        if task is None or task.task_id in self._index:
            return
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(task)
        self._index[task.task_id] = row
        self.endInsertRows()

    def update_task(self, task_id: str) -> None:
        """刷新指定任务所在行（只 emit 该行 ``dataChanged``）。

        Args:
            task_id: 任务 id。
        """
        row = self._index.get(task_id)
        if row is None or row >= len(self._rows):
            return
        top_left = self.index(row, 0)
        bottom_right = self.index(row, len(self.COLUMNS) - 1)
        self.dataChanged.emit(top_left, bottom_right, [int(Qt.ItemDataRole.DisplayRole), ROLE_TASK])

    def remove_task(self, task_id: str) -> None:
        """移除指定任务所在行并重建索引。

        Args:
            task_id: 任务 id。
        """
        row = self._index.get(task_id)
        if row is None or row >= len(self._rows):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._rows.pop(row)
        self.endRemoveRows()
        self._rebuild_index()

    def task_at(self, row: int) -> DownloadTask | None:
        """按行号获取任务。

        Args:
            row: 行号。

        Returns:
            任务对象；越界返回 ``None``。
        """
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def row_of(self, task_id: str) -> int:
        """按任务 id 获取行号（不存在返回 ``-1``）。"""
        return self._index.get(task_id, -1)

    def clear(self) -> None:
        """清空所有行。"""
        if not self._rows:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._rows) - 1)
        self._rows.clear()
        self._index.clear()
        self.endRemoveRows()

    def _rebuild_index(self) -> None:
        """重建 ``task_id → row`` 索引。"""
        self._index = {task.task_id: idx for idx, task in enumerate(self._rows)}

    # ------------------------------------------------------------------
    # 文案生成
    # ------------------------------------------------------------------
    @staticmethod
    def _display_text(task: DownloadTask, column: int) -> str:
        """生成指定列的展示文本。"""
        if column == COL_TITLE:
            return truncate(task.display_name(), 60)
        if column == COL_QUALITY:
            return task.resolution or "--"
        if column == COL_PROGRESS:
            return percent_text(task.progress.percent)
        if column == COL_SPEED:
            if task.status == TaskStatus.DOWNLOADING:
                return human_speed(task.progress.speed)
            return "--"
        if column == COL_STATUS:
            text = STATUS_TEXT_MAP.get(task.status.value, task.status.value)
            if task.status == TaskStatus.QUEUED and task.retry_count > 0:
                return f"重试中 ({task.retry_count}/{task.max_retries})"
            return text
        return ""

    @staticmethod
    def _tooltip_text(task: DownloadTask) -> str:
        """生成整行的悬浮提示。"""
        lines = [f"标题：{task.display_name()}", f"链接：{task.url}"]
        if task.filepath:
            lines.append(f"文件：{task.filepath}")
        else:
            lines.append(f"目录：{task.output_dir}")
        if task.error_message:
            lines.append(f"错误：{task.error_message}")
        return "\n".join(lines)


__all__ = ["ROLE_TASK", "ROLE_TASK_ID", "DownloadTableModel"]
