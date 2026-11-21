"""core/browser.py 离线单测：headless UA 修正的替换语义（不启动浏览器）。"""

from __future__ import annotations

from feedscope.core.browser import _HEADLESS_UA_RE

_PREFIX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
_FULL = "152.0.7977.65"


def _fix(ua: str) -> str:
    """_chrome_ua 的替换步骤（版本号由探针浏览器给，这里固定值）。"""
    return _HEADLESS_UA_RE.sub(lambda _: f"Chrome/{_FULL}", ua)


def test_frozen_headless_token_is_replaced() -> None:
    ua = _PREFIX + "HeadlessChrome/152.0.0.0 Safari/537.36"
    assert _fix(ua) == _PREFIX + f"Chrome/{_FULL} Safari/537.36"


def test_full_version_headless_token_is_replaced() -> None:
    """冻结形态若变成完整小版本（如 HeadlessChrome/152.0.7977.0），同样要换掉，
    不能静默漏替换——漏了 CF jsd 挑战会挂死不自解。"""
    ua = _PREFIX + "HeadlessChrome/152.0.7977.0 Safari/537.36"
    assert "HeadlessChrome" not in _fix(ua)


def test_normal_chrome_ua_untouched() -> None:
    ua = _PREFIX + f"Chrome/{_FULL} Safari/537.36"
    assert _fix(ua) == ua
    assert _HEADLESS_UA_RE.search(ua) is None  # 无标记 = 调用方走「无须修」分支


# ---- UA 探针落盘缓存（7 天 TTL；探不到不缓存）----


def test_ua_cache_roundtrip_and_ttl(tmp_path, monkeypatch) -> None:
    from feedscope.core import browser

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "profile")
    assert browser._read_ua_cache() is None  # 无缓存
    browser._write_ua_cache("Mozilla/5.0 Chrome/156.0.8078.74")
    assert browser._read_ua_cache() == "Mozilla/5.0 Chrome/156.0.8078.74"
    # 时间老化：ts 回拨 8 天 -> 过期
    import json
    import time
    cache = tmp_path / "chrome-ua.json"
    data = json.loads(cache.read_text())
    data["ts"] = time.time() - 8 * 24 * 3600
    cache.write_text(json.dumps(data))
    assert browser._read_ua_cache() is None


def test_ua_cache_corrupt_file_is_miss(tmp_path, monkeypatch) -> None:
    from feedscope.core import browser

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "profile")
    (tmp_path / "chrome-ua.json").write_text("not-json{")
    assert browser._read_ua_cache() is None


def test_channel_tab_concurrent_get_opens_one_page(monkeypatch) -> None:
    """两个并发 get() 都观察到页签失效（get 的检查与开页不在同一临界区）：
    session 锁内双检复用——修复前各开一页、后写 _page 的覆盖先开的，
    先开的页签泄漏成孤儿（浏览器会话关闭前一直占内存）。"""
    import asyncio

    from feedscope.core import browser
    from feedscope.core.browser import ChannelTab

    opened: list[_RacePage] = []

    class _RacePage:
        def __init__(self) -> None:
            self.closed = False
            opened.append(self)

        def is_closed(self) -> bool:
            return self.closed

        async def goto(self, url: str, **kw: object) -> None:
            await asyncio.sleep(0.01)  # 拉开窗口：两个 get 都在开页途中

        async def close(self) -> None:
            self.closed = True

    class _Ctx:
        async def new_page(self) -> _RacePage:
            await asyncio.sleep(0)
            return _RacePage()

    class _Session:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()

        async def run(self, op, *args, **kwargs):
            async with self._lock:  # 与 BrowserSession.run 同形：全 app 串行
                return await op(_Ctx(), *args, **kwargs)

    session = _Session()
    monkeypatch.setattr(browser, "get_session", lambda: session)

    async def _run() -> None:
        tab = ChannelTab("https://example.com/", name="race")
        first, second = await asyncio.gather(tab.get(), tab.get())
        assert first is second, "并发取到同一页签"
        assert len(opened) == 1, "只开了一页（无孤儿）"
        assert not opened[0].closed

    asyncio.run(_run())
