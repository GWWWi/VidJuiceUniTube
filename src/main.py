"""应用入口。

启动流程：

1. 初始化日志与全局异常钩子；
2. 创建 ``QApplication`` 并加载 QSS 样式（缺失时降级为 Qt 默认样式）；
3. 通过 ``build_app()`` 装配核心对象图（Config / History / Client / Merger / Manager）；
4. 构建 ``MainWindow`` 并显示；
5. 进入 Qt 事件循环，退出时由 ``MainWindow.closeEvent`` 负责收尾。

运行方式（项目根目录）::

    python -m src.main
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from src.constants import (
    APP_DISPLAY_NAME,
    APP_ICON_REL,
    APP_NAME,
    APP_VERSION,
    ORG_DOMAIN,
    ORG_NAME,
    STYLE_QSS_REL,
)
from src.utils.logger import install_excepthook, setup_logging
from src.utils.paths import resource_path

logger = logging.getLogger(__name__)


def load_stylesheet() -> str:
    """读取 QSS 样式表内容。

    Returns:
        样式表文本；文件缺失或读取失败时返回空字符串（界面降级为 Qt 默认样式）。
    """
    qss_path = resource_path(STYLE_QSS_REL)
    if not os.path.isfile(qss_path):
        logger.warning("样式表不存在，使用 Qt 默认样式：%s", qss_path)
        return ""
    try:
        with open(qss_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning("样式表读取失败，使用 Qt 默认样式：%s", exc)
        return ""


def create_application(argv: list[str]) -> QApplication:
    """创建并配置 ``QApplication``。

    Args:
        argv: 命令行参数。

    Returns:
        已完成基础配置（元信息 / 图标 / 样式）的应用实例。
    """
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(ORG_DOMAIN)
    # 关闭最后一个窗口即退出（托盘图标不应阻止退出）
    app.setQuitOnLastWindowClosed(True)

    icon = QIcon(resource_path(APP_ICON_REL))
    if not icon.isNull():
        app.setWindowIcon(icon)

    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    return app


def main(argv: list[str] | None = None) -> int:
    """程序主入口。

    Args:
        argv: 命令行参数；``None`` 表示使用 ``sys.argv``。

    Returns:
        进程退出码。
    """
    args = list(argv if argv is not None else sys.argv)

    setup_logging(level=logging.INFO, to_file=True)
    install_excepthook()
    logger.info("=" * 60)
    logger.info("%s v%s 启动", APP_DISPLAY_NAME, APP_VERSION)

    app = create_application(args)

    # 延迟导入：确保 QApplication 已创建后再构造任何 QWidget 子类
    from src.app import build_app
    from src.ui.main_window import MainWindow

    try:
        config, manager = build_app()
    except Exception as exc:  # noqa: BLE001 - 启动失败需给用户可见提示
        logger.critical("应用初始化失败：%s", exc, exc_info=True)
        QMessageBox.critical(None, "启动失败", f"应用初始化失败：\n{exc}")
        return 1

    window = MainWindow(manager, config)
    window.show()

    logger.info("主窗口已显示，进入事件循环")
    exit_code = app.exec()
    logger.info("%s 已退出（code=%d）", APP_DISPLAY_NAME, exit_code)
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
