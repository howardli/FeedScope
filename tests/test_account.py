"""账号（登录/登出）TUI 流程：stub 登录源 + monkeypatch login.py 驱动整条链路。

覆盖：撞登录墙自动弹登录框（取消/确认）、选登录调 login 并刷新出内容、
Ctrl+U 三分派（匿名源 / 未登录弹登录 / 已登录弹登出并清登录态）。不碰网络、不开浏览器。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from conftest import last_notification, settle
from textual.widgets import Label

import feedscope.sources  # noqa: F401  先触发真实源注册
from feedscope.core.models import FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import FeedLoader, LoginRequired, Source, StaticFeedLoader
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.dialogs import AccountScreen


def _item(i: int) -> FeedItem:
    return FeedItem(source="loginsrc", id=f"i{i}", title=f"第{i}条", url=f"https://e.com/{i}")


class LoginSource(Source):
    """撞墙源：authed=False 时 open_feed 抛 LoginRequired；测试通过 monkeypatch 的
    login/login 翻转 authed 模拟「登录成功后刷新出内容」。"""

    source_id = "loginsrc"
    label = "登录源"
    supports_detail = False
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    login_url = "https://example.com/login"
    login_cookie = "session"
    logout_cookies: ClassVar[list[str]] = ["session"]
    authed: ClassVar[bool] = False  # 测试态：登录是否已完成（fake login 翻转）

    async def open_feed(self, name: str) -> FeedLoader:
        if not LoginSource.authed:
            raise LoginRequired("登录源需要登录后才能查看")
        return StaticFeedLoader([_item(0)])


class AnonSource(Source):
    """匿名源：无 login_url，Ctrl+U 应提示「无需登录」。"""

    source_id = "anon"
    label = "匿名源"
    supports_detail = False
    feeds: ClassVar[list[str]] = ["only"]
    feed_labels: ClassVar[dict[str, str]] = {"only": "唯一流"}

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_item(0)])


@pytest.fixture(autouse=True)
def _reset_authed():
    LoginSource.authed = False
    yield
    LoginSource.authed = False


@pytest.fixture()
def login_registry():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[LoginSource.source_id] = LoginSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def anon_login_registry():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[AnonSource.source_id] = AnonSource
    _REGISTRY[LoginSource.source_id] = LoginSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def fake_login(monkeypatch):
    """monkeypatch login.py 的三个入口：login 翻转 authed、logout 复位、is_logged_in 读 authed。"""
    import feedscope.login as login_mod

    calls: dict[str, list[str]] = {"login": [], "logout": [], "is_logged_in": []}

    async def _login(source_id: str, *, quiet: bool = False) -> bool:
        calls["login"].append(source_id)
        LoginSource.authed = True
        return True

    async def _logout(source_id: str) -> bool:
        calls["logout"].append(source_id)
        LoginSource.authed = False
        return True

    async def _is_logged_in(source_id: str) -> bool:
        calls["is_logged_in"].append(source_id)
        return LoginSource.authed

    monkeypatch.setattr(login_mod, "login", _login)
    monkeypatch.setattr(login_mod, "logout", _logout)
    monkeypatch.setattr(login_mod, "is_logged_in", _is_logged_in)
    return calls







async def _run_wall_cancel_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        # 撞墙：自动弹登录框（不自动开浏览器，等用户选）
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "撞墙弹登录框"
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "登录" in prompt, "登录框正文说明是登录"
        assert app.sub_title == "未登录", "面包屑标记未登录"

        # 取消：弹框关闭，留在空态（未登录提示常驻）
        await pilot.press("escape")
        assert await settle(pilot, lambda: not isinstance(app.screen, AccountScreen)), "取消关闭弹框"
        assert "未登录" in app.sub_title


def test_wall_pushes_login_modal_and_cancel(login_registry) -> None:
    asyncio.run(_run_wall_cancel_flow())


async def _run_cancel_remembered_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "撞墙弹登录框"
        await pilot.press("escape")  # 取消
        assert await settle(pilot, lambda: not isinstance(app.screen, AccountScreen))

        # 取消被记住：再刷新不再自动弹框，常驻提示保留 Ctrl+U 登录入口
        await pilot.press("f5")
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert not isinstance(app.screen, AccountScreen), "取消后刷新不得再自动弹框"
        assert "未登录" in app.sub_title
        hint = str(app.query_one("#empty-hint", Label).content)
        assert "Ctrl+U 登录" in hint, "常驻提示要指明登录入口"

        # Ctrl+U 手动登录入口不受取消记忆影响
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "Ctrl+U 仍可手动登录"
        await pilot.press("escape")
        assert await settle(pilot, lambda: not isinstance(app.screen, AccountScreen))


def test_cancel_remembered_no_reprompt(login_registry, fake_login) -> None:
    # fake_login 隔离 is_logged_in：真实现会起无头 Chrome，全量跑时冷启动
    # 常超过 _settle 的 3 秒上限而闪断（本文件约定不碰网络、不开浏览器）
    asyncio.run(_run_cancel_remembered_flow())


async def _run_refusal_survives_switch_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        # 注册顺序：匿名源在前（默认源），登录源第二位
        assert await settle(
            pilot, lambda: app.main.current_source.source_id == "anon" and len(app.main.items) == 1
        ), "匿名源正常出内容"

        await pilot.click("#source-tab-1")  # 切到登录源：撞墙弹登录框
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "撞墙弹登录框"
        await pilot.press("escape")  # 取消登录

        # 切回匿名源不受影响，再切回登录源不再追问
        await pilot.click("#source-tab-0")
        assert await settle(pilot, lambda: app.main.current_source.source_id == "anon" and len(app.main.items) == 1)
        await pilot.click("#source-tab-1")
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert not isinstance(app.screen, AccountScreen), "切回已拒绝的源不得再自动弹框"
        assert "Ctrl+U 登录" in str(app.query_one("#empty-hint", Label).content)


def test_refusal_survives_source_switch(anon_login_registry) -> None:
    asyncio.run(_run_refusal_survives_switch_flow())


async def _run_failed_login_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.click("#account-go")  # 选「登录」但登录未完成（fake 返回 False）
        assert await settle(
            pilot,
            lambda: not isinstance(app.screen, AccountScreen) and "登录未完成" in last_notification(app),
        ), "失败有 toast 提示"
        # 登录失败后的自动刷新不得立刻再弹框
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert not isinstance(app.screen, AccountScreen), "登录未完成后不得自动追问"


def test_failed_login_no_reprompt(login_registry, monkeypatch) -> None:
    import feedscope.login as login_mod

    async def _fail_login(source_id: str, *, quiet: bool = False) -> bool:
        return False  # 用户关掉登录窗口 / 超时

    # app.py 持有 login 模块对象本身（from feedscope import login），替换模块属性即生效
    monkeypatch.setattr(login_mod, "login", _fail_login)
    asyncio.run(_run_failed_login_flow())


async def _run_logout_reprompts_flow(calls) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        # 先拒绝一次（refused 生效）
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: not isinstance(app.screen, AccountScreen))

        # u 手动登录成功 → 刷新出内容
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.click("#account-go")
        assert await settle(pilot, lambda: LoginSource.authed and len(app.main.items) == 1), "登录成功出内容"

        # 登出（换账号动线）：撞墙要重新弹登录框，不能被旧的拒绝记录压住
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.click("#account-go")
        assert await settle(
            pilot,
            lambda: not LoginSource.authed and isinstance(app.screen, AccountScreen),
        ), "登出后撞墙恢复自动弹登录框"


def test_logout_reprompts_after_refusal(login_registry, fake_login) -> None:
    asyncio.run(_run_logout_reprompts_flow(fake_login))


async def _run_login_success_flow(calls) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.click("#account-go")  # 选「登录」
        # 登录成功后自动刷新，列表出内容、弹框已关
        assert await settle(
            pilot,
            lambda: len(app.main.items) == 1 and not isinstance(app.screen, AccountScreen),
        ), "选登录后刷新出内容"
        assert calls["login"] == ["loginsrc"], "登录动作只调了当前源"
        assert LoginSource.authed is True


def test_login_via_modal_calls_login_and_refreshes(login_registry, fake_login) -> None:
    asyncio.run(_run_login_success_flow(fake_login))


async def _run_account_key_flow(calls) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        # 匿名源（注册顺序第一位 = 默认源）：u 提示无需登录，不弹框
        assert await settle(pilot, lambda: app.main.current_source.source_id == "anon" and len(app.main.items) == 1)
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: "无需登录" in last_notification(app)), "匿名源 u 有反馈"
        assert not isinstance(app.screen, AccountScreen), "匿名源 u 不弹框"

        # 切到登录源（第二位）：撞墙自动弹登录框
        await pilot.click("#source-tab-1")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "撞墙弹登录框"
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "登录" in prompt, "未登录时弹的是登录框"
        await pilot.press("escape")  # 取消

        # 未登录 u：同样弹登录框
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.press("escape")

        # 走一次成功登录让 authed=True（列表出内容）
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.click("#account-go")
        assert await settle(pilot, lambda: LoginSource.authed and len(app.main.items) == 1), "登录成功刷新出内容"

        # 已登录：u 弹登出框
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "登出" in prompt, "已登录时 u 弹的是登出框"
        await pilot.click("#account-go")  # 确认登出
        assert await settle(pilot, lambda: calls["logout"] == ["loginsrc"]), "登出调 logout"
        assert LoginSource.authed is False, "登出复位登录态"


def test_account_key_dispatches(anon_login_registry, fake_login) -> None:
    asyncio.run(_run_account_key_flow(fake_login))
