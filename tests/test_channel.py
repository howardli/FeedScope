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
    assert bad.closed  # 弃掉的页签要真关，不能留孤儿页


def test_eval_second_failure_propagates(make_tab) -> None:
    """重建后仍失败：原样上交（调用方包 SourceError），失败页同样关掉。"""
    page = FakePage(nav_failures=5)
    tab, _ = make_tab(page)
    with pytest.raises(RuntimeError, match="destroyed"):
        asyncio.run(tab.eval("x"))
    assert page.closed


def test_check_failure_closes_page_and_propagates(make_tab) -> None:
    """开页校验（登录墙等）失败：页签关掉再上交，不留半开的孤儿页。"""
    page = FakePage()

    async def wall(p) -> None:
        raise RuntimeError("login wall")

    tab, _ = make_tab(page, check=wall)
    with pytest.raises(RuntimeError, match="login wall"):
        asyncio.run(tab.get())
    assert page.closed


# ---- ChannelTab.fetch：统一页内取数原语（解包约定 / 报错转译 / 样本落盘）----


class FetchPage(FakePage):
    """evaluate 回放固定结果（__body / __status / __error / 非 dict 垃圾）的页。"""

    def __init__(self, result) -> None:
        super().__init__()
        self._result = result
        self.args: list = []

    async def evaluate(self, script: str, arg=None):
        self.evals += 1
        self.args.append(arg)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _fetch_tab(monkeypatch: pytest.MonkeyPatch, result) -> tuple[ChannelTab, FetchPage]:
    page = FetchPage(result)
    monkeypatch.setattr(browser, "get_session", lambda: FakeSession(page))
    return ChannelTab(_PARK, name="test"), page


def test_fetch_json_body_and_args(monkeypatch) -> None:
    """成功路径：__body 解包、(None, 正文) 返回、参数原样透传给页签 JS。"""
    tab, page = _fetch_tab(monkeypatch, {"__body": {"data": [1]}})
    status, body = asyncio.run(
        tab.fetch("https://x/api", source="测试", headers={"a": "1"}, referrer="https://r/")
    )
    assert status is None and body == {"data": [1]}
    url, headers, cookie_headers, referrer, as_text = page.args[0]
    assert (
        url == "https://x/api"
        and headers == {"a": "1"}
        and cookie_headers == {}
        and referrer == "https://r/"
    )
    assert as_text is False


def test_fetch_text_body_saves_sample(monkeypatch, tmp_path) -> None:
    """as_text 拿 HTML 正文并顺手落样本（probe 改版修复流水线三源同权）。"""
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(tmp_path))
    tab, _page = _fetch_tab(monkeypatch, {"__body": "<html>x</html>"})
    status, body = asyncio.run(tab.fetch("https://x/a", source="测试", as_text=True))
    assert status is None and body == "<html>x</html>"
    assert any("a" in p.name for p in tmp_path.iterdir())


def test_fetch_status_surfaces_no_sample(monkeypatch, tmp_path) -> None:
    """非 2xx：状态码原样上交（语义转译归各源），正文空不落样本。"""
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(tmp_path))
    tab, _page = _fetch_tab(monkeypatch, {"__status": 403})
    status, body = asyncio.run(tab.fetch("https://x/a", source="测试"))
    assert status == 403 and body is None
    assert list(tmp_path.iterdir()) == []


def test_fetch_error_and_garbage_are_source_error(monkeypatch) -> None:
    """__error（网络/解析失败）与非约定结构：转 SourceError（出口给 UI 提示）。"""
    from feedscope.core.source import SourceError

    tab, _page = _fetch_tab(monkeypatch, {"__error": "boom"})
    with pytest.raises(SourceError, match="boom"):
        asyncio.run(tab.fetch("https://x/a", source="测试"))
    tab, _page = _fetch_tab(monkeypatch, "not-a-dict")
    with pytest.raises(SourceError, match="结构异常"):
        asyncio.run(tab.fetch("https://x/a", source="测试"))


def test_fetch_playwright_failure_wrapped(monkeypatch) -> None:
    """evaluate 层失败（重建重试也没救）：包成 SourceError，不裸抛。"""
    from feedscope.core.source import SourceError

    tab, _page = _fetch_tab(monkeypatch, RuntimeError(_NAV_ERR))
    with pytest.raises(SourceError, match="请求失败"):
        asyncio.run(tab.fetch("https://x/a", source="测试"))
