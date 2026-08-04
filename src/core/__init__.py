"""业务核心包。

约定：本包内**禁止** import ``PySide6.QtWidgets``；
仅 ``download_manager.py`` 允许使用 ``PySide6.QtCore``（信号与线程池）。
其余模块保持纯 Python，可脱离 Qt 单测。
"""

__all__: list[str] = []
