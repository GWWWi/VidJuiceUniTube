"""子进程工具。

Windows 下直接调用 ``subprocess`` 会弹出黑色控制台窗口，本模块统一注入
``CREATE_NO_WINDOW`` 与 ``STARTUPINFO``，所有外部进程调用必须走这里。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, Sequence

logger = logging.getLogger(__name__)

#: Windows CREATE_NO_WINDOW 标志（非 Windows 平台为 0）
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform.startswith("win") else 0


def hidden_startupinfo() -> Any:
    """构建隐藏窗口的 ``STARTUPINFO``（非 Windows 平台返回 ``None``）。"""
    if not sys.platform.startswith("win"):
        return None
    startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    startupinfo.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return startupinfo


def hidden_kwargs(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回可直接展开给 ``subprocess`` 的「无窗口」关键字参数。

    Args:
        extra: 需要合并的额外关键字参数。

    Returns:
        含 ``creationflags`` / ``startupinfo`` 的关键字字典。
    """
    kwargs: dict[str, Any] = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["startupinfo"] = hidden_startupinfo()
    if extra:
        kwargs.update(extra)
    return kwargs


def run_hidden(
    cmd: Sequence[str],
    timeout: float | None = None,
    text: bool = True,
    check: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """以「无控制台窗口」方式运行外部命令并捕获输出。

    Args:
        cmd: 命令与参数列表。
        timeout: 超时秒数，``None`` 表示不限制。
        text: 是否以文本模式解码 stdout/stderr。
        check: 为 ``True`` 时非零退出码抛 ``CalledProcessError``。
        **kwargs: 透传给 ``subprocess.run`` 的其他参数。

    Returns:
        ``subprocess.CompletedProcess`` 对象。

    Raises:
        subprocess.TimeoutExpired: 超时。
        subprocess.CalledProcessError: ``check=True`` 且退出码非零。
    """
    run_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": text,
        "timeout": timeout,
        "check": check,
    }
    if text:
        run_kwargs["encoding"] = "utf-8"
        run_kwargs["errors"] = "replace"
    run_kwargs.update(hidden_kwargs(kwargs))

    logger.debug("执行外部命令：%s", " ".join(str(part) for part in cmd))
    return subprocess.run(list(cmd), **run_kwargs)  # noqa: S603


def popen_hidden(cmd: Sequence[str], **kwargs: Any) -> subprocess.Popen:
    """以「无控制台窗口」方式启动一个后台进程（不等待结束）。

    Args:
        cmd: 命令与参数列表。
        **kwargs: 透传给 ``subprocess.Popen`` 的其他参数。

    Returns:
        ``subprocess.Popen`` 对象。
    """
    popen_kwargs = hidden_kwargs(kwargs)
    return subprocess.Popen(list(cmd), **popen_kwargs)  # noqa: S603


__all__ = ["CREATE_NO_WINDOW", "hidden_kwargs", "hidden_startupinfo", "popen_hidden", "run_hidden"]
