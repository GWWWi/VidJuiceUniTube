"""``LoginBrowserDialog.__init__`` 与打包配置的回归测试（QA 第 3 轮）。

补上变异测试暴露的盲区——以下改动在本文件出现前都能「测试全绿」地溜过去：

- M6  : ``__init__`` 不再调 ``loadAllCookies()``
- M6b : ``cookieRemoved`` 信号不再连接
- M8  : build.spec 把 QtQml / QtQuick 重新加回 excludes
- M9  : Netscape 行的 path 为空时不再回退 ``/``
- 新增：profile 退回无痕 ``defaultProfile()``（登录态不落盘）
- 新增：view 未绑定持久化 profile（改了等于没改）
- 新增：build.spec 重新开启 UPX（压坏 Qt DLL）
- 新增：build.py 打包前不再硬校验 QtWebEngine

设计要点：用假的 ``QWebEngineProfile`` / ``QWebEnginePage`` / ``QWebEngineView``
注入，在**没有** QtWebEngine 的环境下也能真实执行 ``LoginBrowserDialog.__init__``，
因此不依赖 ``pytest.importorskip`` 跳过——这正是原先 M6 / M6b 能逃逸的原因
（唯一覆盖它们的用例在无 QtWebEngine 环境被 skip 掉了）。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from src.core.config_store import ConfigStore
from src.ui import login_browser as LB

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ======================================================================
# 假 WebEngine：让 __init__ 在无 QtWebEngine 的环境也能真实执行
# ======================================================================
class _FakeSignal:
    """最小可用的信号替身，只需支持 connect / emit。"""

    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, slot) -> None:  # noqa: ANN001, D102
        self.slots.append(slot)

    def emit(self, *args) -> None:  # noqa: D102
        for slot in self.slots:
            slot(*args)


class _FakeCookieStore:
    """模拟 ``QWebEngineCookieStore``：**故意不提供** ``allCookies()``。

    真实的 ``QWebEngineCookieStore`` 没有这个方法，源码里若再出现该调用
    会直接 ``AttributeError``——本替身如实还原这一点。
    """

    def __init__(self, raise_on_load: bool = False) -> None:
        self.cookieAdded = _FakeSignal()
        self.cookieRemoved = _FakeSignal()
        self.load_calls = 0
        self._raise = raise_on_load

    def loadAllCookies(self) -> None:  # noqa: N802, D102
        self.load_calls += 1
        if self._raise:
            raise RuntimeError("simulated Qt failure")


@pytest.fixture()
def fake_webengine(qt_app, monkeypatch: pytest.MonkeyPatch):
    """注入假 WebEngine，返回构建对话框的工厂。

    依赖 conftest 的 ``qt_app``（**必须是 QApplication**，否则创建 QWidget
    会让整个 pytest 进程以 0xC0000409 静默崩溃）。
    """
    from PySide6.QtWidgets import QWidget

    holder: dict = {}

    class _FakeView(QWidget):
        """替身 view：记录 setPage 与 load，不发起真实网络请求。"""

        def __init__(self, parent=None) -> None:  # noqa: ANN001
            super().__init__(parent)
            self.page_obj = None

        def setPage(self, page) -> None:  # noqa: ANN001, N802, D102
            self.page_obj = page

        def load(self, url) -> None:  # noqa: ANN001, D102
            holder.setdefault("loads", []).append(url.toString())

    class _FakePage:
        """替身 page：记录它是用哪个 profile 建的。"""

        def __init__(self, profile, parent=None) -> None:  # noqa: ANN001
            self.profile = profile
            self.parent_obj = parent

    class _FakeProfile:
        """替身 profile：记录持久化配置。

        ``defaultProfile()`` 故意抛断言错误——它是 off-the-record 的，
        用它就意味着登录态不落盘，属于本轮明确修掉的缺陷。
        """

        class PersistentCookiesPolicy:  # noqa: D106
            ForcePersistentCookies = "ForcePersistentCookies"
            NoPersistentCookies = "NoPersistentCookies"

        def __init__(self, name=None, parent=None) -> None:  # noqa: ANN001
            self.name = name
            self.parent_obj = parent
            self.storage_path: str | None = None
            self.cache_path: str | None = None
            self.cookies_policy: str | None = None
            holder["profile"] = self

        @staticmethod
        def defaultProfile():  # noqa: N802
            raise AssertionError(
                "不得使用 QWebEngineProfile.defaultProfile()：它是无痕模式，"
                "cookie 不落盘，用户每次重启都要重新登录"
            )

        def setPersistentStoragePath(self, path: str) -> None:  # noqa: N802, D102
            self.storage_path = path

        def setCachePath(self, path: str) -> None:  # noqa: N802, D102
            self.cache_path = path

        def setPersistentCookiesPolicy(self, policy) -> None:  # noqa: ANN001, N802, D102
            self.cookies_policy = policy

        def cookieStore(self):  # noqa: N802, D102
            return holder["store"]

    monkeypatch.setattr(LB, "_HAS_WEBENGINE", True)
    monkeypatch.setattr(LB, "QWebEngineProfile", _FakeProfile, raising=False)
    monkeypatch.setattr(LB, "QWebEnginePage", _FakePage, raising=False)
    monkeypatch.setattr(LB, "QWebEngineView", _FakeView, raising=False)

    def _build(tmp_path: Path, raise_on_load: bool = False):
        # 隔离 app_data_dir，避免测试往真实 %APPDATA% 写 webengine 目录
        monkeypatch.setattr(LB, "app_data_dir", lambda: str(tmp_path))
        holder["store"] = _FakeCookieStore(raise_on_load)
        cfg = ConfigStore(str(tmp_path / "config.json"))
        cfg.load()
        dialog = LB.LoginBrowserDialog(cfg)
        return dialog, holder

    return _build


@pytest.fixture()
def qcookie():
    """构造 ``QNetworkCookie`` 的工厂。"""
    QNetworkCookie = pytest.importorskip("PySide6.QtNetwork").QNetworkCookie

    def _make(name: str, value: str, domain: str, path: str = "/"):
        cookie = QNetworkCookie()
        cookie.setName(name.encode("utf-8"))
        cookie.setValue(value.encode("utf-8"))
        cookie.setDomain(domain)
        cookie.setPath(path)
        return cookie

    return _make


# ======================================================================
# 盲区 1 / 2：__init__ 的 loadAllCookies 与信号连接
# ======================================================================
class TestInitCookiePipeline:
    """``LoginBrowserDialog.__init__`` 的 cookie 采集管线。"""

    def test_init_requests_existing_cookies(self, fake_webengine, tmp_path: Path) -> None:
        """必须调用 ``loadAllCookies()`` 回放既有 cookie（不能用 allCookies）。"""
        _dialog, holder = fake_webengine(tmp_path)
        assert holder["store"].load_calls == 1

    def test_init_connects_both_cookie_signals(self, fake_webengine, tmp_path: Path) -> None:
        """``cookieAdded`` 与 ``cookieRemoved`` 都必须连接到对应槽。"""
        _dialog, holder = fake_webengine(tmp_path)
        store = holder["store"]
        assert [s.__func__ for s in store.cookieAdded.slots] == [
            LB.LoginBrowserDialog._on_cookie_added
        ]
        assert [s.__func__ for s in store.cookieRemoved.slots] == [
            LB.LoginBrowserDialog._on_cookie_removed
        ]

    def test_signal_replay_last_wins_and_removal(
        self, fake_webengine, tmp_path: Path, qcookie
    ) -> None:
        """经真实信号链路：回放 → 覆盖 → 移除，最终状态正确。"""
        dialog, holder = fake_webengine(tmp_path)
        store = holder["store"]

        store.cookieAdded.emit(qcookie("SID", "old", ".youtube.com", "/"))
        store.cookieAdded.emit(qcookie("SID", "new", ".youtube.com", "/"))
        store.cookieAdded.emit(qcookie("SID", "sub", ".youtube.com", "/watch"))
        store.cookieAdded.emit(qcookie("HSID", "h", ".youtube.com", "/"))
        store.cookieRemoved.emit(qcookie("HSID", "h", ".youtube.com", "/"))

        got = sorted(
            (bytes(c.name()).decode(), bytes(c.value()).decode(), c.path())
            for c in dialog._collect_cookies()
        )
        assert got == [("SID", "new", "/"), ("SID", "sub", "/watch")]

    def test_load_failure_does_not_break_construction(
        self, fake_webengine, tmp_path: Path
    ) -> None:
        """``loadAllCookies()`` 抛异常时对话框仍应构建成功。"""
        dialog, holder = fake_webengine(tmp_path, raise_on_load=True)
        assert holder["store"].load_calls == 1
        assert dialog._collect_cookies() == []

    def test_no_allcookies_call_in_source(self) -> None:
        """AST 级确认源码中不存在 ``allCookies()`` 调用（该 API 不存在）。"""
        src = (_PROJECT_ROOT / "src" / "ui" / "login_browser.py").read_text(encoding="utf-8")
        called = {
            node.func.attr
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "allCookies" not in called
        assert "loadAllCookies" in called


# ======================================================================
# 新增盲区：持久化 profile（否则每次重启都要重新登录）
# ======================================================================
class TestPersistentProfile:
    """具名持久化 profile 的配置与绑定。"""

    def test_profile_is_named_not_default(self, fake_webengine, tmp_path: Path) -> None:
        """必须用具名 profile；用 defaultProfile 会被替身直接判失败。"""
        _dialog, holder = fake_webengine(tmp_path)
        assert holder["profile"].name == "VidJuiceLogin"

    def test_persistent_paths_under_app_data(self, fake_webengine, tmp_path: Path) -> None:
        """持久化目录与缓存目录必须落在 app_data_dir 下，且不得硬编码。"""
        _dialog, holder = fake_webengine(tmp_path)
        profile = holder["profile"]

        expected_root = tmp_path / "webengine"
        assert Path(profile.storage_path) == expected_root
        assert Path(profile.cache_path) == expected_root / "Cache"

    def test_force_persistent_cookies(self, fake_webengine, tmp_path: Path) -> None:
        """cookie 策略必须为 ForcePersistentCookies，否则仍不落盘。"""
        _dialog, holder = fake_webengine(tmp_path)
        assert holder["profile"].cookies_policy == "ForcePersistentCookies"

    def test_view_actually_uses_that_profile(self, fake_webengine, tmp_path: Path) -> None:
        """view 必须绑定该 profile 建的 page，否则仍走 defaultProfile。"""
        dialog, holder = fake_webengine(tmp_path)

        assert dialog._view.page_obj is not None, "view 未 setPage，持久化配置不生效"
        assert dialog._view.page_obj.profile is holder["profile"]

    def test_profile_parented_to_dialog(self, fake_webengine, tmp_path: Path) -> None:
        """profile 以对话框为 parent，随对话框一同销毁。"""
        dialog, holder = fake_webengine(tmp_path)
        assert holder["profile"].parent_obj is dialog

    def test_page_parented_to_profile_not_dialog(self, fake_webengine, tmp_path: Path) -> None:
        """page 的 parent 必须是 profile，否则析构顺序错误会导致关闭时崩溃。

        Qt 按「添加顺序正序」销毁 children：profile 先挂到 dialog，若 page 也
        挂 dialog，profile 就会**先于** page 析构，违反 Qt「profile 必须比其
        page 活得久」的要求，报 "Release of profile requested but
        WebEnginePage still not deleted. Expect troubles!"，严重时直接崩溃。
        挂到 profile 名下则 parent 必然后于 child 析构。
        """
        dialog, holder = fake_webengine(tmp_path)
        profile = holder["profile"]

        assert dialog._page.parent_obj is profile, "page 的 parent 必须是 profile"
        assert dialog._page.parent_obj is not dialog, "page 不可与 profile 平级挂在 dialog 上"


# ======================================================================
# 盲区 3：Netscape path 回退
# ======================================================================
class TestNetscapePathFallback:
    """path 字段的边界行为。"""

    @pytest.mark.parametrize("raw_path", ["", "\t", "\n"])
    def test_blank_path_falls_back_to_root(self, qcookie, raw_path: str) -> None:
        """path 为空或全由非法字符构成时必须回退 ``/``，否则该行少一个有效字段。"""
        text = LB.cookies_to_netscape([qcookie("SID", "v", ".youtube.com", raw_path)])
        line = [ln for ln in text.splitlines() if ln and not ln.startswith("#")][0]
        fields = line.split("\t")
        assert len(fields) == 7
        assert fields[2] == "/"


# ======================================================================
# 去重键与落盘处理同源（必修 6 / 7）
# ======================================================================
class TestCookieKeyConsistency:
    """``_cookie_key`` 必须与写盘时的净化处理严格同源。"""

    def test_key_sanitized_same_as_written_line(self, qcookie) -> None:
        """含控制字符的 name 与净化后同名的 cookie 必须视为同一个键。

        否则两者各存一份，落盘时被净化成同名，写出两行重复凭据。
        """
        plain = qcookie("SID", "a", ".youtube.com", "/")
        dirty = qcookie("SI\tD", "b", ".youtube.com", "/")
        assert LB.LoginBrowserDialog._cookie_key(plain) == LB.LoginBrowserDialog._cookie_key(dirty)

    def test_key_is_str_not_bytes(self, qcookie) -> None:
        """键的三个字段都应为 str，与写行逻辑同源。"""
        key = LB.LoginBrowserDialog._cookie_key(qcookie("SID", "v", ".youtube.com", "/"))
        assert all(isinstance(part, str) for part in key)

    def test_key_never_raises_on_broken_cookie(self) -> None:
        """domain() / path() 抛异常时不得穿透（信号槽内异常会打崩事件循环）。"""

        class _Broken:
            def name(self):  # noqa: D102
                return b"SID"

            def domain(self):  # noqa: D102
                raise RuntimeError("boom")

            def path(self):  # noqa: D102
                return "/"

        assert LB.LoginBrowserDialog._cookie_key(_Broken()) == ("", "", "/")


# ======================================================================
# 盲区 4：打包配置静态约束
# ======================================================================
class TestBuildSpec:
    """打包配置的静态一致性（防止 QtWebEngine 依赖被误排除 / 被 UPX 压坏）。"""

    @staticmethod
    def _spec_kwargs() -> dict:
        """执行 build.spec，捕获 ``Analysis`` / ``EXE`` 的参数。"""
        captured: dict = {}

        class _Stub:
            def __init__(self, *args, **kwargs):
                captured.setdefault(type(self).__name__, kwargs)
                self.pure = []
                self.zipped_data = None
                self.scripts = self.binaries = self.zipfiles = self.datas = []

        ns = {
            "Analysis": type("Analysis", (_Stub,), {}),
            "PYZ": type("PYZ", (_Stub,), {}),
            "EXE": type("EXE", (_Stub,), {}),
        }
        spec = (_PROJECT_ROOT / "build.spec").read_text(encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(_PROJECT_ROOT)
        try:
            exec(compile(spec, "build.spec", "exec"), ns)  # noqa: S102
        finally:
            os.chdir(cwd)
        return captured

    def test_qml_and_quick_not_excluded(self) -> None:
        """QtQml / QtQuick 是 QtWebEngine 运行时依赖，排除会导致内置浏览器崩溃。"""
        excludes = self._spec_kwargs()["Analysis"]["excludes"]
        assert "PySide6.QtQml" not in excludes
        assert "PySide6.QtQuick" not in excludes

    def test_webengine_chain_declared(self) -> None:
        """QtWebEngine 依赖链必须完整声明在 hiddenimports。"""
        hidden = set(self._spec_kwargs()["Analysis"]["hiddenimports"])
        required = {
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets",
            "PySide6.QtWebChannel",
            "PySide6.QtPositioning",
            "PySide6.QtPrintSupport",
            "PySide6.QtQuickWidgets",
            "PySide6.QtQml",
            "PySide6.QtQuick",
        }
        assert required <= hidden, f"缺失：{sorted(required - hidden)}"

    def test_excludes_and_hiddenimports_disjoint(self) -> None:
        """同一模块不可既排除又隐藏导入（含父包前缀）。"""
        kwargs = self._spec_kwargs()["Analysis"]
        hidden, excludes = kwargs["hiddenimports"], kwargs["excludes"]
        conflicts = [h for h in hidden for e in excludes if h == e or h.startswith(e + ".")]
        assert not conflicts, f"冲突：{sorted(set(conflicts))}"

    def test_upx_disabled(self) -> None:
        """UPX 会压坏 Qt / QtWebEngine 的 DLL，必须保持关闭。"""
        assert self._spec_kwargs()["EXE"]["upx"] is False

    def test_extractor_collect_failure_not_silenced(self) -> None:
        """收集 yt_dlp.extractor 失败时不得静默 ``pass``（否则产物丢全部解析器）。

        只针对 ``collect_submodules`` 那个 try 块——同文件里 certifi 缺失时
        ``pass`` 是有意的降级（不阻断打包），不应被一并判失败。
        """
        spec_src = (_PROJECT_ROOT / "build.spec").read_text(encoding="utf-8")
        tree = ast.parse(spec_src)

        target: ast.Try | None = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            uses_collect = any(
                isinstance(sub, ast.Call)
                and getattr(sub.func, "id", None) == "collect_submodules"
                for sub in ast.walk(node)
            )
            if uses_collect:
                target = node
                break

        assert target is not None, "build.spec 中未找到 collect_submodules 调用"
        for handler in target.handlers:
            remaining = [n for n in handler.body if not isinstance(n, ast.Pass)]
            assert remaining, "collect_submodules 失败被静默 pass 吞掉，产物会丢失全部 extractor"


class TestBuildPreflight:
    """``build.py`` 打包前置检查必须拦住缺失 QtWebEngine 的环境。"""

    def test_preflight_hard_checks_webengine(self) -> None:
        """preflight 必须显式 import QtWebEngineWidgets 并在失败时置 ok=False。

        只装 PySide6-Essentials 的机器能打出 EXE，但内置浏览器登录直接抛
        RuntimeError —— 打包零报错、需求静默失效，必须在打包前拦下。

        两条断言缺一不可：只检查 import 存在是**纸糊的**——把 ``ok = False``
        删掉只留一行 error 日志，preflight 依然返回 True 继续打包，产物照出、
        登录静默失效，而测试全绿。故必须连「失败时置 ok=False」一起断言。
        """
        src = (_PROJECT_ROOT / "build.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        preflight = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "preflight"
        )

        # 定位「import PySide6.QtWebEngineWidgets」所在的那个 try 块
        target: ast.Try | None = None
        for node in ast.walk(preflight):
            if not isinstance(node, ast.Try):
                continue
            imported = {
                alias.name
                for sub in ast.walk(node)
                if isinstance(sub, ast.Import)
                for alias in sub.names
            }
            if "PySide6.QtWebEngineWidgets" in imported:
                target = node
                break

        assert target is not None, "preflight 未硬校验 QtWebEngine（缺 import 语句）"

        # 该 try 的 except 分支里必须有 ok = False 赋值，否则检查形同虚设
        assigns_false = False
        for handler in target.handlers:
            for sub in ast.walk(handler):
                if (
                    isinstance(sub, ast.Assign)
                    and any(getattr(t, "id", None) == "ok" for t in sub.targets)
                    and isinstance(sub.value, ast.Constant)
                    and sub.value.value is False
                ):
                    assigns_false = True
        assert assigns_false, "QtWebEngine 缺失时未置 ok=False，preflight 会放行打包"
