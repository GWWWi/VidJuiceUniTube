"""yt-dlp 封装。

**全项目唯一**允许 ``import yt_dlp`` 的模块。其他任何文件都必须通过
``YtDlpClient`` 访问下载引擎，以便统一 opts 构建、错误翻译与未来替换引擎。

约定：
- 使用库方式（``yt_dlp.YoutubeDL``）而非 subprocess；
- 每次调用创建**新的** ``YoutubeDL`` 实例（内部有状态，并发复用会串数据）；
- 空字符串配置项必须转成 ``None``（yt-dlp 对 ``cookiefile=""`` 会报错）。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from src.constants import (
    BEST_FORMAT_ID,
    OUTPUT_TEMPLATE,
    SOCKET_TIMEOUT,
    SUPPORTED_CONTAINERS,
    YTDLP_INTERNAL_RETRIES,
)
from src.core.config_store import ConfigStore
from src.core.exceptions import DownloadAbort, DownloadPause, ParseError
from src.core.format_utils import quality_sort_key
from src.core.models import DownloadTask, MediaFormat, MediaInfo, _safe_int

logger = logging.getLogger(__name__)

#: 错误关键字 → 中文提示（按顺序匹配，先命中者优先）
ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("unsupported url", "暂不支持该网站，请确认链接是否正确"),
    ("is not a valid url", "链接格式不正确，请检查后重试"),
    ("private video", "该视频为私享视频，无法下载"),
    ("video unavailable", "视频不存在、已删除或在当前地区不可用"),
    ("this video is not available", "视频不存在、已删除或在当前地区不可用"),
    ("removed by the uploader", "该视频已被上传者删除"),
    ("members-only", "该视频为会员专属内容，需要在设置中配置 cookie 文件"),
    ("sign in to confirm your age", "该视频有年龄限制，需要在设置中配置 cookie 文件"),
    ("age restricted", "该视频有年龄限制，需要在设置中配置 cookie 文件"),
    ("sign in", "该内容需要登录才能访问，请在设置中配置 cookie 文件"),
    ("login required", "该内容需要登录才能访问，请在设置中配置 cookie 文件"),
    ("drm", "该平台使用 DRM 加密保护，本软件不支持下载"),
    ("protected", "该内容受加密保护，本软件不支持下载"),
    ("http error 429", "请求过于频繁，请稍后再试或在设置中配置代理"),
    ("too many requests", "请求过于频繁，请稍后再试或在设置中配置代理"),
    ("http error 403", "服务器拒绝访问（403），可尝试配置 cookie 或代理"),
    ("http error 404", "资源不存在（404），请确认链接是否有效"),
    ("timed out", "网络连接超时，请检查网络或代理设置"),
    ("timeout", "网络连接超时，请检查网络或代理设置"),
    ("connection", "网络连接失败，请检查网络或代理设置"),
    ("ssl", "SSL 证书校验失败，请检查系统时间或代理设置"),
    ("no space left", "磁盘空间不足，请清理磁盘后重试"),
    ("permission denied", "文件写入被拒绝，请检查下载目录权限"),
    ("ffmpeg", "缺少 ffmpeg 或合并失败，请在设置中指定 ffmpeg 路径"),
    ("unable to extract", "页面结构解析失败，可能是站点改版，请稍后再试或升级软件"),
    ("requested format is not available",
     "所选画质不可用，请换一个画质重试；若仍失败可改选「最佳画质（自动）」或在设置中配置 cookie"),
    # SABR-only / PO Token 缺失 / 签名 challenge 等场景会让 yt-dlp 报告
    # 与格式选择相关的次级错误，额外给出更明确的「站点受限」提示。
)

#: 不可重试的错误关键字（重试也不会成功，直接判定失败）
NON_RETRYABLE_KEYWORDS: tuple[str, ...] = (
    "暂不支持该网站",
    "链接格式不正确",
    "私享视频",
    "视频不存在",
    "已被上传者删除",
    "年龄限制",
    "需要登录",
    "会员专属",
    "DRM 加密",
    "受加密保护",
    "本软件不支持下载",
    "所选画质不可用",
    "磁盘空间不足",
)


def is_non_retryable(message: str) -> bool:
    """判断中文错误消息是否属于「不可重试」类。

    Args:
        message: ``probe_error()`` 产出的中文错误消息。

    Returns:
        ``True`` 表示重试无意义，应直接置为失败。
    """
    text = str(message or "")
    return any(keyword in text for keyword in NON_RETRYABLE_KEYWORDS)


class YtDlpClient:
    """yt-dlp 引擎封装。"""

    def __init__(self, config: ConfigStore) -> None:
        """初始化客户端。

        Args:
            config: 配置存取器，用于读取 cookie / 代理 / ffmpeg 路径。
        """
        self._config: ConfigStore = config

    def cookie_path(self) -> str | None:
        """当前生效的 cookies 文件路径（供错误提示 / 日志使用）。

        Worker 不应该直接访问 ``_config``——那是实现细节，测试替身也不会提供。
        暴露一个语义明确的公有方法，既解耦也让失败路径不会因为
        ``AttributeError`` 把异常再次抛出 ``run()``。
        """
        try:
            return self._config.cookiefile()
        except Exception:  # noqa: BLE001 - 仅用于提示文案，取不到就当没有
            logger.debug("读取 cookiefile 路径失败", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # opts 构建
    # ------------------------------------------------------------------
    def build_opts(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """构建 yt-dlp 选项字典。

        Args:
            extra: 任务级附加选项，会覆盖同名基础选项。

        Returns:
            可直接传给 ``YoutubeDL`` 的选项字典。
        """
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": SOCKET_TIMEOUT,
            "retries": YTDLP_INTERNAL_RETRIES,
            "fragment_retries": YTDLP_INTERNAL_RETRIES,
            "nocheckcertificate": False,
            "ignoreerrors": False,
            "noplaylist": True,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "consoletitle": False,
            # YouTube 年龄 / 地区限制兜底：未登录（无 cookie）时也轮换多种
            # player_client，尝试绕过部分年龄限制。extractor_args 按 extractor
            # 名称分桶，非 YouTube 站点不会读取 ``youtube`` key，因此不影响其他
            # 站点。若外部 ``extra`` 显式设置了 ``extractor_args``，下方
            # ``opts.update(extra)`` 会覆盖此处（即「仅在该 key 未由外部设置时
            # 才启用兜底」）。
            # ⚠️ 语义提醒：``opts.update(extra)`` 是**整桶替换而非深合并**——
            # 外部一旦传入 ``extractor_args``，此处整个 youtube 兜底配置都会
            # 被顶掉（而不是逐键合并）。当前代码库无人传该参数，如将来需要
            # 局部覆盖，请改为显式深合并，不要依赖 update 的浅覆盖行为。
            # ``default`` 由 yt-dlp 展开为其当前推荐客户端集合（会随版本演进，
            # 避免硬编码 ``web`` 而错过官方修复），随后追加 ``tv`` / ``ios``
            # 作为年龄 / 地区限制的兜底客户端。
            "extractor_args": {"youtube": {"player_client": ["default", "tv", "ios"]}},
            "logger": _YdlLoggerBridge(),
        }

        # 空串必须转 None，否则 yt-dlp 会报错
        cookiefile = self._config.cookiefile()
        if cookiefile:
            opts["cookiefile"] = cookiefile

        proxy = self._config.proxy()
        if proxy:
            opts["proxy"] = proxy

        ffmpeg_location = self._resolve_ffmpeg_location()
        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location

        if extra:
            opts.update(extra)
        return opts

    def _resolve_ffmpeg_location(self) -> str | None:
        """解析要传给 yt-dlp 的 ffmpeg 位置（目录或可执行文件路径）。"""
        configured = self._config.ffmpeg_path()
        if configured and os.path.exists(configured):
            return configured

        # 延迟导入，避免 core 模块间循环依赖
        from src.core.ffmpeg_merger import FfmpegMerger

        try:
            return FfmpegMerger.resolve_binary()
        except Exception:  # noqa: BLE001 - 找不到 ffmpeg 不应阻断解析
            return None

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def extract_info(self, url: str) -> MediaInfo:
        """解析链接，返回归一化的媒体信息。

        Args:
            url: 视频链接。

        Returns:
            ``MediaInfo``，``formats`` 已按画质降序排序，并在首位插入
            伪「最佳画质（自动）」项。

        Raises:
            ParseError: 解析失败（消息为中文可读原因）。
        """
        clean_url = str(url or "").strip()
        if not clean_url:
            raise ParseError("请输入有效的视频链接", url=clean_url)

        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:  # pragma: no cover - 依赖缺失属环境问题
            raise ParseError("未安装 yt-dlp，请先执行 pip install -r requirements.txt", detail=str(exc)) from exc

        # ``ignore_no_formats_error`` 仅在「列出格式」场景下设置：
        # yt-dlp 的 ``process_video_result``（被 ``extract_info`` 隐式触发，
        # 见 YoutubeDL.py:1947）会用 ``_default_format_spec`` 做一次格式选择
        # —— 默认是 ``bestvideo*+bestaudio/best``。当视频因为 SABR-only 实验、
        # 缺失 PO Token 或签名 challenge 解析失败导致没有可用视频流时，
        # 该次选择会抛 ``Requested format is not available``，**即使我们
        # 只是想列出格式**。这会让解析本身失败、用户连画质下拉都看不到。
        # ``ignore_no_formats_error=True`` 会让 yt-dlp 把这次失败降级为
        # warning，仍然返回完整 formats 列表。注意：**下载路径不应设置**，
        # 因为那里我们需要明确的失败信号来判断是否需要重试/降级。
        opts = self.build_opts({"skip_download": True, "ignore_no_formats_error": True})
        logger.info("开始解析链接：%s", clean_url)

        try:
            with YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(clean_url, download=False)
        except Exception as exc:  # noqa: BLE001 - 统一翻译为中文错误
            message = self.probe_error(exc)
            logger.warning("解析失败：%s | %s", clean_url, exc)
            raise ParseError(message, detail=str(exc), url=clean_url) from exc

        if not raw:
            raise ParseError("解析结果为空，请确认链接是否有效", url=clean_url)

        # 播放列表：只取第一条
        if isinstance(raw, dict) and raw.get("entries"):
            entries = [item for item in raw.get("entries") or [] if item]
            if not entries:
                raise ParseError("播放列表为空或全部不可用", url=clean_url)
            logger.warning("检测到播放列表，仅解析第一个视频（共 %d 个）", len(entries))
            raw = entries[0]

        return self._build_media_info(clean_url, raw)

    def _build_media_info(self, url: str, raw: dict[str, Any]) -> MediaInfo:
        """把 yt-dlp 原始 info dict 转换为 ``MediaInfo``。

        Args:
            url: 原始请求链接。
            raw: yt-dlp 返回的 info dict。

        Returns:
            归一化的 ``MediaInfo``。
        """
        formats: list[MediaFormat] = []
        for item in raw.get("formats") or []:
            if not isinstance(item, dict):
                continue
            vcodec = str(item.get("vcodec") or "none")
            acodec = str(item.get("acodec") or "none")
            if vcodec == "none" and acodec == "none":
                continue  # 无效项（如纯 storyboard）
            if str(item.get("format_note") or "").lower() == "storyboard":
                continue
            if not item.get("format_id"):
                continue
            formats.append(MediaFormat.from_ydl_dict(item))

        # 按画质降序：高度 > 帧率 > 是否含音轨 > 体积
        formats.sort(key=quality_sort_key, reverse=True)

        # 置顶插入伪「最佳画质（自动）」作为默认项
        formats.insert(0, MediaFormat.make_best())

        info = MediaInfo(
            url=str(raw.get("webpage_url") or url),
            title=str(raw.get("title") or "未命名视频"),
            uploader=raw.get("uploader") or raw.get("channel") or raw.get("uploader_id"),
            duration=_safe_int(raw.get("duration")),
            thumbnail=raw.get("thumbnail"),
            extractor=str(raw.get("extractor_key") or raw.get("extractor") or ""),
            formats=formats,
        )
        logger.info("解析成功：%s（%d 个格式）", info.title, len(info.formats))
        return info

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def download(
        self,
        task: DownloadTask,
        hook: Callable[[dict[str, Any]], None],
        abort_flag: threading.Event | None = None,
    ) -> str:
        """执行下载并返回最终文件路径。

        Args:
            task: 下载任务（只读取静态字段）。
            hook: yt-dlp 进度回调，内部负责节流与中断检查。
            abort_flag: 取消标志；在启动前检查一次，避免无谓的网络请求。

        Returns:
            下载完成后的本地文件绝对路径。

        Raises:
            DownloadAbort: 用户取消。
            DownloadPause: 用户暂停。
            Exception: 其他底层异常（调用方需用 ``probe_error`` 翻译）。
        """
        if abort_flag is not None and abort_flag.is_set():
            raise DownloadAbort(task_id=task.task_id)

        from yt_dlp import YoutubeDL

        os.makedirs(task.output_dir, exist_ok=True)

        merge_format = task.ext if task.ext in SUPPORTED_CONTAINERS else "mp4"
        extra: dict[str, Any] = {
            "format": task.format_id or BEST_FORMAT_ID,
            "outtmpl": os.path.join(task.output_dir, OUTPUT_TEMPLATE),
            "merge_output_format": merge_format,
            "progress_hooks": [hook],
            "continuedl": True,  # 断点续传，暂停/恢复的关键
            "skip_download": False,
            "overwrites": False,
        }
        opts = self.build_opts(extra)

        logger.info("开始下载：%s（format=%s）", task.title, task.format_id)
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(task.url, download=True)
            return self._resolve_output_path(ydl, info, task)

    @staticmethod
    def _resolve_output_path(ydl: Any, info: Any, task: DownloadTask) -> str:
        """从 yt-dlp 返回的 info 推断最终产物路径。

        Args:
            ydl: ``YoutubeDL`` 实例。
            info: ``extract_info(download=True)`` 的返回值。
            task: 当前任务，用于兜底拼路径。

        Returns:
            最终文件绝对路径（可能不存在，由调用方校验）。
        """
        if isinstance(info, dict):
            requested = info.get("requested_downloads")
            if requested:
                first = requested[0] or {}
                path = first.get("filepath") or first.get("_filename")
                if path:
                    return os.path.abspath(str(path))

            direct = info.get("filepath") or info.get("_filename")
            if direct:
                return os.path.abspath(str(direct))

            try:
                prepared = ydl.prepare_filename(info)
                if prepared:
                    # 合并后扩展名可能变化，优先返回实际存在的文件
                    if os.path.exists(prepared):
                        return os.path.abspath(str(prepared))
                    stem, _ext = os.path.splitext(str(prepared))
                    for candidate_ext in (task.ext, "mp4", "mkv", "webm"):
                        candidate = f"{stem}.{candidate_ext}"
                        if os.path.exists(candidate):
                            return os.path.abspath(candidate)
                    return os.path.abspath(str(prepared))
            except Exception as exc:  # noqa: BLE001 - 推断失败不应中断流程
                logger.debug("prepare_filename 失败：%s", exc)

        return os.path.abspath(os.path.join(task.output_dir, f"{task.title}.{task.ext}"))

    # ------------------------------------------------------------------
    # 错误翻译
    # ------------------------------------------------------------------
    @staticmethod
    def probe_error(
        exc: Exception,
        *,
        cookie_path: str | None = None,
    ) -> str:
        """把底层异常翻译为面向用户的中文提示。

        Args:
            exc: 任意异常对象。
            cookie_path: 当前配置的 cookies 文件绝对路径。命中登录/年龄类错误
                时，会追加到提示里，方便用户直接去核对（不传则只给原始中文）。

        Returns:
            中文错误消息（含建议）。
        """
        # 控制流异常与已翻译的应用异常直接透传
        if isinstance(exc, (DownloadAbort, DownloadPause)):
            return str(exc)
        if isinstance(exc, ParseError):
            # ParseError 已携带 detail；这里也允许外层再附加 cookie 提示
            if cookie_path and any(k in exc.message.lower() for k in _LOGIN_HINT_KEYWORDS):
                return _append_cookie_hint(exc.message, cookie_path)
            return exc.message

        raw = str(exc or "").strip()
        if not raw:
            return "发生未知错误，请稍后重试"

        lowered = raw.lower()
        for keyword, message in ERROR_PATTERNS:
            if keyword in lowered:
                if keyword in _LOGIN_HINT_KEYWORDS:
                    return _append_cookie_hint(message, cookie_path)
                return message

        if isinstance(exc, MemoryError):
            return "内存不足，请关闭其他程序后重试"
        if isinstance(exc, OSError):
            return f"文件系统错误：{raw[:200]}"

        return f"解析失败：{raw[:200]}"


#: 触发「附加 cookie 文件路径」提示的错误关键字集合。
#: 这些场景下用户最常见的下一步是「去核对 cookies.txt 是否有登录态」，
#: 弹窗里直接给出文件绝对路径能少走一轮回路。
_LOGIN_HINT_KEYWORDS: tuple[str, ...] = (
    "sign in to confirm your age",
    "age restricted",
    "sign in",
    "login required",
    "members-only",
)


def _append_cookie_hint(message: str, cookie_path: str | None) -> str:
    """登录/年龄类错误消息追加 cookies 文件路径提示。

    仅当 ``cookie_path`` 非空且尚未在消息中时追加——避免重复提示。
    """
    if not cookie_path:
        return message
    if cookie_path in message:
        return message
    return f"{message}\n\n当前 cookie 文件：{cookie_path}\n（如未含登录态，请在「登录 YouTube」里重新登录并保存）"


class _YdlLoggerBridge:
    """把 yt-dlp 内部日志桥接到 Python logging（避免其直接写 stdout）。"""

    _logger = logging.getLogger("yt_dlp")

    def debug(self, msg: str) -> None:
        """yt-dlp debug 输出。"""
        text = str(msg)
        if text.startswith("[debug] "):
            self._logger.debug(text[8:])
        else:
            self._logger.debug(text)

    def info(self, msg: str) -> None:
        """yt-dlp info 输出。"""
        self._logger.debug(str(msg))

    def warning(self, msg: str) -> None:
        """yt-dlp warning 输出。"""
        self._logger.warning(str(msg))

    def error(self, msg: str) -> None:
        """yt-dlp error 输出。"""
        self._logger.error(str(msg))


__all__ = ["ERROR_PATTERNS", "NON_RETRYABLE_KEYWORDS", "YtDlpClient", "is_non_retryable"]
