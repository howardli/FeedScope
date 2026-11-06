"""login.py 的 cookie 判定 / 登出逻辑单测：假 context 替代 Playwright（不碰浏览器）。

覆盖：X 判登要 auth_token+ct0 成对、知乎判登 z_c0 单枚、登出只清声明的 cookie。
is_logged_in / logout 的 session 装配路径由 TUI 流程测试（test_account）用
monkeypatch 覆盖，这里不重复。
"""

from __future__ import annotations

import asyncio

import pytest

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core.registry import get_source
from feedscope.login import _clear_cookies, _logged_in


class FakeContext:
    """最小 Playwright context 替身：cookies()/clear_cookies() 只按名字操作。"""

    def __init__(self, names: set[str]) -> None:
        self._names = set(names)
        self.cleared: list[str | None] = []

    async def cookies(self, url: str) -> list[dict]:
        return [{"name": n, "value": "v"} for n in sorted(self._names)]

    async def clear_cookies(self, name: str | None = None) -> None:
        self.cleared.append(name)
        self._names.discard(name)


@pytest.fixture()
def x():
    return get_source("x")


@pytest.fixture()
def zhihu():
    return get_source("zhihu")


@pytest.fixture()
def reddit():
    return get_source("reddit")


def _jwt(claims: dict) -> str:
    """构造只带 payload 的假 JWT（判登只看 claims，签名不验）。"""
    import base64
    import json as _json

    def enc(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return enc(b'{"alg":"none"}') + "." + enc(_json.dumps(claims).encode()) + ".x"


def test_logged_in_x_needs_both(x) -> None:
    assert asyncio.run(_logged_in(FakeContext({"auth_token", "ct0"}), x)) is True
    assert asyncio.run(_logged_in(FakeContext({"auth_token"}), x)) is False
    assert asyncio.run(_logged_in(FakeContext(set()), x)) is False


def test_logged_in_zhihu_single(zhihu) -> None:
    assert asyncio.run(_logged_in(FakeContext({"z_c0"}), zhihu)) is True
    assert asyncio.run(_logged_in(FakeContext(set()), zhihu)) is False


def test_logged_in_reddit_uses_login_verify(reddit) -> None:
    """Reddit 的判据是 token_v2 的 JWT 解码，不是 cookie 全集：
    登录态只有 token_v2、没有 reddit_session 时也算已登录，反之匿名 token 不算。"""
    ctx = FakeBrowserContext({"token_v2"}, jar={"token_v2": _jwt({"sub": "user"})})
    assert asyncio.run(_logged_in(ctx, reddit)) is True
    anon = FakeBrowserContext(
        {"reddit_session", "token_v2"}, jar={"token_v2": _jwt({"sub": "loid"})}
    )
    assert asyncio.run(_logged_in(anon, reddit)) is False


def test_clear_cookies_x_returns_had_and_clears_pair(x) -> None:
    ctx = FakeContext({"auth_token", "ct0"})
    assert asyncio.run(_clear_cookies(ctx, x)) is True
    assert set(ctx.cleared) == {"auth_token", "ct0"}
    assert ctx._names == set()


def test_clear_cookies_absent_returns_false_and_leaves_others(x) -> None:
    ctx = FakeContext({"some_other"})
    assert asyncio.run(_clear_cookies(ctx, x)) is False
    assert ctx._names == {"some_other"}  # 只清声明的，别的不动


# ---- login() 主流程：假 Playwright 全程离线（轮询 / 判登优先级 / 异常出口）----


class FakePage:
    def __init__(self) -> None:
        self.gone_to: list[str] = []

    async def goto(self, url: str, **kwargs) -> None:
        self.gone_to.append(url)


class FakeBrowserContext(FakeContext):
    """FakeContext 补齐 login() 用到的页面/导航/关闭；jar 给真 cookie 值
    （判登解码头需要完整值，缺省回 "v" 只够测名字在不在）。"""

    def __init__(self, names: set[str], jar: dict[str, str] | None = None) -> None:
        super().__init__(names)
        self._jar = jar
        self.page = FakePage()
        self.pages = [self.page]
        self.closed = False

    async def cookies(self, url: str) -> list[dict]:
        if self._jar is not None:
            return [{"name": k, "value": v} for k, v in self._jar.items()]
        return await super().cookies(url)

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeAsyncPlaywright:
    """async_playwright() 替身：注入 context；raise_on_launch 模拟浏览器起不来。"""

    def __init__(self, ctx: FakeBrowserContext, *, raise_on_launch: Exception | None = None) -> None:
        self._ctx = ctx
        self._raise = raise_on_launch

    def __call__(self) -> FakeAsyncPlaywright:
        return self

    async def __aenter__(self):
        ctx, raise_on_launch = self._ctx, self._raise

        class _Chromium:
            async def launch_persistent_context(self, **kw):
                if raise_on_launch is not None:
                    raise raise_on_launch
                return ctx

        return type("PW", (), {"chromium": _Chromium()})()

    async def __aexit__(self, *exc) -> bool:
        return False


def _fake_pw(monkeypatch: pytest.MonkeyPatch, ctx: FakeBrowserContext, **kw) -> None:
    monkeypatch.setattr(
        "playwright.async_api.async_playwright", FakeAsyncPlaywright(ctx, **kw)
    )


def _fast_poll(monkeypatch: pytest.MonkeyPatch, *, timeout: float = 0.3) -> None:
    import feedscope.login as login_mod

    monkeypatch.setattr(login_mod, "LOGIN_TIMEOUT", timeout)
    monkeypatch.setattr(login_mod, "POLL_INTERVAL", 0.05)


def test_login_polls_cookie_success(monkeypatch, capsys) -> None:
    ctx = FakeBrowserContext({"z_c0"})
    _fake_pw(monkeypatch, ctx)
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("zhihu")) is True
    assert ctx.page.gone_to == ["https://www.zhihu.com/signin"]  # 打开的是源声明的登录页
    assert ctx.closed
    assert "登录成功" in capsys.readouterr().out


def test_login_verify_wins_over_cookie_presence(monkeypatch) -> None:
    """Reddit：login_verify（JWT 解码）是判据——匿名态 sub=loid 时 cookie 在也不算登录。"""
    anon = FakeBrowserContext({"token_v2"}, jar={"token_v2": _jwt({"sub": "loid"})})
    _fake_pw(monkeypatch, anon)
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("reddit")) is False  # 不会误报成功（超时退出）

    user = FakeBrowserContext({"token_v2"}, jar={"token_v2": _jwt({"sub": "user"})})
    _fake_pw(monkeypatch, user)
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("reddit")) is True


def test_login_x_requires_cookie_pair(monkeypatch) -> None:
    """X 的登录轮询与判登门同口径：auth_token 单独出现不算成功，ct0 成对才行。"""
    _fake_pw(monkeypatch, FakeBrowserContext({"auth_token"}))
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("x")) is False

    _fake_pw(monkeypatch, FakeBrowserContext({"auth_token", "ct0"}))
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("x")) is True


def test_login_timeout_returns_false(monkeypatch, capsys) -> None:
    ctx = FakeBrowserContext(set())
    _fake_pw(monkeypatch, ctx)
    _fast_poll(monkeypatch, timeout=0.2)
    assert asyncio.run(login_mod().login("zhihu")) is False
    assert "超时" in capsys.readouterr().out


def test_login_browser_failure_is_friendly(monkeypatch, capsys) -> None:
    ctx = FakeBrowserContext(set())
    _fake_pw(monkeypatch, ctx, raise_on_launch=RuntimeError("no chrome"))
    _fast_poll(monkeypatch)
    assert asyncio.run(login_mod().login("zhihu")) is False
    assert "浏览器启动或打开登录页失败" in capsys.readouterr().out  # 中文出口，不裸栈


def test_login_anonymous_or_unknown_source(monkeypatch, capsys) -> None:
    assert asyncio.run(login_mod().login("hn")) is False  # 免登录源
    assert "无需登录" in capsys.readouterr().out
    assert asyncio.run(login_mod().login("nope")) is False  # 未注册源
    assert "未知源" in capsys.readouterr().out


def login_mod():
    import feedscope.login as m

    return m
