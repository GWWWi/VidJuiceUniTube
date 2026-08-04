"""内置浏览器登录（需求 #3）测试。

重点验证 Netscape cookies 文件写入格式正确，**不启动真实浏览器**。
涉及 ``PySide6.QtWebEngineWidgets`` 的用例在缺失该模块时通过
``pytest.importorskip`` 跳过并注明，避免在无桌面 / 精简 PySide6 的
CI 或沙箱环境中报错。

Netscape cookies.txt 每行 7 个 Tab 分隔字段：
``domain \\t includeSubDomains \\t path \\t secure \\t expiration \\t name \\t value``
首行固定为 ``# Netscape HTTP Cookie File``；会话 cookie 的 expiration 为 ``0``。
"""

from __future__ import annotations

import os
import unittest.mock as mock
from pathlib import Path

import pytest

from src.core.config_store import ConfigStore
from src.ui import login_browser
from src.ui.login_browser import apply_cookies, cookies_to_netscape


@pytest.fixture()
def cookie_factory():
    """构造 ``QNetworkCookie`` 的工厂（缺失 QtNetwork 时整体跳过）。"""
    QNetworkCookie = pytest.importorskip(
        "PySide6.QtNetwork", reason="未安装 PySide6.QtNetwork，跳过 cookie 格式测试"
    ).QNetworkCookie
    from PySide6.QtCore import QDateTime

    def _make(
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        secure: bool = False,
        expiration_secs: int | None = None,
    ) -> object:
        cookie = QNetworkCookie()
        cookie.setName(name.encode("utf-8"))
        cookie.setValue(value.encode("utf-8"))
        cookie.setDomain(domain)
        cookie.setPath(path)
        cookie.setSecure(secure)
        if expiration_secs is not None:
            cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(expiration_secs))
        return cookie

    return _make


# ======================================================================
# Netscape 文本格式（纯函数，不需要浏览器）
# ======================================================================
class TestNetscapeFormat:
    """cookie → Netscape 文本格式正确性。"""

    def test_header_and_secure_cookie(self, cookie_factory) -> None:
        """带过期时间与 secure 的 cookie 应正确写入所有字段。"""
        cookie = cookie_factory("CONSENT", "YES+cb", ".youtube.com", "/", secure=True, expiration_secs=1_700_000_000)
        text = cookies_to_netscape([cookie])

        lines = text.splitlines()
        assert lines[0] == "# Netscape HTTP Cookie File"
        # 末尾含换行，故最后一个空串来自尾换行
        data_line = lines[2]
        fields = data_line.split("\t")
        assert fields == [
            ".youtube.com",  # domain
            "TRUE",          # includeSubDomains（域名以 . 开头）
            "/",             # path
            "TRUE",          # secure
            "1700000000",    # expiration
            "CONSENT",       # name
            "YES+cb",        # value
        ]

    def test_session_cookie_expiration_is_zero(self, cookie_factory) -> None:
        """无过期时间的会话 cookie，expiration 字段必须为 ``0``。"""
        cookie = cookie_factory("SID", "abc123", "youtube.com", "/", secure=False)
        text = cookies_to_netscape([cookie])

        data_line = text.splitlines()[2]
        fields = data_line.split("\t")
        assert fields[0] == "youtube.com"
        assert fields[1] == "FALSE"  # 域名不以 . 开头
        assert fields[3] == "FALSE"  # 非 secure
        assert fields[4] == "0"      # 会话 cookie 过期时间

    def test_empty_list_still_valid(self) -> None:
        """空 cookie 列表返回带表头与末尾换行的合法空文件内容。"""
        text = cookies_to_netscape([])
        assert text.startswith("# Netscape HTTP Cookie File")
        assert text.endswith("\n")

    def test_skips_invalid_cookie(self, cookie_factory) -> None:
        """无 name 的非法 cookie 被跳过。"""
        bad = cookie_factory("", "v", ".youtube.com")
        good = cookie_factory("OK", "v", ".youtube.com")
        text = cookies_to_netscape([bad, good])
        # 仅 good 一行数据
        data_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert len(data_lines) == 1
        assert data_lines[0].split("\t")[5] == "OK"

    def test_tab_and_newline_stripped_from_value(self, cookie_factory) -> None:
        """value 内的 Tab / 换行被剔除，保证每行严格 7 字段。"""
        cookie = cookie_factory("SID", "a\tb\nc\rd", ".youtube.com")
        text = cookies_to_netscape([cookie])

        data_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert len(fields) == 7
        assert fields[6] == "abcd"

    def test_name_becoming_empty_after_sanitize_is_skipped(self, cookie_factory) -> None:
        """name 全由非法字符组成时，过滤后为空应整行跳过。"""
        cookie = cookie_factory("\t\n", "v", ".youtube.com")
        text = cookies_to_netscape([cookie])

        data_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert data_lines == []


# ======================================================================
# cookie 去重 / 移除（不需要浏览器：直接操作 _cookies_dict）
# ======================================================================
class TestCookieDedup:
    """``_cookie_key`` 去重语义与 cookieAdded / cookieRemoved 处理。"""

    @staticmethod
    def _bare_dialog() -> object:
        """构造复用真实 cookie 记账方法的轻量替身。

        ``LoginBrowserDialog`` 继承自 ``QDialog``，无法在不初始化 Qt 对象的
        情况下实例化；而待测的记账逻辑只依赖 ``_cookies_dict``，因此把真实
        的未绑定方法挂到一个普通对象上直接验证，避免拉起 WebEngine。
        """
        from src.ui.login_browser import LoginBrowserDialog

        class _CookieBook:
            _on_cookie_added = LoginBrowserDialog.__dict__["_on_cookie_added"]
            _on_cookie_removed = LoginBrowserDialog.__dict__["_on_cookie_removed"]
            _collect_cookies = LoginBrowserDialog.__dict__["_collect_cookies"]
            _cookie_key = LoginBrowserDialog.__dict__["_cookie_key"]

            def __init__(self) -> None:
                self._cookies_dict: dict = {}

        return _CookieBook()

    def test_key_includes_path(self, cookie_factory) -> None:
        """同名同域但不同 path 的 cookie 是两条独立凭据，不可互相顶掉。"""
        from src.ui.login_browser import LoginBrowserDialog

        root = cookie_factory("SID", "v1", ".youtube.com", "/")
        sub = cookie_factory("SID", "v2", ".youtube.com", "/watch")
        assert LoginBrowserDialog._cookie_key(root) != LoginBrowserDialog._cookie_key(sub)

    def test_later_cookie_wins(self, cookie_factory) -> None:
        """同键 cookie 后到覆盖先到（刷新后的新凭据生效）。"""
        dialog = self._bare_dialog()
        old = cookie_factory("SID", "old", ".youtube.com", "/")
        new = cookie_factory("SID", "new", ".youtube.com", "/")

        dialog._on_cookie_added(old)
        dialog._on_cookie_added(new)

        collected = dialog._collect_cookies()
        assert len(collected) == 1
        assert bytes(collected[0].value()) == b"new"

    def test_removed_cookie_dropped(self, cookie_factory) -> None:
        """cookieRemoved 后该 cookie 不再被写出。"""
        dialog = self._bare_dialog()
        keep = cookie_factory("KEEP", "v", ".youtube.com", "/")
        gone = cookie_factory("GONE", "v", ".youtube.com", "/")

        dialog._on_cookie_added(keep)
        dialog._on_cookie_added(gone)
        dialog._on_cookie_removed(gone)

        names = {bytes(c.name()) for c in dialog._collect_cookies()}
        assert names == {b"KEEP"}

    def test_removing_unknown_cookie_is_safe(self, cookie_factory) -> None:
        """移除未记录的 cookie 不应抛异常。"""
        dialog = self._bare_dialog()
        dialog._on_cookie_removed(cookie_factory("NOPE", "v", ".youtube.com", "/"))
        assert dialog._collect_cookies() == []


# ======================================================================
# 落盘 + 配置写入（不需要浏览器）
# ======================================================================
class TestApplyCookies:
    """``apply_cookies`` 写文件并更新配置。"""

    def test_writes_file_and_updates_config(self, config: ConfigStore, tmp_path: Path, cookie_factory) -> None:
        """写入 Netscape 文件并把路径写入配置。"""
        cookie = cookie_factory("VISITOR", "x", ".youtube.com", expiration_secs=1_700_000_000)
        path = apply_cookies(config, [cookie], base_dir=str(tmp_path))

        assert path == str(tmp_path / "cookies.txt")
        assert config.cookiefile() == path
        assert os.path.isfile(path)
        content = Path(path).read_text(encoding="utf-8")
        assert content.startswith("# Netscape HTTP Cookie File")
        assert "VISITOR" in content

    def test_empty_cookies_raises(self, config: ConfigStore, tmp_path: Path) -> None:
        """空 cookie 列表应抛 ``ValueError``。"""
        with pytest.raises(ValueError):
            apply_cookies(config, [], base_dir=str(tmp_path))


# ======================================================================
# 登录态检测：区分「已登录」与「仅访客」
# ======================================================================
class TestHasLoginState:
    """``has_login_state`` 命中关键 auth cookie 之一即视为已登录。"""

    def test_visitor_only_returns_false(self, cookie_factory) -> None:
        """只有访客 cookie 时返回 False（典型「登录未真正完成」场景）。"""
        from src.ui.login_browser import has_login_state

        cookies = [
            cookie_factory("VISITOR_INFO1_LIVE", "x", ".youtube.com"),
            cookie_factory("PREF", "tz=UTC", ".youtube.com"),
            cookie_factory("SOCS", "CAI", ".youtube.com"),
            cookie_factory("__Secure-YNID", "x", ".youtube.com"),
        ]
        assert has_login_state(cookies) is False

    @pytest.mark.parametrize(
        "marker",
        ["SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO", "HSID", "SSID"],
    )
    def test_any_auth_marker_returns_true(
        self, cookie_factory, marker: str
    ) -> None:
        """任一关键登录态 cookie 出现即返回 True。"""
        from src.ui.login_browser import has_login_state

        cookies = [
            cookie_factory("VISITOR_INFO1_LIVE", "x", ".youtube.com"),
            cookie_factory(marker, "auth-token", ".youtube.com"),
        ]
        assert has_login_state(cookies) is True

    def test_empty_list_returns_false(self) -> None:
        """空列表返回 False。"""
        from src.ui.login_browser import has_login_state

        assert has_login_state([]) is False
        assert has_login_state(None) is False  # type: ignore[arg-type]


# ======================================================================
# 对话框关闭时自动 export（核心 UX 修复）
# ======================================================================
class TestCloseEventAutoExport:
    """关闭登录对话框时自动 export cookies，修正「登录后忘点保存」的丢凭据。"""

    def test_close_event_exports_cookies(
        self, qt_app, config: ConfigStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有 cookies 时关闭对话框应自动写文件并更新 config。"""
        pytest.importorskip(
            "PySide6.QtWebEngineWidgets",
            reason="本环境未安装 PySide6.QtWebEngineWidgets，跳过",
        )
        import unittest.mock as mock

        from PySide6.QtWebEngineWidgets import QWebEngineView

        from src.ui.login_browser import LoginBrowserDialog, apply_cookies

        monkeypatch.setattr(login_browser, "app_data_dir", lambda: str(tmp_path))

        class _StubView(QWebEngineView):
            def load(self, url):  # noqa: ANN001, D102
                pass

        with mock.patch.object(login_browser, "QWebEngineView", _StubView):
            dialog = LoginBrowserDialog(config)

        # 模拟用户登录：往 _cookies_dict 注入带登录态的 cookie
        from PySide6.QtNetwork import QNetworkCookie

        auth_cookie = QNetworkCookie()
        auth_cookie.setName(b"__Secure-3PSID")
        auth_cookie.setValue(b"auth-token")
        auth_cookie.setDomain(".youtube.com")
        auth_cookie.setPath("/")
        dialog._on_cookie_added(auth_cookie)

        # 关闭对话框：应自动 export（不再依赖手动点保存按钮）
        dialog.close()

        cookie_file = tmp_path / "cookies.txt"
        assert cookie_file.is_file(), "关闭对话框应自动写入 cookies.txt"
        assert config.cookiefile() == str(cookie_file)
        assert "__Secure-3PSID" in cookie_file.read_text(encoding="utf-8")

    def test_close_event_skips_when_no_cookies(
        self, qt_app, config: ConfigStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cookies 列表为空时关闭对话框不应写文件（避免空文件覆盖有效配置）。"""
        pytest.importorskip(
            "PySide6.QtWebEngineWidgets",
            reason="本环境未安装 PySide6.QtWebEngineWidgets，跳过",
        )
        import unittest.mock as mock

        from PySide6.QtWebEngineWidgets import QWebEngineView

        from src.ui.login_browser import LoginBrowserDialog

        monkeypatch.setattr(login_browser, "app_data_dir", lambda: str(tmp_path))

        class _StubView(QWebEngineView):
            def load(self, url):  # noqa: ANN001, D102
                pass

        with mock.patch.object(login_browser, "QWebEngineView", _StubView):
            dialog = LoginBrowserDialog(config)

        # 不注入任何 cookie，直接关闭
        dialog.close()

        assert not (tmp_path / "cookies.txt").exists()
        # config 也未被意外写脏
        assert config.cookiefile() is None


# ======================================================================
# 模块 / 对话框可用性（不启动浏览器）
# ======================================================================
class TestDialogAvailability:
    """对话框类可导入，且在缺少 WebEngine 时优雅降级。"""

    def test_module_importable(self) -> None:
        """模块即使缺少 WebEngine 也应可导入（不阻断导入冒烟测试）。"""
        from src.ui.login_browser import LoginBrowserDialog

        assert LoginBrowserDialog is not None

    def test_init_raises_without_webengine(self, config: ConfigStore, monkeypatch: pytest.MonkeyPatch) -> None:
        """模拟缺少 WebEngine 时，实例化应抛 ``RuntimeError`` 而非崩溃。"""
        monkeypatch.setattr(login_browser, "_HAS_WEBENGINE", False)
        from src.ui.login_browser import LoginBrowserDialog

        with pytest.raises(RuntimeError):
            LoginBrowserDialog(config)


class TestWebEngineInstantiation:
    """需要真实 QtWebEngine 的用例；缺失时跳过并注明。"""

    def test_dialog_instantiates_with_webengine(
        self,
        qt_app,  # noqa: ANN001 - 必须声明：本用例真的会构造 QDialog
        config: ConfigStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """若环境提供 QtWebEngine，对话框应能构建（不发起真实网络请求）。

        必须依赖 ``qt_app``：本用例会真正创建 ``QDialog``，而无 QApplication 时
        构造 QWidget 会让整个 pytest 进程以 0xC0000409 静默崩溃（无 traceback、
        无 stderr）。跑全量时恰好被字母序在前的 ``test_download_manager.py``
        拉起的 session 级 QApplication 兜住，但单独跑本文件就会崩。
        """
        pytest.importorskip(
            "PySide6.QtWebEngineWidgets",
            reason="本环境未安装 PySide6.QtWebEngineWidgets，跳过（用户 Windows 机可用）",
        )
        from PySide6.QtCore import QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView

        from src.ui.login_browser import DEFAULT_LOGIN_URL, LoginBrowserDialog

        # 持久化 profile 会真的建目录，重定向到 tmp_path，避免污染真实 %APPDATA%
        monkeypatch.setattr(login_browser, "app_data_dir", lambda: str(tmp_path))

        # 阻止真实网络请求：用不发起 load 的桩替换 QWebEngineView
        class _StubView(QWebEngineView):
            def load(self, url):  # noqa: ANN001, D102
                pass

        with mock.patch.object(login_browser, "QWebEngineView", _StubView):
            dialog = LoginBrowserDialog(config)

        assert isinstance(dialog, LoginBrowserDialog)
        # 默认起始地址应为 YouTube 登录页
        assert dialog._url_edit.text() == DEFAULT_LOGIN_URL
        # 默认按钮存在（不直接 exec 以免弹窗阻塞）
        assert dialog._save_btn is not None
        # 校验 QUrl 可被正常构造（确保地址栏默认值合法）
        assert QUrl(DEFAULT_LOGIN_URL).isValid()
