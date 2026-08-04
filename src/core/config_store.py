"""配置读写。

存放位置：``%APPDATA%/VidJuice/config.json``（UTF-8）。

特性：
- 缺失 key 自动用默认值补齐（向前兼容）；
- JSON 损坏时备份为 ``.bak`` 并重建默认配置（自愈）；
- ``save()`` 采用原子写（先写 ``.tmp`` 再 ``os.replace``），避免半截文件；
- 提供语义化 getter，UI 层禁止直接使用裸字符串 key。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from src.constants import (
    CFG_AUTO_OPEN_FOLDER,
    CFG_COOKIEFILE,
    CFG_DEFAULT_CONTAINER,
    CFG_DEFAULT_QUALITY,
    CFG_DOWNLOAD_DIR,
    CFG_FFMPEG_PATH,
    CFG_MAX_CONCURRENT,
    CFG_MAX_RETRIES,
    CFG_PROXY,
    CFG_THEME,
    CFG_WINDOW_GEOMETRY,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RETRIES,
    MAX_CONCURRENT_LIMIT,
    MAX_RETRIES_LIMIT,
    MIN_CONCURRENT,
    MIN_RETRIES,
)
from src.utils.paths import config_file_path, default_download_dir

logger = logging.getLogger(__name__)


def _clamp(value: int, low: int, high: int) -> int:
    """把整数限制在 ``[low, high]`` 区间内。"""
    return max(low, min(high, value))


class ConfigStore:
    """应用配置存取器。

    Attributes:
        DEFAULTS: 默认配置字典（每次访问动态构建，避免默认下载目录被缓存）。
    """

    #: 静态默认值（``download_dir`` 在 ``defaults()`` 中动态填充）
    DEFAULTS: dict[str, Any] = {
        CFG_DOWNLOAD_DIR: "",
        CFG_MAX_CONCURRENT: DEFAULT_MAX_CONCURRENT,
        CFG_MAX_RETRIES: DEFAULT_MAX_RETRIES,
        CFG_DEFAULT_QUALITY: "best",
        CFG_DEFAULT_CONTAINER: "mp4",
        CFG_COOKIEFILE: "",
        CFG_FFMPEG_PATH: "",
        CFG_PROXY: "",
        CFG_AUTO_OPEN_FOLDER: False,
        CFG_THEME: "light",
        CFG_WINDOW_GEOMETRY: "",
    }

    def __init__(self, path: str | None = None) -> None:
        """初始化配置存取器。

        Args:
            path: 配置文件路径；``None`` 表示使用 ``%APPDATA%/VidJuice/config.json``。
        """
        self._path: str = path or config_file_path()
        self._data: dict[str, Any] = self.defaults()

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    @classmethod
    def defaults(cls) -> dict[str, Any]:
        """构建一份全新的默认配置（含动态计算的默认下载目录）。"""
        data = dict(cls.DEFAULTS)
        data[CFG_DOWNLOAD_DIR] = default_download_dir()
        return data

    @property
    def path(self) -> str:
        """配置文件绝对路径。"""
        return self._path

    @property
    def data(self) -> dict[str, Any]:
        """当前配置的浅拷贝（防止外部直接篡改内部字典）。"""
        return dict(self._data)

    def load(self) -> dict[str, Any]:
        """从磁盘加载配置；文件不存在或损坏时自愈为默认配置。

        Returns:
            加载后的配置字典。
        """
        defaults = self.defaults()
        config_path = Path(self._path)

        if not config_path.is_file():
            logger.info("配置文件不存在，使用默认配置：%s", self._path)
            self._data = defaults
            self.save()
            return self.data

        try:
            raw = config_path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError("配置根节点不是对象")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("配置文件损坏（%s），已备份并重建默认配置", exc)
            self._backup_corrupted()
            self._data = defaults
            self.save()
            return self.data

        # 缺 key 用默认值补齐；多余 key 保留（向前兼容未来版本）
        merged = dict(defaults)
        merged.update({str(k): v for k, v in loaded.items()})
        self._data = merged
        logger.info("配置加载完成：%s", self._path)
        return self.data

    def save(self) -> None:
        """原子方式保存配置到磁盘（失败只记录日志，不抛异常）。"""
        config_path = Path(self._path)
        tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(str(tmp_path), str(config_path))
            logger.debug("配置已保存：%s", self._path)
        except OSError as exc:
            logger.error("保存配置失败：%s", exc)
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _backup_corrupted(self) -> None:
        """把损坏的配置文件备份为 ``config.json.bak``。"""
        try:
            src = Path(self._path)
            dst = src.with_suffix(src.suffix + ".bak")
            shutil.copy2(str(src), str(dst))
            logger.info("损坏配置已备份到：%s", dst)
        except OSError as exc:
            logger.warning("备份损坏配置失败：%s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置项。

        Args:
            key: 配置 key（应使用 ``constants`` 中的常量）。
            default: 键不存在时的兜底值；为 ``None`` 时回落到内置默认值。

        Returns:
            配置值。
        """
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return self.defaults().get(key)

    def set(self, key: str, value: Any) -> None:
        """写入配置项（仅更新内存，需显式调用 ``save()`` 落盘）。"""
        self._data[str(key)] = value

    def update(self, values: dict[str, Any]) -> None:
        """批量写入配置项。"""
        for key, value in (values or {}).items():
            self.set(key, value)

    def reset(self) -> None:
        """重置为默认配置并立即保存。"""
        self._data = self.defaults()
        self.save()

    # ------------------------------------------------------------------
    # 语义化 getter / setter
    # ------------------------------------------------------------------
    def download_dir(self) -> str:
        """下载目录（为空时回落到默认目录）。"""
        value = str(self.get(CFG_DOWNLOAD_DIR) or "").strip()
        return value or default_download_dir()

    def set_download_dir(self, value: str) -> None:
        """设置下载目录。"""
        self.set(CFG_DOWNLOAD_DIR, str(value or "").strip())

    def max_concurrent(self) -> int:
        """最大并发下载数（限制在 1–10）。"""
        try:
            value = int(self.get(CFG_MAX_CONCURRENT) or DEFAULT_MAX_CONCURRENT)
        except (TypeError, ValueError):
            value = DEFAULT_MAX_CONCURRENT
        return _clamp(value, MIN_CONCURRENT, MAX_CONCURRENT_LIMIT)

    def set_max_concurrent(self, value: int) -> None:
        """设置最大并发下载数。"""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = DEFAULT_MAX_CONCURRENT
        self.set(CFG_MAX_CONCURRENT, _clamp(parsed, MIN_CONCURRENT, MAX_CONCURRENT_LIMIT))

    def max_retries(self) -> int:
        """失败自动重试次数（限制在 0–10）。"""
        try:
            value = int(self.get(CFG_MAX_RETRIES) or 0)
        except (TypeError, ValueError):
            value = DEFAULT_MAX_RETRIES
        return _clamp(value, MIN_RETRIES, MAX_RETRIES_LIMIT)

    def set_max_retries(self, value: int) -> None:
        """设置失败自动重试次数。"""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = DEFAULT_MAX_RETRIES
        self.set(CFG_MAX_RETRIES, _clamp(parsed, MIN_RETRIES, MAX_RETRIES_LIMIT))

    def default_quality(self) -> str:
        """默认画质选项标识。"""
        return str(self.get(CFG_DEFAULT_QUALITY) or "best")

    def set_default_quality(self, value: str) -> None:
        """设置默认画质选项标识。"""
        self.set(CFG_DEFAULT_QUALITY, str(value or "best"))

    def default_container(self) -> str:
        """默认容器格式。"""
        return str(self.get(CFG_DEFAULT_CONTAINER) or "mp4")

    def set_default_container(self, value: str) -> None:
        """设置默认容器格式。"""
        self.set(CFG_DEFAULT_CONTAINER, str(value or "mp4"))

    def cookiefile(self) -> str | None:
        """cookie 文件路径；未配置返回 ``None``（yt-dlp 不接受空串）。"""
        value = str(self.get(CFG_COOKIEFILE) or "").strip()
        return value or None

    def set_cookiefile(self, value: str | None) -> None:
        """设置 cookie 文件路径。"""
        self.set(CFG_COOKIEFILE, str(value or "").strip())

    def ffmpeg_path(self) -> str | None:
        """ffmpeg 可执行文件路径；未配置返回 ``None``（自动查找）。"""
        value = str(self.get(CFG_FFMPEG_PATH) or "").strip()
        return value or None

    def set_ffmpeg_path(self, value: str | None) -> None:
        """设置 ffmpeg 路径。"""
        self.set(CFG_FFMPEG_PATH, str(value or "").strip())

    def proxy(self) -> str | None:
        """代理地址；未配置返回 ``None``。"""
        value = str(self.get(CFG_PROXY) or "").strip()
        return value or None

    def set_proxy(self, value: str | None) -> None:
        """设置代理地址。"""
        self.set(CFG_PROXY, str(value or "").strip())

    def auto_open_folder(self) -> bool:
        """完成后是否自动打开所在目录。"""
        return bool(self.get(CFG_AUTO_OPEN_FOLDER, False))

    def set_auto_open_folder(self, value: bool) -> None:
        """设置完成后是否自动打开目录。"""
        self.set(CFG_AUTO_OPEN_FOLDER, bool(value))

    def theme(self) -> str:
        """主题标识（本版仅 ``light``）。"""
        return str(self.get(CFG_THEME) or "light")

    def set_theme(self, value: str) -> None:
        """设置主题标识。"""
        self.set(CFG_THEME, str(value or "light"))

    def window_geometry(self) -> str:
        """窗口几何（base64 字符串，空表示未保存）。"""
        return str(self.get(CFG_WINDOW_GEOMETRY) or "")

    def set_window_geometry(self, value: str) -> None:
        """设置窗口几何。"""
        self.set(CFG_WINDOW_GEOMETRY, str(value or ""))


__all__ = ["ConfigStore"]
