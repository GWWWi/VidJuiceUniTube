"""表格单元格自绘委托。

- ``ProgressBarDelegate``：绘制进度条（合并中显示忙碌样式，失败显示红字）；
- ``ActionButtonDelegate``：按任务状态动态渲染操作按钮，并通过 ``editorEvent``
  捕获点击后 emit ``action_triggered(task_id, action)``。

使用自绘而非每行塞真实控件，可让上百条任务高频刷新时依然流畅。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionButton,
    QStyleOptionViewItem,
)

from src.constants import (
    ACTION_CANCEL,
    ACTION_OPEN_FOLDER,
    ACTION_PAUSE,
    ACTION_REMOVE,
    ACTION_RESUME,
    ACTION_RETRY,
    ACTION_TEXT_MAP,
    COLOR_DANGER,
    PHASE_MERGING,
)
from src.core.models import DownloadTask, TaskStatus
from src.ui.table_model import ROLE_TASK

logger = logging.getLogger(__name__)

#: 每个状态对应的操作按钮序列
STATUS_ACTIONS: dict[TaskStatus, tuple[str, ...]] = {
    TaskStatus.QUEUED: (ACTION_CANCEL,),
    TaskStatus.DOWNLOADING: (ACTION_PAUSE, ACTION_CANCEL),
    TaskStatus.MERGING: (ACTION_CANCEL,),
    TaskStatus.PAUSED: (ACTION_RESUME, ACTION_CANCEL),
    TaskStatus.FAILED: (ACTION_RETRY, ACTION_REMOVE),
    TaskStatus.CANCELED: (ACTION_RETRY, ACTION_REMOVE),
    TaskStatus.COMPLETED: (ACTION_OPEN_FOLDER, ACTION_REMOVE),
    TaskStatus.PARSING: (ACTION_CANCEL,),
}

_BUTTON_HEIGHT = 24
_BUTTON_SPACING = 6
_BUTTON_PADDING = 12


def _metrics(painter: QPainter | None) -> QFontMetrics:
    """返回可用于测量文字宽度的字体度量（无画笔时退化为应用默认字体）。"""
    return QFontMetrics(painter.font()) if painter else QFontMetrics(QApplication.font())


class ProgressBarDelegate(QStyledItemDelegate):
    """进度列自绘委托。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """绘制进度条。

        Args:
            painter: 画笔。
            option: 样式选项。
            index: 单元格索引。
        """
        task = index.data(ROLE_TASK)
        if not isinstance(task, DownloadTask):
            super().paint(painter, option, index)
            return

        # 先画选中背景
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)

        rect = option.rect.adjusted(6, 6, -6, -6)

        if task.status == TaskStatus.FAILED:
            self._paint_text(painter, rect, task.error_message or "失败", QColor(COLOR_DANGER))
            return

        bar = QStyleOptionProgressBar()
        bar.rect = rect
        bar.minimum = 0
        bar.textAlignment = Qt.AlignmentFlag.AlignCenter
        bar.textVisible = True

        percent = float(task.progress.percent or 0.0)
        is_busy = percent < 0 or task.status == TaskStatus.MERGING or task.progress.phase == PHASE_MERGING

        if is_busy:
            # 忙碌样式：Qt 约定 min == max == 0 时显示不确定进度
            bar.maximum = 0
            bar.progress = 0
            bar.text = "合并中…" if task.status == TaskStatus.MERGING else "处理中…"
        else:
            bar.maximum = 100
            bar.progress = int(max(0, min(100, percent)))
            bar.text = f"{percent:.1f}%"

        style.drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter, option.widget)

    @staticmethod
    def _paint_text(painter: QPainter, rect: QRect, text: str, color: QColor) -> None:
        """在指定区域绘制带省略的单行文字。"""
        painter.save()
        painter.setPen(QPen(color))
        metrics = QFontMetrics(painter.font())
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided)
        painter.restore()


class ActionButtonDelegate(QStyledItemDelegate):
    """操作列自绘委托：按状态渲染按钮并捕获点击。

    Signals:
        action_triggered: ``(str, str)`` —— ``task_id`` 与动作标识。
    """

    action_triggered = Signal(str, str)

    def __init__(self, parent: Any = None) -> None:
        """初始化委托。"""
        super().__init__(parent)
        self._hover_key: tuple[str, int] | None = None

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """绘制按钮组。"""
        task = index.data(ROLE_TASK)
        if not isinstance(task, DownloadTask):
            super().paint(painter, option, index)
            return

        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)

        actions = STATUS_ACTIONS.get(task.status, ())
        rects = self._button_rects(option.rect, actions, painter)

        for idx, action in enumerate(actions):
            if idx >= len(rects):
                break
            button = QStyleOptionButton()
            button.rect = rects[idx]
            button.text = ACTION_TEXT_MAP.get(action, action)
            button.state = QStyle.StateFlag.State_Enabled
            if self._hover_key == (task.task_id, idx):
                button.state |= QStyle.StateFlag.State_MouseOver
            style.drawControl(QStyle.ControlElement.CE_PushButton, button, painter, option.widget)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> Any:  # noqa: N802
        """返回建议尺寸，保证按钮不会被裁切。"""
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), _BUTTON_HEIGHT + 10))
        return size

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def editorEvent(  # noqa: N802
        self,
        event: QEvent,
        model: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """捕获鼠标事件，命中按钮时 emit ``action_triggered``。

        Args:
            event: 事件对象。
            model: 数据模型。
            option: 样式选项。
            index: 单元格索引。

        Returns:
            事件是否已被处理。
        """
        task = index.data(ROLE_TASK)
        if not isinstance(task, DownloadTask):
            return False

        event_type = event.type()
        if event_type not in (
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
        ):
            return False

        actions = STATUS_ACTIONS.get(task.status, ())
        if not actions:
            return False

        rects = self._button_rects(option.rect, actions, None)
        try:
            pos = event.position().toPoint()
        except AttributeError:  # 兼容旧版事件 API
            pos = event.pos()

        hit_index = -1
        for idx, rect in enumerate(rects):
            if rect.contains(pos):
                hit_index = idx
                break

        if event_type == QEvent.Type.MouseMove:
            new_key = (task.task_id, hit_index) if hit_index >= 0 else None
            if new_key != self._hover_key:
                self._hover_key = new_key
                if option.widget is not None:
                    option.widget.viewport().update()
            return hit_index >= 0

        if event_type == QEvent.Type.MouseButtonPress:
            return hit_index >= 0

        if event_type == QEvent.Type.MouseButtonRelease and hit_index >= 0:
            action = actions[hit_index]
            logger.debug("操作按钮点击：task=%s action=%s", task.task_id, action)
            self.action_triggered.emit(task.task_id, action)
            return True

        return False

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _button_rects(cell: QRect, actions: tuple[str, ...], painter: QPainter | None) -> list[QRect]:
        """计算每个按钮的矩形区域（水平居中排布）。

        Args:
            cell: 单元格矩形。
            actions: 动作列表。
            painter: 画笔（用于测量文字宽度；为 ``None`` 时用默认字体度量）。

        Returns:
            与 ``actions`` 等长的矩形列表。
        """
        if not actions:
            return []

        metrics = _metrics(painter)
        widths = [metrics.horizontalAdvance(ACTION_TEXT_MAP.get(a, a)) + _BUTTON_PADDING * 2 for a in actions]
        total = sum(widths) + _BUTTON_SPACING * (len(actions) - 1)

        start_x = cell.left() + max(4, (cell.width() - total) // 2)
        top = cell.top() + max(0, (cell.height() - _BUTTON_HEIGHT) // 2)

        rects: list[QRect] = []
        cursor = start_x
        for width in widths:
            rects.append(QRect(cursor, top, width, _BUTTON_HEIGHT))
            cursor += width + _BUTTON_SPACING
        return rects


__all__ = ["STATUS_ACTIONS", "ActionButtonDelegate", "ProgressBarDelegate"]
