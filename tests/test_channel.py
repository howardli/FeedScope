"""ChannelTab（常驻取数通道页签）生命周期测试：FakePage 替身，不碰真浏览器。

单例复用 / 被关重建 / evaluate 在途跳转换页签重试一次 / 开页校验失败弃页
——敌意源（知乎/Reddit/X）共用的通道语义在这里集中测一遍，源内测试只测
各自的取数路由与自愈，不再重复测生命周期。
"""

from __future__ import annotations

import asyncio

import pytest

from feedscope.core import browser
from feedscope.core.browser import ChannelTab

_NAV_ERR = "Execution context was destroyed, most likely because of a navigation"
_PARK = "https://example.com/"


class FakePage:
    """最小 Playwright page 替身：evaluate 回显参数；nav_failures>0 时前若干次
    抛「执行上下文被销毁」模拟在途跳转。"""

    def __init__(self, nav_failures: int = 0) -> None:
        self.url = ""
        self.closed = False
        self._nav = nav_failures
        self.evals = 0

    async def goto(self, url: str, **kw) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    async def evaluate(self, script: str, arg=None):
        self.evals += 1
        if self._nav > 0:
            self._nav -= 1
            raise RuntimeError(_NAV_ERR)
        return {"ok": arg}


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = pages

    async def new_page(self) -> FakePage:
        return self._pages.pop(0) if len(self._pages) > 1 else self._pages[0]


class FakeSession:
    def __init__(self, *pages: FakePage) -> None:
        self._pages = list(pages)
        self.opens = 0

    async def run(self, op, *args, **kwargs):
        self.opens += 1
        return await op(FakeContext(self._pages), *args, **kwargs)


@pytest.fixture()
def make_tab(monkeypatch: pytest.MonkeyPatch):
    def make(*pages: FakePage, check=None) -> tuple[ChannelTab, FakeSession]:
        session = FakeSession(*(pages or [FakePage()]))
        monkeypatch.setattr(browser, "get_session", lambda: session)
        return ChannelTab(_PARK, check=check, name="test"), session

    return make


def test_opened_once_and_reused(make_tab) -> None:
    """页签进程级单例：第二次 get 直接复用，不再开页；停靠在指定 URL。"""
    tab, session = make_tab()
    p1 = asyncio.run(tab.get())
    p2 = asyncio.run(tab.get())
    assert p1 is p2 and session.opens == 1
    assert p1.url == _PARK


def test_reopens_when_closed(make_tab) -> None:
    """页签被关：单例失效，下一次取通道重新开页。"""
    tab, session = make_tab()
    page = asyncio.run(tab.get())
    page.closed = True
    asyncio.run(tab.get())
    assert session.opens == 2


def test_eval_rebuilds_on_nav_destroyed(make_tab) -> None:
    """evaluate 撞在途跳转（context destroyed）：弃单例换新页签重试一次，不裸炸。"""
    bad = FakePage(nav_failures=1)
    good = FakePage()
    tab, session = make_tab(bad, good)
    out = asyncio.run(tab.eval("x", 7))
    assert out == {"ok": 7} and session.opens == 2  # 第二个页签接住了重试


def test_eval_second_failure_propagates(make_tab) -> None:
    """重建后仍失败：原样上交（调用方包 SourceError）。"""
    tab, _ = make_tab(FakePage(nav_failures=5))
    with pytest.raises(RuntimeError, match="destroyed"):
        asyncio.run(tab.eval("x"))


def test_check_failure_closes_page_and_propagates(make_tab) -> None:
    """开页校验（登录墙等）失败：页签关掉再上交，不留半开的孤儿页。"""
    page = FakePage()

    async def wall(p) -> None:
        raise RuntimeError("login wall")

    tab, _ = make_tab(page, check=wall)
    with pytest.raises(RuntimeError, match="login wall"):
        asyncio.run(tab.get())
    assert page.closed
