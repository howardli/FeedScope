"""BrowserSession：本机 Chrome + 持久 profile 的唯一入口。

约定：
- 全 app 单例、惰性启动（首次用到才拉起浏览器）
- 所有页面操作走 run()，持锁串行执行 -> 无并发竞争
- profile 是独立目录，绝不碰用户日常 Chrome 的默认 profile
- App 退出时 close_session() 兜底清理
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, TypeVar

from platformdirs import user_cache_path

from feedscope.core.const import chrome_launch_kwargs

logger = logging.getLogger(__name__)

PROFILE_DIR = user_cache_path("feedscope") / "chromium-profile"


def drop_stale_locks() -> None:
    """异常退出残留的 Singleton* 锁会让 Chrome 拒绝启动，启动前清掉。"""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = PROFILE_DIR / name
        try:
            if lock.is_symlink() or lock.exists():
                lock.unlink()
        except OSError:
            logger.warning("无法清理残留锁 %s", lock)

_T = TypeVar("_T")

_HEADLESS_UA_RE = re.compile(r"HeadlessChrome/[\d.]+")


async def run_page(context: Any, op: Callable[[Any], Awaitable[_T]]) -> _T:
    """开新页执行 op(page) 并保证页签关闭（成功、异常、被取消都关）；页不跨调用存活。

    适用于「开页 → 取数 → 关页」的瞬态操作（知乎热榜）。
    需要跨多次 load_more 活着的页签不要用它（知乎信息流/详情），
    那类页签由持有它的 Loader 用 close_page() 负责关闭。
    """
    page = await context.new_page()
    try:
        return await op(page)
    finally:
        with suppress(Exception):
            await page.close()


async def close_page(page: Any) -> None:
    """关闭 Loader 持有的页签：幂等，出错自己吞（与 Loader.close 同约定）。"""
    try:
        if not page.is_closed():
            await page.close()
    except Exception:
        pass


_UA_CACHE_TTL = 7 * 24 * 3600  # Chrome 版本几天到几周才变，探针结果落盘缓存


def _read_ua_cache() -> str | None:
    """读 UA 缓存（含 Chrome 版本探针的完整结果）；过期/缺失/损坏返回 None。"""
    try:
        data = json.loads((PROFILE_DIR.parent / "chrome-ua.json").read_text())
        if data.get("ua") and time.time() - data.get("ts", 0) < _UA_CACHE_TTL:
            return data["ua"]
    except (OSError, ValueError):
        pass
    return None


def _write_ua_cache(ua: str) -> None:
    """落盘 UA 缓存（写失败不声张：下次再探一次而已）。"""
    try:
        PROFILE_DIR.parent.mkdir(parents=True, exist_ok=True)
        (PROFILE_DIR.parent / "chrome-ua.json").write_text(json.dumps({"ua": ua, "ts": time.time()}))
    except OSError:
        logger.warning("UA 缓存写盘失败（不影响本次运行）")


async def _chrome_ua(pw: Any) -> str | None:
    """把无头 UA 修成与本机有头 Chrome 逐字一致（探针浏览器问真实版本）。

    无头 Chrome 的 UA 自带 HeadlessChrome/<major>.0.0.0 标记（小版本被冻结），
    反爬（如 Cloudflare 的托管挑战）见到它就挂死不自解。其余指纹
    （brands/WebGL/webdriver）本就干净，只改 UA 即可全过；用探针浏览器
    拿完整版本号，Chrome 自动升级后 UA 也跟着对。探针失败则返回 None 不覆写，
    别让这条反爬路径挡住其他源。探针要额外拉起一只 Chrome（~1s），结果落盘
    缓存 7 天（见 _read_ua_cache）——常规启动直接命中，不再付这笔钱。
    """
    if cached := _read_ua_cache():
        return cached
    browser = await pw.chromium.launch(channel="chrome", headless=True)
    try:
        page = await browser.new_page()
        headless_ua: str = await page.evaluate("navigator.userAgent")
        version: str = browser.version.removeprefix("Chrome/")  # 关掉前取，如 152.0.7977.65
    finally:
        await browser.close()
    # 整只 HeadlessChrome/<版本> token 换成 Chrome/<完整版本>：冻结形态
    # （major.0.0.0）与完整小版本形态都吃得下，不会静默漏替换
    ua, n = _HEADLESS_UA_RE.subn(lambda _: f"Chrome/{version}", headless_ua)
    if n == 0:
        return None  # 未来 Chrome 不再打 Headless 标记，就无须修
    _write_ua_cache(ua)
    logger.info("headless UA 修正: %s", ua)
    return ua


class BrowserSession:
    """管理一个 Playwright persistent context（channel 固定为本机 Chrome）。"""

    def __init__(self) -> None:
        self._pw: Any = None        # Playwright 句柄
        self._context: Any = None   # BrowserContext
        self._lock = asyncio.Lock()
        self.ua: str | None = None  # 探针修过的完整 Chrome UA（探针失败 None）；httpx 源对齐用

    @property
    def started(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        if self._context is not None:
            return
        async with self._lock:
            if self._context is not None:  # double-check：并发 start 只拉起一次
                return
            from playwright.async_api import async_playwright

            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            drop_stale_locks()
            logger.info("launch chrome, profile=%s", PROFILE_DIR)
            self._pw = await async_playwright().start()
            try:
                ua = await _chrome_ua(self._pw)
            except Exception:
                logger.warning("探测 Chrome 版本失败，沿用原生 headless UA", exc_info=True)
                ua = None
            self.ua = ua  # 暴露给 httpx 源（X）：请求 UA 与浏览器会话逐字一致
            ua_kwargs: dict[str, Any] = {"user_agent": ua} if ua else {}
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=True,  # TUI 内无头；login 子命令另有有头入口
                **chrome_launch_kwargs(),  # channel/viewport/locale 等，与 login 共用
                **ua_kwargs,
            )

    async def run(
        self, op: Callable[..., Awaitable[_T]], *args: Any, **kwargs: Any
    ) -> _T:
        """串行执行一次浏览器操作：op(context, *args, **kwargs) -> awaitable，全 app 排队走这里。"""
        await self.start()
        async with self._lock:
            return await op(self._context, *args, **kwargs)

    async def close(self) -> None:
        """直接关闭，不抢锁：退出路径上可能还有持锁的取数任务，不能被它卡死。"""
        context, pw = self._context, self._pw
        self._context = self._pw = None
        if context is not None:
            with suppress(Exception):
                await context.close()
        if pw is not None:
            with suppress(Exception):
                await pw.stop()


_SESSION: BrowserSession | None = None


def get_session() -> BrowserSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = BrowserSession()
    return _SESSION


async def close_session() -> None:
    global _SESSION
    if _SESSION is not None:
        await _SESSION.close()
        _SESSION = None


async def profile_cookies(url: str) -> dict[str, str]:
    """读持久 profile 某域的 cookie（本地操作，不导航、不触发反爬）。

    X 这类「profile 读 cookie、httpx 直调」的源用：返回全部 name->value，
    判登（auth_token / ct0 是否在）由调用方自己看键，不在这里替它做决定。
    """
    cookies = await get_session().run(lambda ctx: ctx.cookies(url))
    return {c["name"]: c["value"] for c in cookies}


async def drop_profile_cookies(url: str, names: tuple[str, ...]) -> None:
    """清掉持久 profile 里指定名字的 cookie（本地操作，与 logout 同一动作）。

    登录态被服务端吊销后 cookie 只是「还躺在本地」——不清掉的话判登
    （cookie 在即算已登录）与账号屏会一直被骗过，用户只能反复撞 401/403。
    httpx 源（Reddit/X）在确认登录态失效后调这里再抛 LoginRequired。
    url 只为对齐 profile_cookies 的形态：clear_cookies 按名字全域清。
    """
    async def _clear(ctx: Any) -> None:
        for name in names:
            await ctx.clear_cookies(name=name)

    await get_session().run(_clear)
