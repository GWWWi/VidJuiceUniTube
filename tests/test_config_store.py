"""``src.core.config_store`` 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from src.constants import (
    ALL_CONFIG_KEYS,
    CFG_DOWNLOAD_DIR,
    CFG_MAX_CONCURRENT,
    DEFAULT_MAX_CONCURRENT,
    MAX_CONCURRENT_LIMIT,
    MIN_CONCURRENT,
)
from src.core.config_store import ConfigStore


class TestDefaults:
    """默认值相关测试。"""

    def test_defaults_contains_all_keys(self) -> None:
        """默认配置必须覆盖 ``ALL_CONFIG_KEYS`` 中的每一个 key。"""
        defaults = ConfigStore.defaults()
        for key in ALL_CONFIG_KEYS:
            assert key in defaults, f"缺少默认值：{key}"

    def test_defaults_download_dir_not_empty(self) -> None:
        """默认下载目录动态计算，不能为空。"""
        assert ConfigStore.defaults()[CFG_DOWNLOAD_DIR]


class TestLoadSave:
    """加载 / 保存相关测试。"""

    def test_load_creates_file_when_missing(self, config_path: str) -> None:
        """首次加载会写出默认配置文件。"""
        store = ConfigStore(config_path)
        data = store.load()
        assert Path(config_path).is_file()
        assert data[CFG_MAX_CONCURRENT] == DEFAULT_MAX_CONCURRENT

    def test_save_and_reload(self, config_path: str) -> None:
        """写入后重新加载能读到新值。"""
        store = ConfigStore(config_path)
        store.load()
        store.set_max_concurrent(5)
        store.save()

        reloaded = ConfigStore(config_path)
        reloaded.load()
        assert reloaded.max_concurrent() == 5

    def test_save_is_atomic_no_tmp_left(self, config_path: str) -> None:
        """原子写不应残留 ``.tmp`` 文件。"""
        store = ConfigStore(config_path)
        store.load()
        store.save()
        assert not Path(config_path + ".tmp").exists()

    def test_missing_keys_are_filled(self, config_path: str) -> None:
        """老版本配置缺 key 时用默认值补齐。"""
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config_path).write_text(json.dumps({CFG_MAX_CONCURRENT: 4}), encoding="utf-8")

        store = ConfigStore(config_path)
        data = store.load()

        assert data[CFG_MAX_CONCURRENT] == 4
        for key in ALL_CONFIG_KEYS:
            assert key in data

    def test_unknown_keys_are_preserved(self, config_path: str) -> None:
        """未知 key 保留，保证向前兼容。"""
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config_path).write_text(json.dumps({"future_key": 123}), encoding="utf-8")

        store = ConfigStore(config_path)
        assert store.load()["future_key"] == 123

    def test_corrupted_json_self_heals(self, config_path: str) -> None:
        """损坏的 JSON 会被备份为 ``.bak`` 并重建默认配置。"""
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config_path).write_text("{ this is not json", encoding="utf-8")

        store = ConfigStore(config_path)
        data = store.load()

        assert data[CFG_MAX_CONCURRENT] == DEFAULT_MAX_CONCURRENT
        assert Path(config_path + ".bak").is_file()
        # 重建后的文件必须是合法 JSON
        json.loads(Path(config_path).read_text(encoding="utf-8"))

    def test_non_dict_root_self_heals(self, config_path: str) -> None:
        """根节点不是对象时同样自愈。"""
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config_path).write_text("[1, 2, 3]", encoding="utf-8")

        store = ConfigStore(config_path)
        assert store.load()[CFG_MAX_CONCURRENT] == DEFAULT_MAX_CONCURRENT


class TestSemanticAccessors:
    """语义化 getter / setter 测试。"""

    def test_max_concurrent_clamped(self, config: ConfigStore) -> None:
        """并发数被限制在合法区间。"""
        config.set_max_concurrent(999)
        assert config.max_concurrent() == MAX_CONCURRENT_LIMIT

        config.set_max_concurrent(-5)
        assert config.max_concurrent() == MIN_CONCURRENT

        config.set_max_concurrent("abc")  # type: ignore[arg-type]
        assert config.max_concurrent() == DEFAULT_MAX_CONCURRENT

    def test_max_retries_clamped(self, config: ConfigStore) -> None:
        """重试次数被限制在合法区间。"""
        config.set_max_retries(100)
        assert config.max_retries() == 10
        config.set_max_retries(0)
        assert config.max_retries() == 0

    def test_empty_string_becomes_none(self, config: ConfigStore) -> None:
        """空串配置项对外暴露为 ``None``（yt-dlp 不接受空串）。"""
        config.set_cookiefile("")
        config.set_proxy("   ")
        config.set_ffmpeg_path("")
        assert config.cookiefile() is None
        assert config.proxy() is None
        assert config.ffmpeg_path() is None

    def test_non_empty_values_are_stripped(self, config: ConfigStore) -> None:
        """字符串值会去除首尾空白。"""
        config.set_proxy("  http://127.0.0.1:7890  ")
        assert config.proxy() == "http://127.0.0.1:7890"

    def test_download_dir_falls_back(self, config: ConfigStore) -> None:
        """下载目录为空时回落到默认目录。"""
        config.set_download_dir("")
        assert config.download_dir()

    def test_bool_and_text_accessors(self, config: ConfigStore) -> None:
        """布尔与文本配置项。"""
        config.set_auto_open_folder(True)
        assert config.auto_open_folder() is True

        config.set_default_container("mkv")
        assert config.default_container() == "mkv"

        config.set_theme("")
        assert config.theme() == "light"

        config.set_window_geometry("YWJj")
        assert config.window_geometry() == "YWJj"

    def test_reset(self, config: ConfigStore) -> None:
        """重置回默认值并落盘。"""
        config.set_max_concurrent(9)
        config.reset()
        assert config.max_concurrent() == DEFAULT_MAX_CONCURRENT

    def test_data_property_is_copy(self, config: ConfigStore) -> None:
        """``data`` 返回浅拷贝，外部修改不影响内部状态。"""
        snapshot = config.data
        snapshot[CFG_MAX_CONCURRENT] = 999
        assert config.max_concurrent() != 999
