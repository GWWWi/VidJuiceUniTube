"""底部统计状态条。

展示：``进行中 N | 已完成 N | 失败 N | 总速度 X MB/s`` 以及一条临时提示文案。
所有数值格式化统一走 ``format_utils``。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from src.core.format_utils import human_speed


class StatusBarWidget(QFrame):
    """全局统计状态条。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """构建界面。"""
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._active = QLabel("进行中 0", self)
        self._queued = QLabel("排队 0", self)
        self._completed = QLabel("已完成 0", self)
        self._failed = QLabel("失败 0", self)
        self._speed = QLabel("总速度 --", self)
        self._message = QLabel("", self)
        self._message.setObjectName("statusMessage")

        for label in (self._active, self._queued, self._completed, self._failed, self._speed):
            label.setObjectName("statusItem")
            label.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(lambda: self._message.setText(""))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)
        layout.addWidget(self._active)
        layout.addWidget(self._queued)
        layout.addWidget(self._completed)
        layout.addWidget(self._failed)
        layout.addWidget(self._speed)
        layout.addStretch(1)
        layout.addWidget(self._message)

    def update_stats(self, stats: dict[str, Any]) -> None:
        """刷新统计数值。

        Args:
            stats: ``DownloadManager.global_stats()`` 的结果。
        """
        data = dict(stats or {})
        self._active.setText(f"进行中 {int(data.get('active', 0))}")
        self._queued.setText(f"排队 {int(data.get('queued', 0))}")
        self._completed.setText(f"已完成 {int(data.get('completed', 0))}")
        self._failed.setText(f"失败 {int(data.get('failed', 0))}")

        speed = float(data.get("total_speed", 0.0) or 0.0)
        self._speed.setText(f"总速度 {human_speed(speed) if speed > 0 else '--'}")

    def show_message(self, text: str, timeout_ms: int = 5000) -> None:
        """显示一条临时提示文案。

        Args:
            text: 提示内容。
            timeout_ms: 自动清除的毫秒数；``<=0`` 表示常驻。
        """
        self._message.setText(str(text or ""))
        self._message_timer.stop()
        if timeout_ms > 0:
            self._message_timer.start(int(timeout_ms))


__all__ = ["StatusBarWidget"]
