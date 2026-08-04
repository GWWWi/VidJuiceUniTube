# Windows 验收清单

> 环境前提：Windows 10/11 + Python 3.13 + `pip install -r requirements.txt`
> **务必确认 PySide6-Addons 已装上**，否则内置浏览器登录不可用（QtWebEngine 由 Addons 提供，不在 Essentials 里）。

---

## 第 1 步 · 环境自检

```bat
python build.py --check
```

- ✅ 预期：`PyInstaller / yt-dlp / PySide6 / QtWebEngine 已安装` 四条 + `前置检查通过`
- ❌ 出现 `缺少 QtWebEngine` → 执行 `pip install PySide6-Addons` 后重试

> **不要跳过这步继续打包**。缺 Addons 时 EXE 照样能打出来，但登录功能会静默失效。

---

## 第 2 步 · 全量测试

```bat
python -m pytest -q
```

- ✅ 预期：`217 passed`（真机有 QtWebEngine，沙箱里 skip 的那个用例会真正执行）
- ❌ 进程无 traceback 直接退出、退出码 `-1073740791` / `3221226505` → 反馈给我们

> 请跑全量，不要单独跑某个测试文件。

---

## 第 3 步 · 源码态运行（本轮修复的核心）

```bat
python src\main.py
```

1. 点菜单「YouTube 登录（内置浏览器）」→ 应弹出内嵌浏览器并加载 YouTube
   - ❌ 弹 `RuntimeError: 当前环境缺少 QtWebEngine` → 回第 1 步
2. 在窗口内正常登录 YouTube 账号
3. 点「登录后保存 cookies」→ 应提示「已保存 N 条 cookie」
   - 检查 `%APPDATA%\VidJuice\cookies.txt` 存在，首行为 `# Netscape HTTP Cookie File`，每行 7 个 Tab 分隔字段
   - 检查 `%APPDATA%\VidJuice\webengine\` 目录已生成 —— **这是持久化目录，没有它说明持久化没生效**
4. **关闭登录对话框** → 观察控制台
   - ❌ 出现 `Release of profile requested but WebEnginePage still not deleted` 或闪退 → 反馈，一行可修
5. **完全退出应用，重新打开，再点登录** → 应**仍处于登录态、无需重新输密码**
   - ❌ 又要求重新登录 → 持久化 profile 没生效，请反馈
6. 拿一个**年龄限制**的 YouTube 视频粘进主界面解析 → 应能正常解析出清晰度列表

---

## 第 4 步 · 打包

```bat
python build.py
```

- ✅ 预期：`产物校验通过：dist\VidJuiceUniTube.exe（200-300 MB）`
- ❌ 日志出现 `严重警告：收集 yt_dlp.extractor 子模块失败` → **必须停下修复**，否则产物无法解析任何网站
- ⚠️ 产物 < 150MB → 八成是 QtWebEngine 没打进去，检查第 1 步

> 200-300MB 是正常体积（含 QtWebEngine）。**不要为了瘦身去砍 `build.spec` 的 excludes** —— 砍掉 `QtQml`/`QtQuick` 会让内置浏览器崩溃。

---

## 第 5 步 · 产物冒烟（最好换一台没装 Python 的机器）

1. 双击 `dist\VidJuiceUniTube.exe` → 应正常启动主界面
   - ❌ `could not find or load the Qt platform plugin windows` 或闪退 → UPX 压坏 Qt DLL 的典型症状（本轮已 `upx=False`，若仍出现请反馈）
2. 在 EXE 里重复第 3 步的登录 → 保存 cookies → 重启 EXE 验证登录态保持
3. 下载一个短视频，确认音视频已合并（需 `vendor\ffmpeg\bin\ffmpeg.exe` 就位）

---

## 已知限制

| 项 | 说明 |
|---|---|
| DRM 内容 | Netflix / Disney+ 等付费平台明确不支持，不绕过 DRM |
| ffmpeg | 需自行放置到 `vendor\ffmpeg\bin\ffmpeg.exe`，否则音视频分离的源无法合并 |
| yt-dlp 诊断被吞 | `no_warnings=True` 会吞掉 yt-dlp 警告。若机器无 JS runtime（deno/node），`default` 客户端只展开成 `android_vr`，部分画质缺失且无提示 |
| 登录态下 ios 客户端 | yt-dlp 会自动剔除 `ios`（不支持 cookie），由 `tv` / `web_safari` 兜底，属正常行为 |
| 换行风格 | 部分文件 CRLF、部分 LF，无 `.gitattributes`，长期会有 diff 噪音 |
| 临时文件 | 项目根目录有 17 个 `_*` 开头的开发期临时文件 + `__pycache__/`，沙箱权限限制未能删除，可手动清理（已在 `.gitignore` 中排除，不影响版本库） |

---

## 测试覆盖盲区（产品代码正确，仅缺测试保护）

- `apply_cookies` 未调 `config.save()` 的变异可逃逸 —— 当前代码是对的
- `build.py` 体积告警阈值变异可逃逸 —— 仅文案，无功能影响
