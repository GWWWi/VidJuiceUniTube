"""全局常量定义。

约定：
- 所有配置 key 必须在此定义为常量，业务代码禁止硬编码字符串字面量。
- 所有面向用户的中文文案映射集中在此，便于后续国际化。
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------
# 应用标识
# --------------------------------------------------------------------------
APP_NAME: Final[str] = "VidJuice"
APP_DISPLAY_NAME: Final[str] = "VidJuice UniTube"
ORG_NAME: Final[str] = "VidJuice"
ORG_DOMAIN: Final[str] = "vidjuice.local"
APP_VERSION: Final[str] = "1.0.0"

# --------------------------------------------------------------------------
# 默认路径与目录
# --------------------------------------------------------------------------
#: 默认下载子目录（相对 %USERPROFILE%）
DEFAULT_DOWNLOAD_SUBDIR: Final[str] = "Videos/VidJuice"
#: 配置文件名
CONFIG_FILENAME: Final[str] = "config.json"
#: 历史记录数据库文件名
HISTORY_DB_FILENAME: Final[str] = "history.db"
#: 日志子目录与文件名
LOG_SUBDIR: Final[str] = "logs"
LOG_FILENAME: Final[str] = "app.log"
#: 随包 ffmpeg 相对路径（PyInstaller bundle 内同样有效）
BUNDLED_FFMPEG_REL: Final[str] = "vendor/ffmpeg/bin/ffmpeg.exe"
#: 应用图标相对路径
APP_ICON_REL: Final[str] = "src/resources/icons/app.ico"
#: 样式表相对路径
STYLE_QSS_REL: Final[str] = "src/ui/style.qss"
#: 内置浏览器导出的 cookie 文件名（Netscape 格式，存于 %APPDATA%/VidJuice/）
COOKIES_FILENAME: Final[str] = "cookies.txt"

# --------------------------------------------------------------------------
# 默认行为参数
# --------------------------------------------------------------------------
DEFAULT_MAX_CONCURRENT: Final[int] = 3
DEFAULT_MAX_RETRIES: Final[int] = 3
#: 进度信号最小发送间隔（秒），用于节流，避免冲垮 Qt 事件循环
PROGRESS_EMIT_INTERVAL: Final[float] = 0.3
#: 全局统计定时刷新间隔（毫秒）
STATS_REFRESH_INTERVAL_MS: Final[int] = 1000
#: 网络 socket 超时（秒）
SOCKET_TIMEOUT: Final[int] = 30
#: yt-dlp 内部重试次数
YTDLP_INTERNAL_RETRIES: Final[int] = 3
#: ffmpeg 合并超时（秒）
FFMPEG_TIMEOUT: Final[int] = 600
#: 线程池在最大并发之外额外预留的线程数（供解析使用）
THREAD_POOL_EXTRA: Final[int] = 2
#: 应用退出时等待线程池的最长时间（毫秒）
SHUTDOWN_WAIT_MS: Final[int] = 3000
#: 并发数可配置范围
MIN_CONCURRENT: Final[int] = 1
MAX_CONCURRENT_LIMIT: Final[int] = 10
#: 重试次数可配置范围
MIN_RETRIES: Final[int] = 0
MAX_RETRIES_LIMIT: Final[int] = 10

#: yt-dlp 输出文件名模板（限制标题长度，规避 Windows 260 字符路径限制）
OUTPUT_TEMPLATE: Final[str] = "%(title).150B [%(id)s].%(ext)s"
#: 伪「最佳画质」格式的 format_id
BEST_FORMAT_ID: Final[str] = "bestvideo+bestaudio/best"
#: 伪「最佳画质」格式的展示文案
BEST_FORMAT_LABEL: Final[str] = "最佳画质（自动）"
#: 支持的容器格式
SUPPORTED_CONTAINERS: Final[tuple[str, ...]] = ("mp4", "mkv", "webm")

# --------------------------------------------------------------------------
# 配置 key 常量（唯一真源）
# --------------------------------------------------------------------------
CFG_DOWNLOAD_DIR: Final[str] = "download_dir"
CFG_MAX_CONCURRENT: Final[str] = "max_concurrent"
CFG_MAX_RETRIES: Final[str] = "max_retries"
CFG_DEFAULT_QUALITY: Final[str] = "default_quality"
CFG_DEFAULT_CONTAINER: Final[str] = "default_container"
CFG_COOKIEFILE: Final[str] = "cookiefile"
CFG_FFMPEG_PATH: Final[str] = "ffmpeg_path"
CFG_PROXY: Final[str] = "proxy"
CFG_AUTO_OPEN_FOLDER: Final[str] = "auto_open_folder"
CFG_THEME: Final[str] = "theme"
CFG_WINDOW_GEOMETRY: Final[str] = "window_geometry"

#: 全部配置 key 集合，便于校验
ALL_CONFIG_KEYS: Final[tuple[str, ...]] = (
    CFG_DOWNLOAD_DIR,
    CFG_MAX_CONCURRENT,
    CFG_MAX_RETRIES,
    CFG_DEFAULT_QUALITY,
    CFG_DEFAULT_CONTAINER,
    CFG_COOKIEFILE,
    CFG_FFMPEG_PATH,
    CFG_PROXY,
    CFG_AUTO_OPEN_FOLDER,
    CFG_THEME,
    CFG_WINDOW_GEOMETRY,
)

# --------------------------------------------------------------------------
# 状态与阶段文案（英文枚举值 → 中文展示）
# --------------------------------------------------------------------------
STATUS_TEXT_MAP: Final[dict[str, str]] = {
    "queued": "排队中",
    "parsing": "解析中",
    "downloading": "下载中",
    "merging": "合并中",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}

PHASE_DOWNLOADING: Final[str] = "downloading"
PHASE_MERGING: Final[str] = "merging"

PHASE_TEXT_MAP: Final[dict[str, str]] = {
    PHASE_DOWNLOADING: "下载中",
    PHASE_MERGING: "合并中",
}

# --------------------------------------------------------------------------
# 表格列定义（UI 与 Model 共用）
# --------------------------------------------------------------------------
TABLE_COLUMNS: Final[tuple[str, ...]] = ("文件名", "画质", "进度", "速度", "状态", "操作")
COL_TITLE: Final[int] = 0
COL_QUALITY: Final[int] = 1
COL_PROGRESS: Final[int] = 2
COL_SPEED: Final[int] = 3
COL_STATUS: Final[int] = 4
COL_ACTION: Final[int] = 5

# --------------------------------------------------------------------------
# 操作按钮动作标识（Delegate → View → MainWindow）
# --------------------------------------------------------------------------
ACTION_PAUSE: Final[str] = "pause"
ACTION_RESUME: Final[str] = "resume"
ACTION_CANCEL: Final[str] = "cancel"
ACTION_RETRY: Final[str] = "retry"
ACTION_REMOVE: Final[str] = "remove"
ACTION_OPEN_FOLDER: Final[str] = "open_folder"

ACTION_TEXT_MAP: Final[dict[str, str]] = {
    ACTION_PAUSE: "暂停",
    ACTION_RESUME: "继续",
    ACTION_CANCEL: "取消",
    ACTION_RETRY: "重试",
    ACTION_REMOVE: "移除",
    ACTION_OPEN_FOLDER: "打开目录",
}

# --------------------------------------------------------------------------
# UI 主题色
# --------------------------------------------------------------------------
COLOR_PRIMARY: Final[str] = "#2D7FF9"
COLOR_DANGER: Final[str] = "#E5484D"
COLOR_SUCCESS: Final[str] = "#2FA84F"
COLOR_TEXT_MUTED: Final[str] = "#8A94A6"

__all__ = [name for name in dir() if name.isupper()]
