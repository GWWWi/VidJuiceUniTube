# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格文件：VidJuice UniTube 单文件打包配置。

用法：
    python build.py            # 推荐（含清理与产物校验）
    pyinstaller build.spec --noconfirm

注意：
- ffmpeg.exe 需预先放到 vendor/ffmpeg/bin/，否则打包产物无法自动合并音视频；
- app.ico 缺失时会自动降级为无图标打包；
- hiddenimports 必须包含 yt_dlp.extractor，否则打包后所有站点解析都会失败。
"""

import os
import shutil
import sys

block_cipher = None

# spec 文件执行时 CWD 即项目根目录
ROOT = os.path.abspath(os.getcwd())

APP_NAME = "VidJuiceUniTube"
ENTRY_SCRIPT = os.path.join(ROOT, "src", "main.py")

# --------------------------------------------------------------------------
# 数据文件：(源路径, bundle 内相对目录)
# --------------------------------------------------------------------------
datas = []

_qss = os.path.join(ROOT, "src", "ui", "style.qss")
if os.path.isfile(_qss):
    datas.append((_qss, "src/ui"))

_resources = os.path.join(ROOT, "src", "resources")
if os.path.isdir(_resources):
    datas.append((_resources, "src/resources"))

# certifi 证书（打包后 HTTPS 校验必需）
try:
    import certifi

    datas.append((certifi.where(), "certifi"))
except Exception:  # noqa: BLE001 - certifi 缺失不应阻断打包
    pass

# --------------------------------------------------------------------------
# 二进制：ffmpeg / ffprobe
# --------------------------------------------------------------------------
binaries = []

_ffmpeg = os.path.join(ROOT, "vendor", "ffmpeg", "bin", "ffmpeg.exe")
if os.path.isfile(_ffmpeg):
    binaries.append((_ffmpeg, "vendor/ffmpeg/bin"))
else:
    print("[build.spec] 警告：未找到 vendor/ffmpeg/bin/ffmpeg.exe，打包产物将无法自动合并音视频")

_ffprobe = os.path.join(ROOT, "vendor", "ffmpeg", "bin", "ffprobe.exe")
if os.path.isfile(_ffprobe):
    binaries.append((_ffprobe, "vendor/ffmpeg/bin"))

# --------------------------------------------------------------------------
# MSVC 运行库兜底收集（保证干净 Windows 也能直接运行，无需装 VC++ 可再发行包）
# PyInstaller 会自动打包 vcruntime140.dll，但 Qt/PySide6 还依赖 msvcp140.dll、
# concrt140.dll；目标机若未安装 VC++ 2022 可再发行包会启动失败。这里显式收集。
# --------------------------------------------------------------------------
def _collect_vc_redist() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    names = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "concrt140.dll"]
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for name in names:
        p = os.path.join(sys32, name)
        if os.path.isfile(p):
            found.append((p, "."))
    if found:
        print(f"[build.spec] 已收集 MSVC 运行库：{', '.join(os.path.basename(f) for f, _ in found)}")
    else:
        print("[build.spec] 警告：未在 System32 找到 MSVC 运行库，干净目标机可能需先装 VC++ 2022 可再发行包")
    return found

binaries += _collect_vc_redist()

# --------------------------------------------------------------------------
# 隐藏导入
# --------------------------------------------------------------------------
hiddenimports = [
    # yt-dlp 的 extractor 是动态加载的，必须显式声明，否则打包后解析全部失败
    "yt_dlp",
    "yt_dlp.extractor",
    "yt_dlp.extractor.lazy_extractors",
    "yt_dlp.compat",
    "yt_dlp.networking",
    "yt_dlp.networking._requests",
    "yt_dlp.postprocessor",
    # Qt 模块
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
    # 内置浏览器登录（需求 #3）：QtWebEngine 必须显式声明，否则打包后
    # ``from PySide6.QtWebEngineWidgets import QWebEngineView`` 会失败
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    # QtWebEngine 的运行时强依赖：缺失任一个，打包产物在创建 QWebEngineView
    # 时会因找不到 Qt 插件/QML 后端而崩溃，故必须显式声明
    "PySide6.QtWebChannel",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQuickWidgets",
    # QML / Quick 也是 QtWebEngine 的运行时依赖（其进程内渲染后端用到），
    # 不能排除，这里显式声明以保证打包后登录浏览器可用
    "PySide6.QtQml",
    "PySide6.QtQuick",
    # 其他运行时依赖
    "certifi",
    "brotli",
    "mutagen",
    "websockets",
    "Cryptodome",
    "sqlite3",
]

try:
    from PyInstaller.utils.hooks import collect_submodules

    hiddenimports += collect_submodules("yt_dlp.extractor")
except Exception as exc:  # noqa: BLE001
    # 绝不能静默吞掉：收集失败会导致产物丢失全部 extractor，
    # 打包过程零报错，但运行时所有站点解析都失败。
    print("=" * 70)
    print(f"[build.spec] 严重警告：收集 yt_dlp.extractor 子模块失败：{exc}")
    print("[build.spec] 打包产物将无法解析任何站点！请修复后重新打包。")
    print("=" * 70)

# --------------------------------------------------------------------------
# 排除项：削减体积
# --------------------------------------------------------------------------
excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "test",
    "matplotlib",
    "numpy",
    "pandas",
    "PIL",
    # 注意：不可排除 PySide6.QtQml / PySide6.QtQuick —— QtWebEngine 内部依赖
    # 它们，排除后打包产物启动内置浏览器会直接崩溃（需求 #3 登录功能失效）
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
]

# --------------------------------------------------------------------------
# 图标
# --------------------------------------------------------------------------
_icon = os.path.join(ROOT, "src", "resources", "icons", "app.ico")
icon_path = _icon if os.path.isfile(_icon) else None
if icon_path is None:
    print("[build.spec] 提示：未找到 src/resources/icons/app.ico，将不带图标打包")


a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 会压坏 Qt / QtWebEngine 的 DLL（Qt6WebEngineCore.dll、qwindows.dll、
    # QtWebEngineProcess.exe），典型症状是启动时报
    # "could not find or load the Qt platform plugin windows" 或直接闪退。
    # 更麻烦的是：未安装 UPX 的机器上 PyInstaller 会静默跳过压缩，
    # 于是「发不发作取决于打包机环境」，属于随机地雷。
    # 带 QtWebEngine 后产物本就 200MB+，UPX 省下的体积不值这个风险，故关闭。
    upx=False,
    upx_exclude=["vcruntime140.dll", "python311.dll", "python312.dll", "python313.dll"],
    runtime_tmpdir=None,
    console=False,          # GUI 程序，不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    version=None,
)
