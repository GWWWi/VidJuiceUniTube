"""Qt 工作线程包。

包内所有 ``QRunnable`` 只允许通过 ``WorkerSignals`` 与主线程通信，
**严禁**直接触碰任何 ``QWidget``。
"""

__all__: list[str] = []
