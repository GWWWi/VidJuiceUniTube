"""ffmpeg 音视频合并封装。

定位顺序：配置指定 → 随包 ``vendor/ffmpeg/bin/ffmpeg.exe`` → 系统 ``PATH``。
所有 subprocess 调用走 ``utils.proc.run_hidden``，Windows 下不弹黑框。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from src.constants import BUNDLED_FFMPEG_REL, FFMPEG_TIMEOUT
from src.core.exceptions import MergeError
from src.utils.paths import resource_path
from src.utils.proc import run_hidden

logger = logging.getLogger(__name__)


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
    def merge(self, video_path: str, audio_path: str, out_path: str) -> str:
        """把独立的视频流与音频流合并为单一文件（流复制，不重编码）。

        Args:
            video_path: 视频分片路径。
            audio_path: 音频分片路径。
            out_path: 输出文件路径。

        Returns:
            合并后的文件绝对路径。

        Raises:
            MergeError: 输入缺失、ffmpeg 不可用、超时或合并失败。
        """
        if not video_path or not Path(video_path).is_file():
            raise MergeError("合并失败：视频分片不存在", detail=str(video_path))
        if not audio_path or not Path(audio_path).is_file():
            raise MergeError("合并失败：音频分片不存在", detail=str(audio_path))

        ffmpeg = self.binary_path()
        out_abs = os.path.abspath(out_path)
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
            "-movflags", "+faststart",
            out_abs,
        ]

        logger.info("开始合并音视频 → %s", out_abs)
        try:
            result = run_hidden(cmd, timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise MergeError(f"音视频合并超时（超过 {FFMPEG_TIMEOUT} 秒）", detail=str(exc)) from exc
        except OSError as exc:
            raise MergeError("音视频合并失败：无法启动 ffmpeg", detail=str(exc)) from exc

        if result.returncode != 0:
            summary = self._tail_stderr(result.stderr)
            raise MergeError(f"ffmpeg 合并失败：{summary}", detail=str(result.stderr or ""))

        if not Path(out_abs).is_file():
            raise MergeError("ffmpeg 合并失败：未生成输出文件", detail=out_abs)

        logger.info("合并完成：%s", out_abs)
        return out_abs

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
