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

from feedscope.core import samples
from feedscope.core.const import PAGE_TIMEOUT_MS, chrome_launch_kwargs
from feedscope.core.source import SourceError

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


class ChannelTab:
    """常驻取数通道页签：停靠某 URL 的进程级单例 + evaluate 自愈 + 页内 fetch 原语。

    敌意源（知乎/Reddit/X）共用的取数通道形态——请求由真 Chrome 的页面
    fetch 发出，TLS/头序/cookie 与登录会话完全自洽。生命周期全在这里：
    单例复用、页签被关重建、evaluate 撞上在途跳转（context destroyed）
    换页签重试一次。页签随 BrowserSession 生灭，无需显式释放。

    各源差异走四个显式扩展点，不往本类里加源名分支：
    - check：开页校验钩子（知乎的登录墙）
    - fetch() 的 headers / cookie_headers / referrer / as_text：每次请求的
      头形状与正文形态（Reddit 的 Accept+版本头、X 的 Bearer+ct0 csrf、
      知乎的 x-zse-93 与 referer）
    - eval()：任意复合脚本的逃生口（知乎的开题并行拉取/子回复补全——
      多请求一 evaluate 的形态单次 fetch 表达不了）
    - 登录/自愈语义（401/403 转译、版本头与 queryId 的收割重试）留在
      各源 fetch 层：策略各站不同，统一反而互相缠绕
    """

    def __init__(
        self,
        park_url: str,
        *,
        check: Callable[[Any], Awaitable[None]] | None = None,
        name: str = "",
    ) -> None:
        self._park_url = park_url
        self._check = check  # 开页后校验（登录墙等）：抛即弃页原样上交
        self._name = name or park_url  # 定位标签（日志/排错）
        self._page: Any = None

    async def get(self) -> Any:
        """取（必要时开）通道页签；页签被关后下一次取重新开。"""
        if self._page is not None and not self._page.is_closed():
            return self._page

        async def _open(ctx: Any) -> Any:
            page = await ctx.new_page()
            try:
                await page.goto(
                    self._park_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
                )
                if self._check is not None:
                    await self._check(page)
            except BaseException:
                await close_page(page)
                raise
            return page

        self._page = await get_session().run(_open)
        return self._page

    async def eval(self, script: str, arg: Any = None) -> Any:
        """页签里 evaluate；页签废了（在途跳转/被关）换新页签重试一次。

        页面 fetch 自身的失败不走这层（JS 里按约定回 __status/__error，
        不抛异常穿过 evaluate），只有 Playwright 层的页签失效才重建。
        """
        for attempt in range(2):
            page = await self.get()
            try:
                return await page.evaluate(script, arg)
            except Exception:
                # 弃掉的页签要真关掉：只置 None 会让它在 context 里留成孤儿页，
                # 自愈几次就多出几只（浏览器会话关闭前一直占内存）
                await close_page(page)
                if attempt:
                    raise
                self._page = None

    async def fetch(
        self,
        url: str,
        *,
        source: str,
        headers: dict[str, str] | None = None,
        cookie_headers: dict[str, str] | None = None,
        referrer: str | None = None,
        as_text: bool = False,
    ) -> tuple[int | None, Any]:
        """页签里发一次 fetch -> (HTTP 状态码或 None, 正文)；网络/结构失败转 SourceError。

        统一的页内取数原语（知乎/Reddit/X 共用，返回约定 __status/__error/
        __body 见 _FETCH_JS）：UA/头序/cookie/TLS 全是真 Chrome 的，调用方只
        补浏览器不会自动带的东西——headers 手动头；cookie_headers「cookie 值
        提升为头」的映射（如 X 的 ct0 -> X-Csrf-Token，页内就地读、永远新鲜，
        与 SPA 自己同一动作）；referrer 指定则覆盖页签自然 referer；as_text
        拿 HTML 文本（Reddit）否则按 JSON 解析（知乎/X），此时顶层不是 JSON
        对象（登录墙 HTML / 代理错误页）也在这里一并报「结构异常」。成功且
        拿到正文时顺手落样本（samples 未开启零开销，probe 改版修复流水线
        三源同权）。状态码原样上交，401/403 的语义转译归各源。
        """
        try:
            data = await self.eval(
                _FETCH_JS, [url, headers or {}, cookie_headers or {}, referrer, as_text]
            )
        except Exception as exc:  # Playwright 层失败（重建重试也没救回来）
            raise SourceError(f"{source} 请求失败：{exc}") from exc
        if not isinstance(data, dict):
            raise SourceError(f"{source} 返回结构异常")
        if err := data.get("__error"):
            raise SourceError(f"{source} 请求失败：{err}")
        status = data.get("__status")
        body = data.get("__body")
        if status is None and body is None:
            raise SourceError(f"{source} 返回结构异常")
        if not as_text and body is not None and not isinstance(body, dict):
            raise SourceError(f"{source} 返回结构异常（不是 JSON 对象）")
        status_i = int(status) if status else None
        if status_i is None and body is not None:
            # 样本名用通道名（ascii：zhihu/reddit/x），source 只管错误文案——
            # 纯中文标签过 url_name 会被清成 src，样本就没主了
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            samples.save(samples.url_name(self._name, url), text)
        return status_i, body

    def reset(self) -> None:
        """弃单例（下一次 get 重开）；出错自愈路径用。"""
        self._page = None


# 页签里执行的统一 fetch：失败按约定回 __status（非 2xx）/__error（网络或
# 解析异常），成功回 __body（文本或 JSON）。cookie_headers 把指定 cookie 的
# 值就地提升为请求头（页内读永远新鲜）；不抛 JS 异常穿过 evaluate
_FETCH_JS = """
async ([url, headers, cookieHeaders, referrer, asText]) => {
  try {
    const h = Object.assign({}, headers);
    for (const [name, cookie] of Object.entries(cookieHeaders)) {
      // cookie 名里的正则元字符先转义（名字是调用方给的，不当正则源用）
      const esc = cookie.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
      const v = (document.cookie.match(new RegExp("(?:^|;\\\\s*)" + esc + "=([^;]+)")) || [])[1];
      if (v) h[name] = decodeURIComponent(v);
    }
    const opts = { headers: h, credentials: "include" };
    if (referrer) opts.referrer = referrer;
    const r = await fetch(url, opts);
    if (!r.ok) return { __status: r.status };
    return { __body: asText ? await r.text() : await r.json() };
  } catch (e) { return { __error: String(e) }; }
}
"""


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
        (PROFILE_DIR.parent / "chrome-ua.json").write_text(
            json.dumps({"ua": ua, "ts": time.time()})
        )
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
        self._pw: Any = None  # Playwright 句柄
        self._context: Any = None  # BrowserContext
        self._lock = asyncio.Lock()

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
            ua_kwargs: dict[str, Any] = {"user_agent": ua} if ua else {}
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=True,  # TUI 内无头；login 子命令另有有头入口
                **chrome_launch_kwargs(),  # channel/viewport/locale 等，与 login 共用
                **ua_kwargs,
            )

    async def run(self, op: Callable[..., Awaitable[_T]], *args: Any, **kwargs: Any) -> _T:
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

    判登门这类「本地看 cookie」的检查用（X/Reddit 的源声明层）：返回全部
    name->value，判登规则（哪个键在、怎么解码）由调用方自己定，不在这里
    替它做决定。
    """
    cookies = await get_session().run(lambda ctx: ctx.cookies(url))
    return {c["name"]: c["value"] for c in cookies}


async def drop_profile_cookies(url: str, names: tuple[str, ...]) -> None:
    """清掉持久 profile 里指定名字的 cookie（本地操作，与 logout 同一动作）。

    登录态被服务端吊销后 cookie 只是「还躺在本地」——不清掉的话判登
    （cookie 在即算已登录）与账号屏会一直被骗过，用户只能反复撞 401/403。
    源在确认登录态失效后调这里清掉再抛 LoginRequired（X 的 401 自愈路径）。
    url 只为对齐 profile_cookies 的形态：clear_cookies 按名字全域清。
    """

    async def _clear(ctx: Any) -> None:
        for name in names:
            await ctx.clear_cookies(name=name)

    await get_session().run(_clear)
