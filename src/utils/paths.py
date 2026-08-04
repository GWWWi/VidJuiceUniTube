"""路径工具。

统一处理开发态与 PyInstaller 打包态的资源定位，以及应用数据目录、
默认下载目录、资源管理器定位、文件名净化。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from src.constants import (
    APP_NAME,
    CONFIG_FILENAME,
    DEFAULT_DOWNLOAD_SUBDIR,
    HISTORY_DB_FILENAME,
    LOG_FILENAME,
    LOG_SUBDIR,
)

logger = logging.getLogger(__name__)

#: Windows 文件名非法字符
_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Windows 保留设备名（不区分大小写）
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def project_root() -> Path:
    """返回项目根目录（开发态为仓库根，打包态为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 本文件位于 <root>/src/utils/paths.py
    return Path(__file__).resolve().parents[2]


def resource_path(rel: str) -> str:
    """把相对资源路径解析为绝对路径，兼容 PyInstaller 单文件模式。

    Args:
        rel: 相对于项目根（或 bundle 根）的路径，如 ``"src/ui/style.qss"``。

    Returns:
        绝对路径字符串（不保证文件一定存在，由调用方判断）。
    """
    rel_norm = str(rel).replace("\\", "/").lstrip("/")

    # PyInstaller 单文件模式：资源解压到 sys._MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / rel_norm
        if candidate.exists():
            return str(candidate)
        # 某些资源可能被放在 exe 同级目录（如外置 vendor）
        side = Path(sys.executable).resolve().parent / rel_norm
        if side.exists():
            return str(side)
        return str(candidate)

    return str(project_root() / rel_norm)


def app_data_dir() -> str:
    """返回应用数据目录 ``%APPDATA%/VidJuice``，不存在时自动创建。

    非 Windows 平台退化为 ``~/.vidjuice``，保证测试可跨平台运行。
    """
    base = os.environ.get("APPDATA")
    if base:
        target = Path(base) / APP_NAME
    else:
        target = Path.home() / f".{APP_NAME.lower()}"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def config_file_path() -> str:
    """返回配置文件绝对路径 ``%APPDATA%/VidJuice/config.json``。"""
    return str(Path(app_data_dir()) / CONFIG_FILENAME)


def history_db_path() -> str:
    """返回历史记录数据库绝对路径 ``%APPDATA%/VidJuice/history.db``。"""
    return str(Path(app_data_dir()) / HISTORY_DB_FILENAME)


def log_file_path() -> str:
    """返回日志文件绝对路径，并确保日志目录已创建。"""
    log_dir = Path(app_data_dir()) / LOG_SUBDIR
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / LOG_FILENAME)


def default_download_dir() -> str:
    """返回默认下载目录 ``%USERPROFILE%/Videos/VidJuice``，不存在时自动创建。"""
    target = Path.home() / DEFAULT_DOWNLOAD_SUBDIR
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # 极端情况下（只读盘）退化到临时目录
        logger.warning("创建默认下载目录失败：%s，退化到用户主目录", exc)
        return str(Path.home())
    return str(target)


def ensure_dir(path: str) -> str:
    """确保目录存在并返回其绝对路径。"""
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return str(target.resolve())


def open_in_explorer(path: str) -> bool:
    """在资源管理器中打开并选中指定文件；若为目录则直接打开该目录。

    Args:
        path: 文件或目录路径。

    Returns:
        是否成功发起打开操作。
    """
    if not path:
        return False

    target = Path(path)
    try:
        if target.is_file():
            # /select 需要反斜杠路径；explorer 的退出码不可靠，故不检查返回值
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(str(target))}"])
            return True

        folder = target if target.is_dir() else target.parent
        if not folder.exists():
            logger.warning("目录不存在，无法打开：%s", folder)
            return False

        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return True
    except OSError as exc:
        logger.warning("打开资源管理器失败：%s", exc)
        return False


def open_file(path: str) -> bool:
    """用系统默认程序打开文件。

    Args:
        path: 文件路径。

    Returns:
        是否成功发起打开操作。
    """
    target = Path(path)
    if not target.is_file():
        logger.warning("文件不存在，无法打开：%s", path)
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except OSError as exc:
        logger.warning("打开文件失败：%s", exc)
        return False


def sanitize_filename(name: str, max_length: int = 150) -> str:
    """净化文件名，移除 Windows 非法字符并限制长度。

    Args:
        name: 原始文件名（不含目录）。
        max_length: 最大长度（字符数），默认 150。

    Returns:
        安全的文件名；若净化后为空则返回 ``"untitled"``。
    """
    if not name:
        return "untitled"

    cleaned = _ILLEGAL_CHARS_RE.sub("_", str(name))
    cleaned = cleaned.strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)

    if not cleaned:
        return "untitled"

    stem, dot, ext = cleaned.rpartition(".")
    if dot and len(ext) <= 5 and stem:
        if stem.upper() in _RESERVED_NAMES:
            stem = f"_{stem}"
        cleaned = f"{stem}.{ext}"
    elif cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 5:
            keep = max(1, max_length - len(ext) - 1)
            cleaned = f"{stem[:keep]}.{ext}"
        else:
            cleaned = cleaned[:max_length]

    return cleaned or "untitled"


__all__ = [
    "app_data_dir",
    "config_file_path",
    "default_download_dir",
    "ensure_dir",
    "history_db_path",
    "log_file_path",
    "open_file",
    "open_in_explorer",
    "project_root",
    "resource_path",
    "sanitize_filename",
]
