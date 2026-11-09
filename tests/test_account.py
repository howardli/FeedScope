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
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    LoginRequired,
    Source,
    StaticFeedLoader,
)
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.dialogs import AccountScreen
from feedscope.ui.screens.detail import DetailScreen


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


class DetailWallSource(Source):
    """详情撞墙源（知乎 403 形态）：列表照常出内容（v3 宽松）、进详情才会话失效。"""

    source_id = "detailwall"
    label = "详情墙源"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    login_url = "https://example.com/login"
    login_cookie = "session"
    logout_cookies: ClassVar[list[str]] = ["session"]

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_item(0)])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        raise LoginRequired("详情墙源 登录态已失效（HTTP 403），重新登录通常可恢复")


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
def detail_wall_registry():
    """只留详情撞墙源：验证详情屏的登录引导动线（列表正常、详情 LoginRequired）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[DetailWallSource.source_id] = DetailWallSource
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

    # ui/account.py 持有 login 模块对象本身（from feedscope import login），替换模块属性即生效
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


async def _run_detail_wall_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        # 列表正常（会话半失效的 v3 形态）→ Enter 进详情才撞 LoginRequired
        assert await settle(pilot, lambda: app.main is not None and len(app.main.items) == 1)
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen)), "详情撞墙也弹登录框"
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "登录" in prompt, "弹框正文说明是登录"

        # 取消：回详情屏，常驻红字提示（不再是裸 HTTP 码），面包屑标记未登录
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        line = str(app.screen.query_one("#detail-loading", Label).content)
        assert "重新登录" in line and "Ctrl+U" in line, "常驻提示指明重登入口"
        assert app.sub_title == "未登录"

        # 取消被记住：Ctrl+N 原地重试不再追问（与列表屏同一克制）
        await pilot.press("ctrl+n")
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen) and not isinstance(
            app.screen, AccountScreen
        ), "重试不得再自动弹框"


def test_detail_wall_pushes_login_modal_and_remember_cancel(detail_wall_registry) -> None:
    asyncio.run(_run_detail_wall_flow())


async def _run_stale_session_flow(monkeypatch) -> None:
    """知乎 403 形态的闭环：详情撞墙被拒后 Ctrl+U 直接给登录框（不是登出框
    ——判登正被躺着的失效 cookie 骗着），重登成功后失效记忆清零恢复如实分派。"""
    import feedscope.login as login_mod

    from feedscope.ui.screens.main import MainScreen

    login_calls: list[str] = []

    async def _login(source_id: str, *, quiet: bool = False) -> bool:
        login_calls.append(source_id)
        return True

    async def _logged_in(source_id: str) -> bool:
        return True  # cookie 还躺着：判登一直说「已登录」（半失效的真实形态）

    monkeypatch.setattr(login_mod, "login", _login)
    monkeypatch.setattr(login_mod, "is_logged_in", _logged_in)

    app = FeedScopeApp()
    async with app.run_test(size=(80, 30)) as pilot:
        assert await settle(pilot, lambda: app.main is not None and len(app.main.items) == 1)
        # 详情撞墙 → 登录框 → 取消（stale 已在详情分支记账）
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")  # 退回列表（Ctrl+U 是列表屏键位）
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))

        # Ctrl+U：判登说「已登录」但失效已记账 → 直接给登录框
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "需要登录" in prompt and "登出" not in prompt, "失效会话直接给登录框"

        # 选登录：新会话生效 → 失效记忆清零（条件并入 settle：记账清理在
        # worker 里，login 调用记录与 discard 之间有微小时序窗口）
        await pilot.click("#account-go")
        assert await settle(
            pilot,
            lambda: login_calls == ["detailwall"]
            and not app.accounts.stale
            and not isinstance(app.screen, AccountScreen),
        ), "登录动线走通且失效记忆清零"

        # 再 Ctrl+U：失效记忆已清、判登如实 → 登出框
        await pilot.press("ctrl+u")
        assert await settle(pilot, lambda: isinstance(app.screen, AccountScreen))
        prompt = str(app.screen.query_one("#account-prompt").content)
        assert "登出" in prompt, "失效记忆清零后恢复如实分派"
        await pilot.press("escape")


def test_stale_session_ctrl_u_offers_login_directly(detail_wall_registry, monkeypatch) -> None:
    asyncio.run(_run_stale_session_flow(monkeypatch))
