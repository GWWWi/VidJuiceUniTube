"""全模块导入冒烟测试（无 GUI）。

这层测试存在的理由：单元测试只覆盖 ``src/core`` 与 ``src/workers``，
``src/ui`` 完全没有测试，导致「模块级 import 写错类名」这类**启动即崩溃**
的错误可以一路漏到用户手里（历史事故：``QStyleOptionViewButton`` 在 Qt6 中
并不存在，正确类名为 ``QStyleOptionButton``，致使 ``main.py`` 构建主窗口时
直接 ImportError）。

本文件只做 ``import``，**不实例化任何 QWidget、不启动事件循环**，
因此在无桌面显示的 CI / 沙箱环境中同样可以安全运行。
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from pathlib import Path

import pytest

# 无显示环境下强制使用 offscreen 平台插件，避免 Qt 尝试连接显示服务器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _iter_module_names() -> list[str]:
    """收集 ``src`` 下所有子模块的完整点分名称。"""
    names: list[str] = ["src.constants", "src.app", "src.main"]
    for package in ("src.core", "src.workers", "src.ui", "src.utils"):
        module = importlib.import_module(package)
        for info in pkgutil.iter_modules(module.__path__):
            names.append(f"{package}.{info.name}")
    return sorted(names)


@pytest.mark.parametrize("module_name", _iter_module_names())
def test_module_imports_cleanly(module_name: str) -> None:
    """每个模块都必须能够独立导入（覆盖类名拼写 / 循环依赖 / 缺失符号）。"""
    try:
        importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - 失败时给出可定位的信息
        pytest.fail(f"模块 {module_name} 导入失败（很可能是类名写错或循环依赖）：{exc}")


def test_startup_path_is_importable() -> None:
    """``main.py`` 启动链路上的关键模块必须可导入，否则应用启动即崩溃。"""
    for module_name in ("src.main", "src.app", "src.ui.main_window"):
        importlib.import_module(module_name)


def test_worker_signal_contract() -> None:
    """``WorkerSignals`` 的信号名与参数必须与设计文档第 8.1 节完全一致。"""
    from src.workers.signals import WorkerSignals

    expected = {
        "parse_finished": 1,
        "parse_failed": 2,
        "task_progress": 2,
        "task_status_changed": 2,
        "task_finished": 2,
        "task_failed": 2,
        "log_message": 1,
    }
    for name in expected:
        assert hasattr(WorkerSignals, name), f"WorkerSignals 缺少约定信号：{name}"


def test_download_manager_signal_contract() -> None:
    """``DownloadManager`` 必须提供设计文档第 3.1 节约定的对外信号。"""
    from src.core.download_manager import DownloadManager

    for name in ("parse_ready", "parse_error", "task_added", "task_updated", "stats_updated"):
        assert hasattr(DownloadManager, name), f"DownloadManager 缺少约定信号：{name}"
