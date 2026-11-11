"""ffmpeg 音视频合并封装。

定位顺序：配置指定 → 随包 ``vendor/ffmpeg/bin/ffmpeg.exe`` → 系统 ``PATH``。
所有 subprocess 调用走 ``utils.proc.run_hidden``，Windows 下不弹黑框。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from src.constants import BUNDLED_FFMPEG_REL, FFMPEG_TIMEOUT
from src.core.exceptions import DownloadAbort, MergeError
from src.utils.paths import resource_path
from src.utils.proc import popen_hidden, run_hidden

logger = logging.getLogger(__name__)

#: 这些视频容器的编码（h264/h265）无法用 ``-c copy`` 写进 webm。
_WEBM_INCOMPATIBLE = (".mp4", ".mkv", ".flv", ".ts", ".mov")


class FfmpegMerger:
    """ffmpeg 合并器。"""

    def __init__(self, ffmpeg_path: str | None = None) -> None:
        """初始化合并器。

        Args:
            ffmpeg_path: 手动指定的 ffmpeg 可执行文件路径；``None`` 表示自动查找。
        """
        self._ffmpeg_path: str | None = ffmpeg_path or None

    # ------------------------------------------------------------------
    # 二进制定位
    # ------------------------------------------------------------------
    @staticmethod
    def resolve_binary(configured: str | None = None) -> str:
        """按优先级定位 ffmpeg 可执行文件。

        Args:
            configured: 配置中手动指定的路径（可为目录或文件）。

        Returns:
            ffmpeg 可执行文件的绝对路径。

        Raises:
            MergeError: 三种途径都找不到。
        """
        # 1) 配置指定
        if configured:
            candidate = Path(configured)
            if candidate.is_file():
                return str(candidate.resolve())
            if candidate.is_dir():
                for name in ("ffmpeg.exe", "ffmpeg"):
                    exe = candidate / name
                    if exe.is_file():
                        return str(exe.resolve())
            logger.warning("配置的 ffmpeg 路径无效：%s", configured)

        # 2) 随包资源
        bundled = Path(resource_path(BUNDLED_FFMPEG_REL))
        if bundled.is_file():
            return str(bundled.resolve())

        # 3) 系统 PATH
        found = shutil.which("ffmpeg")
        if found:
            return str(Path(found).resolve())

        raise MergeError(
            "未找到 ffmpeg，请在设置中指定 ffmpeg 路径，或把 ffmpeg.exe 放到 vendor/ffmpeg/bin/ 目录",
            detail="resolve_binary: 配置 / 随包资源 / 系统 PATH 均未命中",
        )

    def binary_path(self) -> str:
        """返回当前可用的 ffmpeg 路径（带缓存）。

        Returns:
            ffmpeg 可执行文件路径。

        Raises:
            MergeError: 找不到 ffmpeg。
        """
        if self._ffmpeg_path and Path(self._ffmpeg_path).is_file():
            return self._ffmpeg_path
        self._ffmpeg_path = self.resolve_binary(self._ffmpeg_path)
        return self._ffmpeg_path

    def is_available(self) -> bool:
        """检测 ffmpeg 是否可用（不抛异常）。"""
        try:
            self.binary_path()
            return True
        except MergeError:
            return False

    def version(self) -> str:
        """返回 ffmpeg 版本首行；不可用时返回空串。"""
        try:
            result = run_hidden([self.binary_path(), "-version"], timeout=10)
        except (MergeError, OSError, subprocess.SubprocessError) as exc:
            logger.debug("获取 ffmpeg 版本失败：%s", exc)
            return ""
        output = (result.stdout or "").strip()
        return output.splitlines()[0] if output else ""

    # ------------------------------------------------------------------
    # 合并
    # ------------------------------------------------------------------
    def merge(self, video_path: str, audio_path: str, out_path: str,
              cancel_event=None) -> str:
        """把独立的视频流与音频流合并为单一文件（流复制，不重编码）。

        Args:
            video_path: 视频分片路径。
            audio_path: 音频分片路径。
            out_path: 输出文件路径。
            cancel_event: 可选的 ``threading.Event``；置位后立即终止 ffmpeg。

        Returns:
            合并后的文件绝对路径（可能因容器兼容性被改为 ``.mkv``）。

        Raises:
            MergeError: 输入缺失、ffmpeg 不可用、超时或合并失败。
            DownloadAbort: ``cancel_event`` 被置位。
        """
        if not video_path or not Path(video_path).is_file():
            raise MergeError("合并失败：视频分片不存在", detail=str(video_path))
        if not audio_path or not Path(audio_path).is_file():
            raise MergeError("合并失败：音频分片不存在", detail=str(audio_path))

        ffmpeg = self.binary_path()
        out_abs = os.path.abspath(self._ensure_container_compatible(video_path, out_path))
        os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)

        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", os.path.abspath(video_path),
            "-i", os.path.abspath(audio_path),
            "-c:v", "copy",
            "-c:a", "copy",
        ]
        # faststart 只对 mp4/isobmff 有意义，加给 mkv/webm 只会多一次无谓扫描
        if out_abs.lower().endswith(".mp4"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(out_abs)

        logger.info("开始合并音视频 → %s", out_abs)
        try:
            proc = popen_hidden(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise MergeError("音视频合并失败：无法启动 ffmpeg", detail=str(exc)) from exc

        deadline = time.monotonic() + FFMPEG_TIMEOUT
        stderr = ""
        try:
            # 轮询而非 communicate(timeout=FFMPEG_TIMEOUT)：后者在 600s 内
            # 完全不响应取消请求，用户点「取消」要等 10 分钟才生效。
            while True:
                try:
                    _out, stderr = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        self._kill(proc)
                        logger.info("合并已按用户请求取消：%s", out_abs)
                        raise DownloadAbort("合并已被取消")
                    if time.monotonic() > deadline:
                        self._kill(proc)
                        raise MergeError(f"音视频合并超时（超过 {FFMPEG_TIMEOUT} 秒）")
        finally:
            if proc.poll() is None:
                self._kill(proc)

        if proc.returncode != 0:
            summary = self._tail_stderr(stderr)
            raise MergeError(f"ffmpeg 合并失败：{summary}", detail=str(stderr or ""))

        if not Path(out_abs).is_file():
            raise MergeError("ffmpeg 合并失败：未生成输出文件", detail=out_abs)

        logger.info("合并完成：%s", out_abs)
        return out_abs

    @staticmethod
    def _ensure_container_compatible(video_path: str, out_path: str) -> str:
        """webm 容器只接受 VP8/VP9/AV1；遇到 mp4 系分片时降级为 mkv。

        否则 ``-c copy`` 会以 ``Only VP8 or VP9 or AV1 video ...`` 失败，
        而错误又被分类成「合并失败」，用户会去折腾 ffmpeg 路径。
        """
        if os.path.splitext(out_path)[1].lower() != ".webm":
            return out_path
        if os.path.splitext(video_path)[1].lower() in _WEBM_INCOMPATIBLE:
            alt = str(Path(out_path).with_suffix(".mkv"))
            logger.warning("webm 容器不支持该视频编码，改为输出 mkv：%s", alt)
            return alt
        return out_path

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        """强制结束 ffmpeg 并回收句柄，避免留下孤儿进程继续写盘。"""
        try:
            proc.kill()
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001 - 清理路径不应再抛
            logger.debug("终止 ffmpeg 进程失败", exc_info=True)

    def cleanup_parts(self, *paths: str) -> None:
        """删除合并后残留的中间分片文件（失败只记日志）。

        Args:
            *paths: 待删除的文件路径。
        """
        for path in paths:
            if not path:
                continue
            try:
                target = Path(path)
                if target.is_file():
                    target.unlink()
                    logger.debug("已清理中间文件：%s", path)
            except OSError as exc:
                logger.warning("清理中间文件失败 %s：%s", path, exc)

    @staticmethod
    def _tail_stderr(stderr: str | None, lines: int = 5) -> str:
        """截取 stderr 末尾若干行作为错误摘要。

        Args:
            stderr: ffmpeg 的标准错误输出。
            lines: 保留行数。

        Returns:
            单行化的错误摘要。
        """
        text = str(stderr or "").strip()
        if not text:
            return "未知错误"
        tail = [line.strip() for line in text.splitlines() if line.strip()][-lines:]
        return " | ".join(tail)[:500] or "未知错误"


__all__ = ["FfmpegMerger"]
