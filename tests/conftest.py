"""pytest 公共夹具。

设计原则：
- 所有涉及磁盘的夹具都写在 ``tmp_path`` 下，测试之间完全隔离；
- 不进行任何真实网络请求，yt-dlp 一律用 ``FakeYtDlpClient`` 替身；
- 需要 Qt 事件循环的测试统一复用一个进程级 ``QCoreApplication``。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest

# 保证可以 ``import src.*``（在未安装为包的情况下直接跑 pytest）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.config_store import ConfigStore  # noqa: E402
from src.core.history_store import HistoryStore  # noqa: E402
from src.core.models import DownloadTask, MediaFormat, MediaInfo  # noqa: E402


# ======================================================================
# 基础路径与配置
# ======================================================================
@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    """提供一个隔离的应用数据目录。"""
    target = tmp_path / "appdata"
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture()
def config_path(app_dir: Path) -> str:
    """临时配置文件路径。"""
    return str(app_dir / "config.json")


@pytest.fixture()
def download_dir(tmp_path: Path) -> str:
    """临时下载目录。"""
    target = tmp_path / "downloads"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


@pytest.fixture()
def config(config_path: str, download_dir: str) -> ConfigStore:
    """已加载的 ``ConfigStore``，下载目录指向临时目录。"""
    store = ConfigStore(config_path)
    store.load()
    store.set_download_dir(download_dir)
    store.set_max_concurrent(2)
    store.set_max_retries(2)
    return store


@pytest.fixture()
def history(app_dir: Path) -> HistoryStore:
    """已初始化的临时 ``HistoryStore``，测试结束自动关闭。"""
    store = HistoryStore(str(app_dir / "history.db"))
    store.init_db()
    yield store
    store.close()


# ======================================================================
# 领域对象工厂
# ======================================================================
@pytest.fixture()
def sample_format() -> MediaFormat:
    """一个典型的 1080p 纯视频格式（需要合并）。"""
    return MediaFormat(
        format_id="137",
        ext="mp4",
        resolution="1920x1080",
        height=1080,
        fps=30,
        filesize=134_217_728.0,
        vcodec="avc1.640028",
        acodec="none",
    )


@pytest.fixture()
def sample_info(sample_format: MediaFormat) -> MediaInfo:
    """一个典型的解析结果（首位为伪「最佳画质」）。"""
    audio = MediaFormat(
        format_id="140",
        ext="m4a",
        resolution="",
        height=None,
        fps=None,
        filesize=3_145_728.0,
        vcodec="none",
        acodec="mp4a.40.2",
    )
    return MediaInfo(
        url="https://example.com/watch?v=abc123",
        title="示例视频 Sample Video",
        uploader="示例频道",
        duration=245,
        thumbnail="https://example.com/thumb.jpg",
        extractor="Example",
        formats=[MediaFormat.make_best(), sample_format, audio],
    )


@pytest.fixture()
def sample_task(sample_info: MediaInfo, sample_format: MediaFormat, download_dir: str) -> DownloadTask:
    """一个排队中的下载任务。"""
    return DownloadTask.from_media(
        url=sample_info.url,
        title=sample_info.title,
        fmt=sample_format,
        output_dir=download_dir,
        max_retries=2,
        container="mp4",
    )


# ======================================================================
# yt-dlp 替身
# ======================================================================
class FakeYtDlpClient:
    """``YtDlpClient`` 的测试替身（不触网）。

    Attributes:
        info: ``extract_info`` 的返回值。
        parse_error: 不为 ``None`` 时 ``extract_info`` 抛出该异常。
        download_result: ``download`` 的返回路径。
        download_error: 不为 ``None`` 时 ``download`` 抛出该异常。
        calls: 记录调用次数，便于断言重试逻辑。
    """

    def __init__(self, info: MediaInfo | None = None) -> None:
        """初始化替身。"""
        self.info: MediaInfo | None = info
        self.parse_error: Exception | None = None
        self.download_result: str = ""
        self.download_error: Exception | None = None
        self.calls: dict[str, int] = {"extract_info": 0, "download": 0}
        self.on_download: Callable[[DownloadTask], None] | None = None

    def extract_info(self, url: str) -> MediaInfo:
        """模拟解析。"""
        self.calls["extract_info"] += 1
        if self.parse_error is not None:
            raise self.parse_error
        if self.info is None:
            return MediaInfo(url=url, title="fake", formats=[MediaFormat.make_best()])
        return self.info

    def download(self, task: DownloadTask, hook: Any, abort_flag: Any = None) -> str:
        """模拟下载。"""
        self.calls["download"] += 1
        if self.on_download is not None:
            self.on_download(task)
        if self.download_error is not None:
            raise self.download_error
        return self.download_result or str(Path(task.output_dir) / f"{task.title}.{task.ext}")

    @staticmethod
    def probe_error(exc: Exception, *, cookie_path: str | None = None) -> str:
        """直接透传异常文本（真实实现会翻译成中文）。

        签名与真实 ``YtDlpClient.probe_error`` 保持一致（含 cookie_path
        kwarg），避免工人侧调用 ``self._client.probe_error(...)`` 时因参数
        数量不匹配而抛 ``TypeError``。
        """
        return str(exc)


class FakeMerger:
    """``FfmpegMerger`` 的测试替身。"""

    def __init__(self, available: bool = True) -> None:
        """初始化替身。"""
        self._available = available
        self.merged: list[tuple[str, str, str]] = []

    def is_available(self) -> bool:
        """是否可用。"""
        return self._available

    def binary_path(self) -> str | None:
        """可执行文件路径。"""
        return "ffmpeg" if self._available else None

    def merge(self, video: str, audio: str, output: str) -> str:
        """记录一次合并并返回输出路径。"""
        self.merged.append((video, audio, output))
        return output

    def cleanup_parts(self, *paths: str) -> None:
        """空实现。"""
        return None


@pytest.fixture()
def fake_client(sample_info: MediaInfo) -> FakeYtDlpClient:
    """预置解析结果的 yt-dlp 替身。"""
    return FakeYtDlpClient(sample_info)


@pytest.fixture()
def fake_merger() -> FakeMerger:
    """ffmpeg 合并器替身。"""
    return FakeMerger(available=True)


# ======================================================================
# Qt 环境
# ======================================================================
@pytest.fixture(scope="session")
def qt_app():  # type: ignore[no-untyped-def]
    """进程级 ``QApplication``（Qt 不允许创建多个实例）。

    必须是 ``QApplication`` 而非 ``QCoreApplication``：后者无法承载 QWidget，
    一旦某个用例创建对话框（如 ``LoginBrowserDialog``），整个 pytest 进程会以
    ``0xC0000409``（STATUS_STACK_BUFFER_OVERRUN）**静默崩溃** —— 无 traceback、
    无 stderr，只剩一个非零退出码。且 ``QApplication.instance()`` 在只存在
    ``QCoreApplication`` 时返回的是那个对象（非 None），所以「没有就建一个」
    的防御写法救不了，必须从一开始就建 ``QApplication``。

    ``offscreen`` 平台插件保证在无桌面的 CI / 沙箱中同样可用。
    ``QApplication`` 是 ``QCoreApplication`` 的子类，现有依赖本夹具的用例
    （``manager`` 夹具链等）不受影响。
    """
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 Qt 相关测试")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    yield app


@pytest.fixture()
def manager(qt_app, config: ConfigStore, fake_client: FakeYtDlpClient, fake_merger: FakeMerger, history: HistoryStore):  # type: ignore[no-untyped-def]
    """注入替身依赖的 ``DownloadManager``，测试结束自动 shutdown。"""
    from src.core.download_manager import DownloadManager

    instance = DownloadManager(config, fake_client, fake_merger, history)
    yield instance
    instance.shutdown()
