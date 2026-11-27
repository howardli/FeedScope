"""BrowserSession / Playwright 启动路径的测试（假 playwright 模块，不装不拉 Chrome）。

这是全项目覆盖率最低的一块：常驻会话怎么起、UA 探针怎么回退、profile 锁怎么
判、run 怎么串行、关了怎么重开——以前全靠真机点验。这里用一个最小假件把
playwright.async_api 换掉，把「启动」这条路径真正跑一遍。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import types
from pathlib import Path

import pytest

from feedscope.core import browser
from feedscope.core.browser import ProfileInUse, run_page
from feedscope.core.errors import SourceError

_HEADLESS_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/152.0.0.0 Safari/537.36"
_FULL_VERSION = "152.0.7977.65"


class FakePage:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.closed = False

    async def evaluate(self, script: str, arg=None) -> str:
        self._log.append("evaluate")
        return _HEADLESS_UA

    async def close(self) -> None:
        self.closed = True
        self._log.append("page.close")


class FakeBrowser:
    """UA 探针用的那只一次性浏览器。"""

    version = f"Chrome/{_FULL_VERSION}"

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.closed = False

    async def new_page(self) -> FakePage:
        return FakePage(self._log)

    async def close(self) -> None:
        self.closed = True
        self._log.append("browser.close")


class FakeContext:
    """持久 context：记录启动参数，cookie 操作可断言；close 出错可注入。"""

    def __init__(self, log: list[str], fail_close: bool = False) -> None:
        self._log = log
        self._fail_close = fail_close
        self.jar: list[dict[str, str]] = [
            {"name": "z_c0", "value": "session"},
            {"name": "d_n_v", "value": "x"},
        ]
        self.cleared: list[str] = []
        self.closed = False

    async def new_page(self) -> FakePage:
        return FakePage(self._log)

    async def cookies(self, url: str) -> list[dict[str, str]]:
        self._log.append(f"cookies:{url}")
        return list(self.jar)

    async def clear_cookies(self, *, name: str) -> None:
        self.cleared.append(name)
        self.jar = [c for c in self.jar if c["name"] != name]

    async def close(self) -> None:
        if self._fail_close:
            raise RuntimeError("Target closed")
        self.closed = True
        self._log.append("context.close")


class FakeChromium:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.launches: list[dict] = []
        self.contexts: list[FakeContext] = []
        self.probe_fails = False

    async def launch(self, **kwargs) -> FakeBrowser:
        self.launches.append({"probe": True, **kwargs})
        if self.probe_fails:
            raise RuntimeError("Executable doesn't exist")
        return FakeBrowser(self._log)

    async def launch_persistent_context(self, **kwargs) -> FakeContext:
        self.launches.append(kwargs)
        context = FakeContext(self._log)
        self.contexts.append(context)
        return context


class FakePlaywright:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.chromium = FakeChromium(self.log)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        self.log.append("pw.stop")


@pytest.fixture()
def playwright(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePlaywright:
    """把 playwright.async_api 换成假件；profile 目标指到临时目录。"""
    pw = FakePlaywright()

    class _Starter:
        async def start(self) -> FakePlaywright:
            pw.log.append("pw.start")
            return pw

    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: _Starter()  # type: ignore[attr-defined]
    pkg = types.ModuleType("playwright")
    pkg.async_api = api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "chromium-profile")
    return pw


def _persistent(pw: FakePlaywright) -> list[dict]:
    return [kw for kw in pw.chromium.launches if "user_data_dir" in kw]


async def _noop(ctx: FakeContext) -> None:
    """run() 的最小操作（op 必须是 awaitable）。"""


# ---- 启动 ----


def test_start_launches_once_and_fixes_the_headless_ua(playwright: FakePlaywright) -> None:
    session = browser.BrowserSession()

    async def start_three() -> None:
        await asyncio.gather(session.start(), session.start(), session.start())

    asyncio.run(start_three())
    assert session.started
    (launch,) = _persistent(playwright)
    assert launch["user_data_dir"] == str(browser.PROFILE_DIR)
    assert launch["headless"] is True and launch["channel"] == "chrome"
    assert launch["user_agent"] == _HEADLESS_UA.replace("HeadlessChrome/152.0.0.0", f"Chrome/{_FULL_VERSION}"), (
        "无头 UA 的 HeadlessChrome 标记换成探针问到的完整版本号"
    )
    cache = json.loads((browser.PROFILE_DIR.parent / "chrome-ua.json").read_text())
    assert cache["ua"] == launch["user_agent"], "探针结果落盘，下次启动不再探"
    assert "browser.close" in playwright.log, "探针浏览器用完即关"


def test_ua_probe_failure_does_not_block_the_session(playwright: FakePlaywright) -> None:
    """探针起不来（Chrome 路径怪 / 被占用）：沿用原生 headless UA 照常启动。"""
    playwright.chromium.probe_fails = True
    session = browser.BrowserSession()
    asyncio.run(session.start())
    (launch,) = _persistent(playwright)
    assert "user_agent" not in launch and session.started
    assert not (browser.PROFILE_DIR.parent / "chrome-ua.json").exists(), "探针失败不写缓存"


def test_fresh_ua_cache_skips_the_probe(playwright: FakePlaywright) -> None:
    browser._write_ua_cache("Mozilla/5.0 (fixed) Chrome/151.0.2.3 Safari/537.36")
    playwright.log.clear()
    session = browser.BrowserSession()
    asyncio.run(session.start())
    assert [kw.get("probe") for kw in playwright.chromium.launches] == [None], "只有持久 context 这一次启动"
    assert _persistent(playwright)[0]["user_agent"] == "Mozilla/5.0 (fixed) Chrome/151.0.2.3 Safari/537.36"


def test_stale_or_corrupt_ua_cache_is_ignored(playwright: FakePlaywright) -> None:
    path = browser.PROFILE_DIR.parent / "chrome-ua.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ua": "old", "ts": time.time() - browser._UA_CACHE_TTL - 1}))
    assert browser._read_ua_cache() is None, "过期"
    path.write_text("{ not json")
    assert browser._read_ua_cache() is None, "损坏"
    path.write_text(json.dumps({"ua": "", "ts": time.time()}))
    assert browser._read_ua_cache() is None, "空值"


def test_start_refuses_when_the_profile_is_in_use(playwright: FakePlaywright) -> None:
    browser.PROFILE_DIR.mkdir(parents=True)
    (browser.PROFILE_DIR / "SingletonLock").symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    session = browser.BrowserSession()
    with pytest.raises(ProfileInUse):
        asyncio.run(session.start())
    assert not session.started and playwright.chromium.launches == [], "没有拉起任何 Chrome"


# ---- run / close / 单例 ----


def test_run_auto_starts_and_serializes(playwright: FakePlaywright) -> None:
    session = browser.BrowserSession()
    active = 0
    peak = 0

    async def op(ctx, n: int) -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return n

    async def flow() -> list[int]:
        return await asyncio.gather(*(session.run(op, i) for i in range(5)))

    assert sorted(asyncio.run(flow())) == list(range(5))
    assert peak == 1, "全 app 排队：同一时刻只有一个操作在跑"
    assert len(_persistent(playwright)) == 1, "五个并发 run 只拉起一次"


def test_close_is_idempotent_and_the_session_reopens(playwright: FakePlaywright) -> None:
    session = browser.BrowserSession()
    asyncio.run(session.run(lambda ctx: ctx.cookies("https://x.test/")))
    asyncio.run(session.close())
    asyncio.run(session.close())  # 幂等
    assert not session.started and playwright.stopped
    asyncio.run(session.run(_noop))
    assert len(_persistent(playwright)) == 2, "关掉之后下次取数重开"


def test_close_survives_a_broken_context(playwright: FakePlaywright) -> None:
    """退出路径上 context 可能已经半死：关不掉也不能把异常抛进退出流程。"""
    session = browser.BrowserSession()
    asyncio.run(session.start())
    playwright.chromium.contexts[0]._fail_close = True
    asyncio.run(session.close())
    assert not session.started and playwright.stopped


def test_process_level_singleton(monkeypatch: pytest.MonkeyPatch, playwright: FakePlaywright) -> None:
    monkeypatch.setattr(browser, "_SESSION", None)
    first = browser.get_session()
    assert browser.get_session() is first

    async def cookies_flow() -> dict[str, str]:
        return await browser.profile_cookies("https://x.test/")

    assert asyncio.run(cookies_flow()) == {"z_c0": "session", "d_n_v": "x"}
    asyncio.run(browser.drop_profile_cookies("https://x.test/", ("z_c0",)))
    assert playwright.chromium.contexts[0].cleared == ["z_c0"]
    asyncio.run(browser.close_session())
    assert browser.get_session() is not first, "close_session 之后是一个全新的会话"


# ---- 瞬态页 ----


class _Ctx:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage([])
        self.pages.append(page)
        return page


def test_run_page_closes_the_page_on_success_error_and_cancel() -> None:
    ctx = _Ctx()

    async def ok(page: FakePage) -> str:
        return "done"

    async def boom(page: FakePage) -> None:
        raise SourceError("wall")

    async def flow() -> None:
        assert await run_page(ctx, ok) == "done"
        with pytest.raises(SourceError, match="wall"):
            await run_page(ctx, boom)
        hang = asyncio.ensure_future(run_page(ctx, lambda p: asyncio.sleep(10)))
        await asyncio.sleep(0)
        hang.cancel()
        with pytest.raises(asyncio.CancelledError):
            await hang

    asyncio.run(flow())
    assert [page.closed for page in ctx.pages] == [True, True, True], "成功 / 异常 / 被取消都关页"


def test_close_page_swallows_errors() -> None:
    class Dead(FakePage):
        def is_closed(self) -> bool:
            return False

        async def close(self) -> None:
            raise RuntimeError("Target closed")

    asyncio.run(browser.close_page(Dead([])))  # 不抛
    closed = FakePage([])
    closed.closed = True
    asyncio.run(browser.close_page(closed))
    assert "page.close" not in closed._log, "已关的页不再关一次"
