"""打包脚本：清理 → 调用 PyInstaller → 产物校验。

用法：
    python build.py            # 完整打包
    python build.py --check    # 仅做打包前置环境检查，不执行打包

注意：本版本仅提交脚本与规格文件，CI/交付流程中**不执行**实际打包。
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC_FILE = ROOT / "build.spec"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
EXE_NAME = "VidJuiceUniTube.exe"
FFMPEG_BIN = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
APP_ICON = ROOT / "src" / "resources" / "icons" / "app.ico"

logging.basicConfig(level=logging.INFO, format="[build] %(message)s")
logger = logging.getLogger("build")


def clean() -> None:
    """删除上一次的 build/ 与 dist/ 目录。"""
    for target in (BUILD_DIR, DIST_DIR):
        if target.exists():
            logger.info("清理目录：%s", target)
            shutil.rmtree(target, ignore_errors=True)
    logger.info("清理完成")


def preflight() -> bool:
    """打包前置检查：spec 文件、PyInstaller、ffmpeg、图标。

    Returns:
        致命项全部就绪返回 ``True``；否则 ``False``。
    """
    ok = True

    if not SPEC_FILE.is_file():
        logger.error("缺少规格文件：%s", SPEC_FILE)
        ok = False

    try:
        import PyInstaller  # noqa: F401

        logger.info("PyInstaller 已安装")
    except ImportError:
        logger.error("未安装 PyInstaller，请先执行：pip install -r requirements-dev.txt")
        ok = False

    try:
        import yt_dlp  # noqa: F401

        logger.info("yt-dlp 已安装")
    except ImportError:
        logger.error("未安装 yt-dlp，请先执行：pip install -r requirements.txt")
        ok = False

    try:
        import PySide6  # noqa: F401

        logger.info("PySide6 已安装")
    except ImportError:
        logger.error("未安装 PySide6，请先执行：pip install -r requirements.txt")
        ok = False

    # QtWebEngine 在 PySide6-Addons 里，只装 PySide6-Essentials 的机器能顺利
    # 打出 EXE，但内置浏览器登录（需求 #3）会直接抛 RuntimeError —— 打包过程
    # 零错误、需求静默失效。必须在打包前硬拦。
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401

        logger.info("QtWebEngine 已安装（内置浏览器登录可用）")
    except ImportError:
        logger.error(
            "缺少 QtWebEngine（内置浏览器登录不可用）。"
            "请安装 PySide6-Addons 或完整版 PySide6：pip install PySide6-Addons"
        )
        ok = False

    if not FFMPEG_BIN.is_file():
        logger.warning("未找到 %s，打包产物将无法自动合并音视频（非致命）", FFMPEG_BIN)
    else:
        logger.info("ffmpeg 就绪：%s", FFMPEG_BIN)

    if not APP_ICON.is_file():
        logger.warning("未找到 %s，将不带图标打包（非致命）", APP_ICON)

    return ok


def run_pyinstaller() -> bool:
    """调用 PyInstaller 执行打包。

    Returns:
        打包过程是否成功。
    """
    try:
        import PyInstaller.__main__ as pyi_main
    except ImportError:
        logger.error("无法导入 PyInstaller，打包中止")
        return False

    args = [str(SPEC_FILE), "--noconfirm", "--clean", "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR)]
    logger.info("开始打包：pyinstaller %s", " ".join(args))
    started = time.time()
    try:
        pyi_main.run(args)
    except SystemExit as exc:  # PyInstaller 内部用 SystemExit 表达失败
        if exc.code not in (0, None):
            logger.error("PyInstaller 退出码 %s，打包失败", exc.code)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.error("打包过程异常：%s", exc)
        return False

    logger.info("打包耗时 %.1f 秒", time.time() - started)
    return True


def verify() -> bool:
    """校验产物是否存在并打印体积。

    Returns:
        产物存在返回 ``True``。
    """
    exe_path = DIST_DIR / EXE_NAME
    if not exe_path.is_file():
        logger.error("未找到产物：%s", exe_path)
        return False

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    logger.info("产物校验通过：%s（%.1f MB）", exe_path, size_mb)
    # 阈值设为 400MB：捆绑 QtWebEngine 后产物 200MB+ 属正常，若沿用 200MB
    # 阈值会必然触发告警，诱导后人去砍 excludes——而砍掉 QtQml/QtQuick 正是
    # 会让内置浏览器崩溃的那个坑。
    if size_mb > 400:
        logger.warning(
            "产物体积异常（>400MB），可检查 excludes 配置"
            "（注：含 QtWebEngine 时 200-300MB 属正常，无需处理）"
        )
    return True


def main() -> int:
    """脚本入口。

    Returns:
        进程退出码，0 表示成功。
    """
    parser = argparse.ArgumentParser(description="VidJuice UniTube 打包脚本")
    parser.add_argument("--check", action="store_true", help="仅执行前置环境检查")
    parser.add_argument("--skip-clean", action="store_true", help="跳过清理步骤")
    args = parser.parse_args()

    if not preflight():
        logger.error("前置检查未通过")
        return 1

    if args.check:
        logger.info("前置检查通过（--check 模式，不执行打包）")
        return 0

    if not args.skip_clean:
        clean()

    if not run_pyinstaller():
        return 1

    if not verify():
        return 1

    logger.info("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
