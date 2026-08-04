"""``src.core.ytdlp_client`` 与 ``src.core.ffmpeg_merger`` 单元测试。

全部基于 mock：通过向 ``sys.modules`` 注入伪 ``yt_dlp`` 模块拦截真实网络请求。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from src.constants import BEST_FORMAT_ID, OUTPUT_TEMPLATE
from src.core.config_store import ConfigStore
from src.core.exceptions import DownloadAbort, MergeError, ParseError
from src.core.ffmpeg_merger import FfmpegMerger
from src.core.models import DownloadTask, MediaFormat
from src.core.ytdlp_client import YtDlpClient, is_non_retryable

# ======================================================================
# 伪 yt_dlp
# ======================================================================
_RAW_INFO: dict[str, Any] = {
    "webpage_url": "https://example.com/watch?v=abc123",
    "title": "示例视频",
    "uploader": "示例频道",
    "duration": 245.7,
    "thumbnail": "https://example.com/t.jpg",
    "extractor_key": "Example",
    "formats": [
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none", "format_note": "storyboard"},
        {"format_id": "", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 720},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "fps": 30,
         "filesize": 1000},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 720, "fps": 30},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 100},
        "not-a-dict",
    ],
}


class _FakeYoutubeDL:
    """伪 ``YoutubeDL``，记录构造 opts 并返回预设 info。"""

    #: 最近一次构造使用的 opts（供断言）
    last_opts: dict[str, Any] = {}
    #: ``extract_info`` 的返回值
    result: Any = _RAW_INFO
    #: 不为 ``None`` 时 ``extract_info`` 抛出该异常
    error: Exception | None = None

    def __init__(self, opts: dict[str, Any] | None = None) -> None:
        """记录 opts。"""
        type(self).last_opts = dict(opts or {})
        self._opts = dict(opts or {})

    def __enter__(self) -> "_FakeYoutubeDL":
        """上下文管理器入口。"""
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        """上下文管理器出口（不吞异常）。"""
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        """返回预设结果或抛出预设异常。"""
        if type(self).error is not None:
            raise type(self).error
        return type(self).result

    def prepare_filename(self, info: dict[str, Any]) -> str:
        """返回一个推断出的文件名。"""
        return str(info.get("_expected_name") or "out.mp4")


@pytest.fixture(autouse=True)
def fake_ytdlp_module(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """向 ``sys.modules`` 注入伪 ``yt_dlp``，并在每个用例后复位状态。"""
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = _FakeYoutubeDL  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", module)

    _FakeYoutubeDL.result = _RAW_INFO
    _FakeYoutubeDL.error = None
    _FakeYoutubeDL.last_opts = {}
    yield module
    _FakeYoutubeDL.result = _RAW_INFO
    _FakeYoutubeDL.error = None


@pytest.fixture()
def client(config: ConfigStore) -> YtDlpClient:
    """基于临时配置的客户端。"""
    return YtDlpClient(config)


# ======================================================================
# build_opts
# ======================================================================
class TestBuildOpts:
    """选项构建测试。"""

    def test_base_opts(self, client: YtDlpClient) -> None:
        """基础选项必须包含 Windows 文件名与静默设置。"""
        opts = client.build_opts()
        assert opts["windowsfilenames"] is True
        assert opts["noplaylist"] is True
        assert opts["quiet"] is True
        assert opts["ignoreerrors"] is False
        assert "logger" in opts

    def test_empty_config_values_are_omitted(self, client: YtDlpClient, config: ConfigStore) -> None:
        """空的 cookie / proxy 不能写入 opts（yt-dlp 不接受空串）。"""
        config.set_cookiefile("")
        config.set_proxy("")
        opts = client.build_opts()
        assert "cookiefile" not in opts
        assert "proxy" not in opts

    def test_non_empty_config_values_applied(self, client: YtDlpClient, config: ConfigStore, tmp_path: Path) -> None:
        """已配置的 cookie / proxy 会写入 opts。"""
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# netscape", encoding="utf-8")
        config.set_cookiefile(str(cookie))
        config.set_proxy("http://127.0.0.1:7890")

        opts = client.build_opts()
        assert opts["cookiefile"] == str(cookie)
        assert opts["proxy"] == "http://127.0.0.1:7890"

    def test_extra_overrides_base(self, client: YtDlpClient) -> None:
        """extra 覆盖同名基础项。"""
        opts = client.build_opts({"quiet": False, "format": "137"})
        assert opts["quiet"] is False
        assert opts["format"] == "137"

    def test_youtube_player_client_fallback(self, client: YtDlpClient) -> None:
        """基础 opts 带 YouTube player_client 兜底（无 cookie 时绕年龄限制）。

        使用 ``default`` 而非硬编码 ``web``：``default`` 由 yt-dlp 展开为其
        当前推荐客户端集合，随版本演进自动跟进；``tv`` / ``ios`` 为兜底。
        """
        opts = client.build_opts()
        assert "extractor_args" in opts
        youtube = opts["extractor_args"]["youtube"]
        player_client = youtube["player_client"]
        # 不再硬编码 "web"，交由 yt-dlp 的 "default" 展开
        assert "default" in player_client
        assert "tv" in player_client
        assert "ios" in player_client
        assert player_client[0] == "default"

    def test_extractor_args_overridden_by_extra(self, client: YtDlpClient) -> None:
        """外部 extra 已设置 extractor_args 时，兜底被覆盖而非叠加。"""
        opts = client.build_opts({"extractor_args": {"youtube": {"player_client": ["web"]}}})
        assert opts["extractor_args"]["youtube"]["player_client"] == ["web"]

    def test_non_youtube_untouched_by_extractor_args(self, client: YtDlpClient) -> None:
        """extractor_args 按 extractor 名分桶，不影响非 YouTube 站点解析。"""
        opts = client.build_opts()
        # 仅含 youtube key，其他站点（bilibili 等）不会读取该 key
        assert set(opts["extractor_args"].keys()) == {"youtube"}


# ======================================================================
# extract_info
# ======================================================================
class TestExtractInfo:
    """解析测试。"""

    def test_rejects_empty_url(self, client: YtDlpClient) -> None:
        """空链接直接抛 ``ParseError``。"""
        with pytest.raises(ParseError):
            client.extract_info("   ")

    def test_normalizes_formats(self, client: YtDlpClient) -> None:
        """无效项被过滤，剩余按画质降序且首位是伪「最佳画质」。"""
        info = client.extract_info("https://example.com/watch?v=abc123")

        assert info.title == "示例视频"
        assert info.uploader == "示例频道"
        assert info.duration == 245
        assert info.extractor == "Example"

        # storyboard / 无 format_id / 非 dict 均被过滤，剩 137 / 136 / 140，加上伪最佳共 4 个
        assert len(info.formats) == 4
        assert info.formats[0].format_id == BEST_FORMAT_ID
        assert info.formats[1].format_id == "137"
        assert info.formats[2].format_id == "136"

    def test_skip_download_opt(self, client: YtDlpClient) -> None:
        """解析时必须带 ``skip_download``。"""
        client.extract_info("https://example.com/x")
        assert _FakeYoutubeDL.last_opts["skip_download"] is True

    def test_parse_ignores_format_selection_failure(self, client: YtDlpClient) -> None:
        """解析时必须设 ``ignore_no_formats_error=True``。

        背景：yt-dlp 的 ``process_video_result`` 在 ``extract_info`` 路径也会
        隐式调用，会用 ``bestvideo*+bestaudio/best`` 做一次格式选择。视频
        受 SABR-only / 缺 PO Token 等影响时该选择会失败，抛
        ``Requested format is not available``，导致用户连画质下拉都看不到。
        设该标志后 yt-dlp 降级为 warning，仍然返回完整 formats 列表。
        """
        client.extract_info("https://example.com/x")
        assert _FakeYoutubeDL.last_opts["ignore_no_formats_error"] is True

    def test_download_does_not_set_ignore_no_formats_error(self, client: YtDlpClient) -> None:
        """下载路径**不应**设该标志——我们需要明确的失败信号来判断重试/降级。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            task = DownloadTask.from_media(
                url="https://example.com/x",
                title="t",
                fmt=MediaFormat(format_id="137", ext="mp4", height=1080),
                output_dir=tmp,
                max_retries=0,
            )
            client.download(task, lambda _d: None)
        assert "ignore_no_formats_error" not in _FakeYoutubeDL.last_opts

    def test_playlist_takes_first_entry(self, client: YtDlpClient) -> None:
        """播放列表只取第一个有效条目。"""
        _FakeYoutubeDL.result = {"entries": [None, dict(_RAW_INFO, title="第一个")]}
        info = client.extract_info("https://example.com/list")
        assert info.title == "第一个"

    def test_empty_playlist_raises(self, client: YtDlpClient) -> None:
        """空播放列表抛错。"""
        _FakeYoutubeDL.result = {"entries": [None, None]}
        with pytest.raises(ParseError):
            client.extract_info("https://example.com/list")

    def test_empty_result_raises(self, client: YtDlpClient) -> None:
        """空返回抛错。"""
        _FakeYoutubeDL.result = None
        with pytest.raises(ParseError):
            client.extract_info("https://example.com/x")

    def test_underlying_error_is_translated(self, client: YtDlpClient) -> None:
        """底层异常被翻译成中文 ``ParseError``。"""
        _FakeYoutubeDL.error = RuntimeError("ERROR: Video unavailable")
        with pytest.raises(ParseError) as exc_info:
            client.extract_info("https://example.com/x")
        assert "视频不存在" in exc_info.value.message


# ======================================================================
# download
# ======================================================================
class TestDownload:
    """下载测试。"""

    def test_aborts_before_start(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """启动前已设置取消标志则立即抛 ``DownloadAbort``。"""
        import threading

        flag = threading.Event()
        flag.set()
        with pytest.raises(DownloadAbort):
            client.download(sample_task, lambda d: None, flag)

    def test_opts_use_output_template(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """下载 opts 必须使用统一的 outtmpl 与断点续传。"""
        _FakeYoutubeDL.result = {"requested_downloads": [{"filepath": str(Path(sample_task.output_dir) / "a.mp4")}]}
        client.download(sample_task, lambda d: None, None)

        opts = _FakeYoutubeDL.last_opts
        assert opts["outtmpl"].endswith(OUTPUT_TEMPLATE)
        assert opts["continuedl"] is True
        assert opts["merge_output_format"] == "mp4"
        assert opts["format"] == sample_task.format_id
        assert len(opts["progress_hooks"]) == 1

    def test_returns_requested_download_path(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """优先取 ``requested_downloads[0].filepath``。"""
        expected = Path(sample_task.output_dir) / "final.mp4"
        _FakeYoutubeDL.result = {"requested_downloads": [{"filepath": str(expected)}]}
        assert client.download(sample_task, lambda d: None, None) == str(expected.resolve())

    def test_falls_back_to_prepare_filename(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """没有 filepath 时回落到 ``prepare_filename``。"""
        produced = Path(sample_task.output_dir) / "prepared.mp4"
        produced.write_bytes(b"data")
        _FakeYoutubeDL.result = {"_expected_name": str(produced)}
        assert client.download(sample_task, lambda d: None, None) == str(produced.resolve())

    def test_unsupported_container_falls_back_to_mp4(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """非法容器回落到 mp4。"""
        sample_task.ext = "flv"
        _FakeYoutubeDL.result = {"requested_downloads": [{"filepath": "x.mp4"}]}
        client.download(sample_task, lambda d: None, None)
        assert _FakeYoutubeDL.last_opts["merge_output_format"] == "mp4"


# ======================================================================
# 错误翻译
# ======================================================================
class TestProbeError:
    """错误翻译测试。"""

    @pytest.mark.parametrize(
        ("raw", "keyword"),
        [
            ("ERROR: Unsupported URL: https://x.com", "暂不支持该网站"),
            ("ERROR: Private video", "私享视频"),
            ("ERROR: Video unavailable", "视频不存在"),
            ("Sign in to confirm your age", "年龄限制"),
            ("This video is DRM protected", "DRM"),
            ("HTTP Error 429: Too Many Requests", "请求过于频繁"),
            ("HTTP Error 403: Forbidden", "403"),
            ("HTTP Error 404: Not Found", "404"),
            ("The read operation timed out", "超时"),
            ("No space left on device", "磁盘空间不足"),
            ("Requested format is not available", "所选画质不可用"),
        ],
    )
    def test_known_patterns(self, raw: str, keyword: str) -> None:
        """常见错误被翻译为含关键字的中文提示。"""
        assert keyword in YtDlpClient.probe_error(RuntimeError(raw))

    def test_empty_error(self) -> None:
        """空异常有兜底文案。"""
        assert YtDlpClient.probe_error(RuntimeError("")) == "发生未知错误，请稍后重试"

    def test_parse_error_passthrough(self) -> None:
        """``ParseError`` 直接透传已翻译消息。"""
        assert YtDlpClient.probe_error(ParseError("自定义中文消息")) == "自定义中文消息"

    def test_unknown_error_is_truncated(self) -> None:
        """未知错误保留原文（截断）。"""
        message = YtDlpClient.probe_error(RuntimeError("x" * 500))
        assert message.startswith("解析失败：")
        assert len(message) < 250

    def test_login_error_appends_cookie_path(self) -> None:
        """登录/年龄类错误命中时，提示里附上 cookies 文件路径。"""
        cookie_path = r"C:\Users\me\AppData\Roaming\VidJuice\cookies.txt"
        message = YtDlpClient.probe_error(
            RuntimeError("Sign in to confirm your age"),
            cookie_path=cookie_path,
        )
        assert "年龄限制" in message
        assert cookie_path in message
        assert "重新登录" in message

    def test_non_login_error_does_not_append_cookie_path(self) -> None:
        """非登录类错误不附加 cookies 路径（避免噪声）。"""
        cookie_path = r"C:\Users\me\AppData\Roaming\VidJuice\cookies.txt"
        message = YtDlpClient.probe_error(
            RuntimeError("Video unavailable"), cookie_path=cookie_path
        )
        assert cookie_path not in message

    def test_login_error_without_cookie_path_still_works(self) -> None:
        """不传 cookie_path 时仍给出原始中文提示（向后兼容）。"""
        message = YtDlpClient.probe_error(
            RuntimeError("Sign in to confirm your age")
        )
        assert "年龄限制" in message
        assert "cookie" not in message.lower() or "cookie 文件" in message


class TestIsNonRetryable:
    """重试分类测试。"""

    @pytest.mark.parametrize(
        "message",
        ["暂不支持该网站，请确认链接是否正确", "该视频为私享视频，无法下载", "该平台使用 DRM 加密保护，本软件不支持下载"],
    )
    def test_non_retryable(self, message: str) -> None:
        """不可重试类。"""
        assert is_non_retryable(message) is True

    @pytest.mark.parametrize("message", ["网络连接超时，请检查网络或代理设置", "服务器拒绝访问（403）", ""])
    def test_retryable(self, message: str) -> None:
        """可重试类。"""
        assert is_non_retryable(message) is False


# ======================================================================
# FfmpegMerger
# ======================================================================
class TestFfmpegMerger:
    """ffmpeg 合并器测试（不调用真实 ffmpeg）。"""

    def test_resolve_binary_uses_configured_file(self, tmp_path: Path) -> None:
        """配置指定的文件优先。"""
        exe = tmp_path / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        assert FfmpegMerger.resolve_binary(str(exe)) == str(exe.resolve())

    def test_resolve_binary_accepts_directory(self, tmp_path: Path) -> None:
        """配置指定目录时在其中查找。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        assert FfmpegMerger.resolve_binary(str(bin_dir)) == str(exe.resolve())

    def test_resolve_binary_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """三种途径都找不到时抛 ``MergeError``。"""
        monkeypatch.setattr("src.core.ffmpeg_merger.shutil.which", lambda name: None)
        monkeypatch.setattr("src.core.ffmpeg_merger.resource_path", lambda rel: str(tmp_path / "nope"))
        with pytest.raises(MergeError):
            FfmpegMerger.resolve_binary(None)

    def test_is_available_false_when_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """不可用时返回 False 而非抛异常。"""
        monkeypatch.setattr("src.core.ffmpeg_merger.shutil.which", lambda name: None)
        monkeypatch.setattr("src.core.ffmpeg_merger.resource_path", lambda rel: str(tmp_path / "nope"))
        assert FfmpegMerger(None).is_available() is False

    def test_merge_requires_inputs(self, tmp_path: Path) -> None:
        """输入分片缺失时抛 ``MergeError``。"""
        merger = FfmpegMerger(None)
        with pytest.raises(MergeError):
            merger.merge(str(tmp_path / "nope.mp4"), str(tmp_path / "nope.m4a"), str(tmp_path / "o.mp4"))

    def test_merge_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """成功路径：ffmpeg 返回 0 且产物存在。"""
        video = tmp_path / "v.mp4"
        audio = tmp_path / "a.m4a"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"v")
        audio.write_bytes(b"a")

        exe = tmp_path / "ffmpeg.exe"
        exe.write_bytes(b"MZ")

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            Path(cmd[-1]).write_bytes(b"merged")
            return _Result()

        monkeypatch.setattr("src.core.ffmpeg_merger.run_hidden", _fake_run)

        merger = FfmpegMerger(str(exe))
        assert merger.merge(str(video), str(audio), str(out)) == str(out.resolve())
        assert out.is_file()

    def test_merge_failure_reports_stderr_tail(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """返回码非 0 时抛错并携带 stderr 摘要。"""
        video = tmp_path / "v.mp4"
        audio = tmp_path / "a.m4a"
        video.write_bytes(b"v")
        audio.write_bytes(b"a")
        exe = tmp_path / "ffmpeg.exe"
        exe.write_bytes(b"MZ")

        class _Result:
            returncode = 1
            stdout = ""
            stderr = "line1\nline2\nfatal: bad input"

        monkeypatch.setattr("src.core.ffmpeg_merger.run_hidden", lambda cmd, **kw: _Result())

        with pytest.raises(MergeError) as exc_info:
            FfmpegMerger(str(exe)).merge(str(video), str(audio), str(tmp_path / "o.mp4"))
        assert "fatal: bad input" in exc_info.value.message

    def test_cleanup_parts_is_safe(self, tmp_path: Path) -> None:
        """清理不存在的文件不报错。"""
        existing = tmp_path / "p.mp4"
        existing.write_bytes(b"x")
        FfmpegMerger(None).cleanup_parts(str(existing), str(tmp_path / "missing"), "")
        assert not existing.exists()

    def test_tail_stderr_empty(self) -> None:
        """空 stderr 返回兜底文案。"""
        assert FfmpegMerger._tail_stderr("") == "未知错误"


class TestFormatHelpers:
    """辅助断言：确保伪最佳格式在下载时被正确传递。"""

    def test_best_format_id_used_when_empty(self, client: YtDlpClient, sample_task: DownloadTask) -> None:
        """任务 format_id 为空时回落到 ``BEST_FORMAT_ID``。"""
        sample_task.format_id = ""
        _FakeYoutubeDL.result = {"requested_downloads": [{"filepath": "x.mp4"}]}
        client.download(sample_task, lambda d: None, None)
        assert _FakeYoutubeDL.last_opts["format"] == BEST_FORMAT_ID

    def test_media_format_best_is_flagged(self) -> None:
        """伪最佳格式标记正确。"""
        assert MediaFormat.make_best().is_best is True
