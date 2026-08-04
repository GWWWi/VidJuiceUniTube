"""跨线程信号定义（唯一真源）。

所有 Worker → 主线程的信号都在此集中定义，**禁止**在各 Worker 内散落定义。
信号参数只使用可序列化的基础类型（``str`` / ``int`` / ``float`` / ``dict``），
``MediaInfo`` 例外：它是一次性只读对象，用 ``object`` 传递。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class WorkerSignals(QObject):
    """工作线程统一信号载体。

    Signals:
        parse_finished: ``(object)`` —— 解析成功，携带 ``MediaInfo``。
        parse_failed: ``(str, str)`` —— 解析失败，携带 ``url`` 与中文错误消息。
        task_progress: ``(str, dict)`` —— 进度更新，携带 ``task_id`` 与
            ``ProgressInfo.to_dict()`` 结果。
        task_status_changed: ``(str, str)`` —— 状态变更，携带 ``task_id`` 与
            ``TaskStatus.value``。
        task_finished: ``(str, str)`` —— 下载完成，携带 ``task_id`` 与文件路径。
        task_failed: ``(str, str)`` —— 下载失败，携带 ``task_id`` 与中文错误消息。
        log_message: ``(str)`` —— 需要展示到 UI 的日志行。
    """

    parse_finished = Signal(object)
    parse_failed = Signal(str, str)
    task_progress = Signal(str, dict)
    task_status_changed = Signal(str, str)
    task_finished = Signal(str, str)
    task_failed = Signal(str, str)
    log_message = Signal(str)


__all__ = ["WorkerSignals"]
