# VidJuice UniTube

一款 Windows 桌面视频下载器（类 VidJuice UniTube / Downie），基于 **Python 3.11+ / PySide6 (Qt6) / yt-dlp / ffmpeg**。

支持粘贴链接解析视频信息、选择画质与容器格式、多任务并发下载、暂停/继续/取消、
音视频自动合并、失败自动重试、下载历史记录与配置持久化。

---

## 功能一览

| 能力 | 说明 |
|---|---|
| 链接解析 | 粘贴 URL 后解析标题、时长、UP 主、可用画质列表 |
| 画质选择 | 默认「最佳画质（自动）」，也可手动选择具体分辨率/编码 |
| 并发下载 | 默认最多 3 个任务同时下载，超出部分自动排队（可在设置中调整 1–10） |
| 暂停/继续 | 基于 `.part` 断点续传，暂停后可恢复 |
| 取消 | 取消并清理 `.part` / `.ytdl` 临时文件 |
| 自动合并 | 1080p 以上 DASH 流自动用 ffmpeg 合并为单一 mp4 |
| 失败重试 | 指数退避自动重试（默认 3 次）；DRM / 不支持的站点等不可重试错误直接失败 |
| 历史记录 | SQLite 持久化已完成任务，支持搜索、打开文件、打开目录、重新下载 |
| 配置持久化 | 下载目录、并发数、重试次数、ffmpeg 路径、cookie、代理等 |

---

## 环境要求

- Windows 10 / 11 (x64)
- Python 3.11 或更高版本
- ffmpeg（用于音视频合并，见下文）

---

## 快速开始

```bash
# 1. 创建虚拟环境（可选但推荐）
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements-dev.txt

# 3. 运行
python -m src.main
```

### 运行测试

```bash
pytest
```

核心测试（`test_models` / `test_config_store` / `test_history_store` /
`test_ytdlp_client` / `test_download_manager`）全部使用 mock，**不依赖真实网络**。

---

## ffmpeg 获取方式

程序按以下顺序定位 ffmpeg：

1. 设置对话框中手动指定的 `ffmpeg_path`
2. 随包资源目录 `vendor/ffmpeg/bin/ffmpeg.exe`
3. 系统 `PATH` 中的 `ffmpeg`

推荐下载地址（Windows 静态构建）：

- gyan.dev: <https://www.gyan.dev/ffmpeg/builds/>（下载 `ffmpeg-release-essentials.zip`）
- BtbN: <https://github.com/BtbN/FFmpeg-Builds/releases>

解压后把 `ffmpeg.exe`（可选 `ffprobe.exe`）放到项目下的 `vendor/ffmpeg/bin/` 目录：

```
VidJuiceUniTube/
└── vendor/
    └── ffmpeg/
        └── bin/
            ├── ffmpeg.exe
            └── ffprobe.exe
```

> 未安装 ffmpeg 时，仅能下载「音视频已合流」的单文件格式（一般 ≤ 720p）。

---

## 打包为 exe

```bash
python build.py
```

脚本会清理 `build/`、`dist/`，调用 PyInstaller 按 `build.spec` 打包，并校验产物。

> 本版本仅提交打包配置与脚本，**不产出实际 exe 构建产物**。

---

## 项目结构

```
src/
├── main.py            程序入口
├── app.py             依赖装配（build_app）
├── constants.py       全局常量与配置 key
├── core/              业务核心（纯 Python，无 Qt UI 依赖）
│   ├── models.py           数据模型
│   ├── config_store.py     配置读写
│   ├── history_store.py    历史记录（SQLite）
│   ├── ytdlp_client.py     yt-dlp 封装
│   ├── ffmpeg_merger.py    ffmpeg 合并
│   ├── format_utils.py     体积/速度/时长格式化
│   ├── exceptions.py       自定义异常
│   └── download_manager.py 调度核心
├── workers/           Qt 工作线程层
├── ui/                PySide6 视图层
├── utils/             路径 / 子进程 / 日志工具
└── resources/         图标等资源
```

---

## 数据存放位置

| 内容 | 路径 |
|---|---|
| 配置文件 | `%APPDATA%\VidJuice\config.json` |
| 历史记录 | `%APPDATA%\VidJuice\history.db` |
| 日志 | `%APPDATA%\VidJuice\logs\app.log`（5MB × 3 滚动） |
| 默认下载目录 | `%USERPROFILE%\Videos\VidJuice` |

---

## 已知限制

- **不支持 DRM 加密平台**（Netflix / Disney+ / Prime Video / 爱奇艺会员等），
  这类内容受版权技术保护，本软件不提供任何绕过手段。
- 播放列表链接仅解析并下载**第一个**视频（`noplaylist=True`）。
- 暂停中的任务在应用退出后不会自动恢复（`.part` 文件保留，需手动重新添加）。
- 需登录站点请在「设置 → cookie 文件」中指定 Netscape 格式的 cookies.txt。
- 本版仅提供中文界面与浅色主题。

---

## 免责声明

本软件仅供个人学习与备份**已获授权内容**使用。请遵守目标网站的服务条款及所在
地区的法律法规，勿用于侵犯版权的用途。

---

## License

见 [LICENSE](LICENSE)。
