"""内置浏览器登录 YouTube 并自动导出 cookies。

需求 #3：应用内嵌真实浏览器，用户登录 YouTube 后自动抓取 cookies，
写成 Netscape 格式 ``cookies.txt`` 到 ``%APPDATA%/VidJuice/``，并自动
写入配置，使 ``YtDlpClient`` 下次解析即用上（与年龄验证打通）。

设计要点：
- 仅在 ``PySide6.QtWebEngineWidgets`` / ``QtWebEngineCore`` 可用时才真正
  实例化浏览器；这两个模块在部分环境（如精简 PySide6、CI 沙箱）未安装。
  为了让本模块**始终可被导入**（不阻塞导入冒烟测试与主窗口启动），
  对 WebEngine 相关符号采用 ``try/except`` 懒加载，缺失时置 ``_HAS_WEBENGINE
  = False``，``LoginBrowserDialog.__init__`` 会抛 ``RuntimeError`` 提示用户。
- Netscape 文本转换（``cookies_to_netscape``）与落盘（``apply_cookies``）抽成
  纯函数，不依赖浏览器，便于单测。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.constants import COOKIES_FILENAME
from src.core.config_store import ConfigStore
from src.utils.paths import app_data_dir

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 可选依赖：QtWebEngine（部分环境未安装，需优雅降级）
# --------------------------------------------------------------------------
try:  # pragma: no cover - 依赖可用性随运行环境变化
    from PySide6.QtNetwork import QNetworkCookie

    from PySide6.QtWebEngineCore import (
        QWebEngineCookieStore,
        QWebEnginePage,
        QWebEngineProfile,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE: bool = True
except Exception:  # noqa: BLE001 - 缺失时降级，不阻断导入
    _HAS_WEBENGINE = False
    QNetworkCookie = None  # type: ignore[assignment]
    QWebEngineCookieStore = None  # type: ignore[assignment]
    QWebEnginePage = None  # type: ignore[assignment]
    QWebEngineProfile = None  # type: ignore[assignment]
    QWebEngineView = None  # type: ignore[assignment]


#: 内置浏览器默认起始地址
DEFAULT_LOGIN_URL: str = "https://www.youtube.com/?hl=zh-CN"

#: 具名（= 持久化）WebEngine profile 名称；具名 profile 的 cookie 会落盘，
#: 用户下次打开应用无需重新登录 YouTube
_PERSISTENT_PROFILE_NAME: str = "VidJuiceLogin"

#: WebEngine 持久化数据在 app_data_dir() 下的子目录名
_WEBENGINE_SUBDIR: str = "webengine"

#: Netscape cookies 文件首行标记
_NETSCAPE_HEADER: str = "# Netscape HTTP Cookie File"

#: Netscape 以 Tab 分隔字段、以换行分隔记录，name/value 中混入这些字符会
#: 破坏 7 字段结构，写入前必须剔除
_FIELD_BREAKING_CHARS: tuple[str, ...] = ("\t", "\r", "\n")

#: 判定「已登录 YouTube」的关键 cookie 名集合。
#: 任一出现即认为本次登录已成功建立会话（足以访问年龄限制 / 会员内容）。
#: 来源：YouTube 鉴权依赖 SAPISID + 一对 __Secure-*PSID；LOGIN_INFO 是 YouTube
#: 自身业务登录态。
YOUTUBE_AUTH_MARKERS: frozenset[str] = frozenset(
    {"SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO", "HSID", "SSID"}
)


def has_login_state(cookies: list[Any]) -> bool:
    """判断 cookies 列表是否包含 YouTube 登录态标记。

    背景：登录对话框导出的是**浏览器看到的所有 cookie**（含访客 cookie），
    如果用户其实没登录成功（比如 2FA 中断、被 YouTube 拒绝、页面未跳转），
    导出的 cookies.txt 里就只有 ``VISITOR_INFO1_LIVE`` 之类的访客 cookie，
    yt-dlp 拿去请求年龄限制视频仍会报「需要登录」。这种情形在保存时主动
    提示，能帮用户少走一轮「导出→解析失败→检查文件」的回路。

    Args:
        cookies: 任意具备 ``name()`` 接口的 cookie 对象列表；为 ``None`` 时按空处理。

    Returns:
        列表中任一 cookie 名命中 ``YOUTUBE_AUTH_MARKERS`` 即返回 ``True``；
        空列表 / 全部非登录态 / 取名失败均返回 ``False``。
    """
    if not cookies:
        return False
    names: set[str] = set()
    for cookie in cookies:
        try:
            raw = bytes(cookie.name())
        except Exception:  # noqa: BLE001 - 信号槽来源，宁可漏判也不抛
            continue
        names.add(raw.decode("utf-8", "replace"))
    return bool(names & YOUTUBE_AUTH_MARKERS)


# ==========================================================================
# 纯函数：cookie → Netscape 文本（不依赖浏览器，可单测）
# ==========================================================================
def _sanitize_field(text: str) -> str:
    """剔除会破坏 Netscape 7 字段结构的字符。

    Args:
        text: 原始字段文本（cookie 的 name 或 value）。

    Returns:
        去掉 Tab / CR / LF 后的文本。
    """
    cleaned = text
    for char in _FIELD_BREAKING_CHARS:
        if char in cleaned:
            cleaned = cleaned.replace(char, "")
    return cleaned


def _cookie_to_netscape_line(cookie: Any) -> str:
    """把单个 cookie 转成 Netscape cookies.txt 的一行。

    Netscape 格式每行 7 个以 Tab 分隔的字段：
    ``domain \\t includeSubDomains \\t path \\t secure \\t expiration \\t name \\t value``

    Args:
        cookie: 任意具备 ``name()`` / ``value()`` / ``domain()`` / ``path()`` /
            ``isSecure()`` / ``expirationDate()`` 接口的 cookie 对象。

    Returns:
        Tab 分隔的一行字符串（不含换行符）；字段缺失时返回空串。
    """
    try:
        raw_name = bytes(cookie.name())
        raw_value = bytes(cookie.value())
    except Exception:  # noqa: BLE001 - 非法 cookie 直接跳过
        return ""
    if not raw_name:
        return ""

    # name / value 若含 Tab 或换行会破坏 7 字段结构，写入前统一过滤
    name = _sanitize_field(raw_name.decode("utf-8", "replace"))
    value = _sanitize_field(raw_value.decode("utf-8", "replace"))
    if not name:
        # 过滤后 name 变空则该行无意义，跳过
        return ""

    domain = _sanitize_field(str(cookie.domain() or ""))
    path = _sanitize_field(str(cookie.path() or "/")) or "/"
    secure = "TRUE" if bool(cookie.isSecure()) else "FALSE"
    # 域名以 "." 开头表示包含子域；与 Netscape 约定一致
    include_sub = "TRUE" if domain.startswith(".") else "FALSE"

    expiration_date = cookie.expirationDate()
    if expiration_date is None or not expiration_date.isValid():
        # 会话 cookie：无过期时间，写为 0
        expiration = "0"
    else:
        expiration = str(int(expiration_date.toSecsSinceEpoch()))

    return "\t".join([domain, include_sub, path, secure, expiration, name, value])


def cookies_to_netscape(cookies: list[Any]) -> str:
    """把 cookie 列表转成完整的 Netscape cookies.txt 文本。

    Args:
        cookies: cookie 对象列表。

    Returns:
        以 ``# Netscape HTTP Cookie File`` 开头、以换行结尾的完整文本。
        空列表时仍返回带表头与末尾换行的合法空文件内容。
    """
    lines: list[str] = [_NETSCAPE_HEADER, ""]
    for cookie in cookies or []:
        line = _cookie_to_netscape_line(cookie)
        if line:
            lines.append(line)
    return "\n".join(lines) + "\n"


def apply_cookies(config: ConfigStore, cookies: list[Any], base_dir: str | None = None) -> str:
    """把 cookies 写成 Netscape 文件并写入配置，返回文件路径。

    供 ``LoginBrowserDialog`` 在用户点击保存时调用，也便于单测（无需真实浏览器）。

    Args:
        config: 配置存取器；会写入 ``cookiefile`` 并 ``save()``。
        cookies: 收集到的 cookie 列表。
        base_dir: 文件基目录；``None`` 时使用 ``app_data_dir()``。

    Returns:
        写出的 ``cookies.txt`` 绝对路径。

    Raises:
        ValueError: cookies 为空。
    """
    if not cookies:
        raise ValueError("没有可保存的 cookie")

    base = base_dir or app_data_dir()
    path = os.path.join(base, COOKIES_FILENAME)

    text = cookies_to_netscape(cookies)
    Path(path).write_text(text, encoding="utf-8")

    config.set_cookiefile(path)
    config.save()
    logger.info("已导出 %d 条 cookie 到 %s", len(cookies), path)
    return path


# ==========================================================================
# 内置浏览器登录对话框
# ==========================================================================
class LoginBrowserDialog(QDialog):
    """用内嵌浏览器登录 YouTube 并导出 cookies 的对话框。

    仅当运行环境安装了 ``PySide6.QtWebEngineWidgets`` 时才能实例化；
    否则 ``__init__`` 抛 ``RuntimeError``。
    """

    def __init__(self, config: ConfigStore, parent: Any = None) -> None:
        """构建界面并连接 cookie 采集信号。

        Args:
            config: 配置存取器；保存时写入 ``cookiefile``。
            parent: 父窗口。

        Raises:
            RuntimeError: 运行环境缺少 QtWebEngine 模块。
        """
        if not _HAS_WEBENGINE:
            raise RuntimeError(
                "当前环境缺少 PySide6 的 QtWebEngine 模块，无法使用内置浏览器登录。"
                "请在已安装 QtWebEngine 的桌面环境中运行（如 Windows 版 PySide6）。"
            )

        super().__init__(parent)
        self._config: ConfigStore = config
        #: 通过 cookieAdded / cookieRemoved 信号维护的 cookie 表。
        #: 键为经 ``_sanitize_field`` 归一后的 ``(name, domain, path)``，
        #: 同键后到覆盖先到（last-wins），保证刷新后的新凭据不会被旧值顶掉。
        self._cookies_dict: dict[tuple[str, str, str], Any] = {}

        self.setObjectName("loginBrowserDialog")
        self.setWindowTitle("YouTube 登录（内置浏览器）")
        self.resize(1000, 720)

        self._url_edit = QLineEdit(self)
        self._url_edit.setText(DEFAULT_LOGIN_URL)

        self._go_btn = QPushButton("前往", self)
        self._go_btn.clicked.connect(self._on_go)

        self._save_btn = QPushButton("登录后保存 cookies", self)
        self._save_btn.setObjectName("saveCookiesButton")
        self._save_btn.clicked.connect(self._on_save)

        # 具名 profile = 持久化；QWebEngineProfile.defaultProfile() 是
        # off-the-record（无痕），cookie 不落盘，应用一重启登录态就没了。
        # 析构顺序约束见下方 _page 处：profile 必须比它的 page 活得久，
        # 因此 page 挂在 profile 名下，而不是和 profile 平级挂在 dialog 上。
        self._profile = QWebEngineProfile(_PERSISTENT_PROFILE_NAME, self)
        storage_root = Path(app_data_dir()) / _WEBENGINE_SUBDIR
        self._profile.setPersistentStoragePath(str(storage_root))
        self._profile.setCachePath(str(storage_root / "Cache"))
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

        self._store: Any = self._profile.cookieStore()
        self._store.cookieAdded.connect(self._on_cookie_added)
        self._store.cookieRemoved.connect(self._on_cookie_removed)
        # QWebEngineCookieStore 没有同步的 allCookies()；loadAllCookies() 会
        # 异步把 store 中已持久化的 cookie 通过 cookieAdded 信号回放一遍。
        # 配合上面的持久化 profile，上次登录留下的 cookie 才能在本次启动被
        # 回放进 _cookies_dict —— 若用的是无痕 defaultProfile，store 里没有
        # 任何历史 cookie，这一步就是空转。
        self._load_existing_cookies()

        # 必须让 view 使用上面的具名 profile：QWebEngineView() 默认走
        # defaultProfile，不显式 setPage 的话前面的持久化配置等于没生效。
        #
        # page 的 parent 必须是 **_profile 而非 self**：Qt 按「添加顺序正序」
        # 销毁 children，profile 先挂到 dialog，若 page 也挂 dialog，则
        # profile 会先于 page 析构 —— 违反 Qt「profile 必须比其 page 活得久」
        # 的要求，轻则打印
        # "Release of profile requested but WebEnginePage still not deleted.
        #  Expect troubles!"，重则关闭对话框时直接崩溃。
        # 挂到 profile 名下后，parent 必然后于 child 析构，顺序才正确。
        self._view = QWebEngineView(self)
        self._page = QWebEnginePage(self._profile, self._profile)
        self._view.setPage(self._page)

        self._build_layout()
        self._view.load(QUrl(DEFAULT_LOGIN_URL))

    def _build_layout(self) -> None:
        """组装地址栏 + 浏览器视图 + 保存按钮。"""
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._url_edit, 1)
        top.addWidget(self._go_btn, 0)
        top.addWidget(self._save_btn, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self._view, 1)

    # ------------------------------------------------------------------
    # 信号 / 槽
    # ------------------------------------------------------------------
    def _on_go(self) -> None:
        """地址栏「前往」：加载输入网址。"""
        url = self._url_edit.text().strip()
        if url:
            self._view.load(QUrl(url))

    def _load_existing_cookies(self) -> None:
        """请求 store 回放既有 cookie（异步，经 ``cookieAdded`` 送达）。"""
        try:
            self._store.loadAllCookies()
        except Exception:  # noqa: BLE001 - 回放失败不应阻断对话框构建
            logger.debug("loadAllCookies 调用失败，仅收集本次会话新增的 cookie")

    def _on_cookie_added(self, cookie: Any) -> None:
        """cookieStore 新增 cookie 时收集（同键后到覆盖先到）。"""
        self._cookies_dict[self._cookie_key(cookie)] = cookie

    def _on_cookie_removed(self, cookie: Any) -> None:
        """cookieStore 删除 cookie 时同步移除，避免写出过期/失效凭据。"""
        self._cookies_dict.pop(self._cookie_key(cookie), None)

    @property
    def _cookies(self) -> list[Any]:
        """当前已收集的 cookie 列表（只读视图，便于调试与测试）。"""
        return list(self._cookies_dict.values())

    def _collect_cookies(self) -> list[Any]:
        """返回本会话经 ``cookieAdded`` 收集且未被移除的全部 cookie。

        ``QWebEngineCookieStore`` 不提供同步的 ``allCookies()``，既有 cookie
        由 ``__init__`` 中的 ``loadAllCookies()`` 异步回放进来，因此这里只需
        读取 ``_cookies_dict``。
        """
        return list(self._cookies_dict.values())

    @staticmethod
    def _cookie_key(cookie: Any) -> tuple[str, str, str]:
        """生成 cookie 去重键 ``(name, domain, path)``。

        两个要点：

        1. 必须含 ``path``：同名同域但不同 path 的 cookie 是两条独立凭据，
           只用 ``(name, domain)`` 会互相顶掉。
        2. 三个字段都要过 ``_sanitize_field``，与 ``_cookie_to_netscape_line``
           写盘时的处理**严格同源**。否则 ``"SID"`` 与 ``"SI\\tD"`` 会被算作
           两个不同的键各存一份，落盘时又被过滤成同名，写出两行重复凭据。

        整个键构造包在 try/except 里：本方法由 ``cookieAdded`` /
        ``cookieRemoved`` 信号槽调用，异常若穿透到 C++ 事件循环，PySide6 下
        行为不可控。取值失败时返回哨兵键，让该 cookie 落到同一个槽位而不是
        让程序崩掉。
        """
        try:
            name = _sanitize_field(bytes(cookie.name()).decode("utf-8", "replace"))
            domain = _sanitize_field(str(cookie.domain() or ""))
            path = _sanitize_field(str(cookie.path() or "/")) or "/"
            return (name, domain, path)
        except Exception:  # noqa: BLE001 - 信号槽内绝不能抛到 C++ 事件循环
            logger.debug("构造 cookie 去重键失败，归入哨兵键")
            return ("", "", "/")

    def _on_save(self) -> None:
        """保存 cookies：写 Netscape 文件、写入配置、提示用户。"""
        cookies = self._collect_cookies()
        if not cookies:
            QMessageBox.warning(
                self,
                "尚未捕获到 cookie",
                "浏览器尚未捕获到任何 cookie。请先在页面内登录 YouTube 账号"
                "（需年满 18 岁），登录成功后再点「保存」。",
            )
            return

        try:
            path = apply_cookies(self._config, cookies)
        except ValueError as exc:  # pragma: no cover - 理论上已被上方空判断拦截
            QMessageBox.warning(self, "保存失败", str(exc))
            return

        # 导出了，但**未必**包含登录态：可能登录流程未真正完成（2FA 中断、
        # 设备验证被拒、页面停留在登录前），此时 cookies.txt 里只有访客
        # cookie，yt-dlp 拿去请求仍会被识别为「未登录」。主动提示节省一轮
        # 「导出→解析失败→回头查文件」的回路。
        if has_login_state(cookies):
            QMessageBox.information(
                self,
                "已保存",
                f"已保存 {len(cookies)} 条 cookie 到：\n{path}\n"
                "下次解析 YouTube 将自动使用（含年龄限制 / 会员内容）。",
            )
        else:
            QMessageBox.warning(
                self,
                "已保存，但未检测到登录态",
                f"已导出 {len(cookies)} 条 cookie 到：\n{path}\n\n"
                "但其中没有 SAPISID / __Secure-3PSID / LOGIN_INFO 等登录态 cookie，"
                "说明本次登录可能未真正完成（被中断、2FA 未通过、或页面停留在登录前）。\n\n"
                "请重新打开本对话框，确认 YouTube 已显示你的头像/频道名后再保存，"
                "否则下载年龄限制视频仍会报「需要登录」。",
            )
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: ANN001 - 覆写 Qt 基类签名
        """窗口关闭（点 X、按 Esc、Alt+F4 等）时自动 export。

        为什么需要：用户登录完直接关窗是常见操作路径，但手动点「保存」按钮
        容易漏——结果 WebEngine profile 里可能有登录态，``cookies.txt`` 却是
        旧的或空。自动 export 把"登录即保存"的预期对齐到默认行为，「保存」
        按钮则降级为"显式重新 export"的入口（测试 / 用户想强制刷新时用）。
        """
        cookies = self._collect_cookies()
        if cookies:
            try:
                path = apply_cookies(self._config, cookies)
                logger.info(
                    "登录对话框关闭，自动 export %d 条 cookie 到 %s", len(cookies), path
                )
            except Exception as exc:  # noqa: BLE001 - 关闭路径绝不能弹错打断用户
                logger.warning("关闭时自动 export cookies 失败：%s", exc)
        super().closeEvent(event)


__all__ = [
    "DEFAULT_LOGIN_URL",
    "LoginBrowserDialog",
    "YOUTUBE_AUTH_MARKERS",
    "apply_cookies",
    "cookies_to_netscape",
    "has_login_state",
]
