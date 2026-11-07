"""登录流程：有头 Chrome 手动登录（知乎扫码、X/Reddit 账号密码），检测到源声明的判据即成功。

为什么有头：二维码在手机上、账号密码要手输，窗口必须可见。
为什么独立命令：persistent profile 同一时刻只允许一个实例；`feedscope login`
子命令仍保留（纯命令行场景），但 TUI 内现在也能登录了（见下）。
哪些源需要登录、登录页在哪、拿什么判登，全部由 Source 的 ClassVar 声明
（login_url / login_cookie / login_verify / logout_cookies），本文件不认识
任何具体源——新增需要登录的源只改它自己的包，不动这里。判据优先级：
login_verify（浏览器级钩子，如 Reddit 的 JWT 解码）> logout_cookies 全集
（如 X 的 auth_token+ct0）> login_cookie 单枚兜底。

本文件也承载 TUI 内的登录 / 登出：
- `is_logged_in` / `logout` 复用同一份源声明判据，TUI 撞登录墙后无需退出
  即可弹浏览器登录、或一键登出换账号。
- `login(quiet=True)` 供 TUI 调用：抑制 stdout 打印（Textual 会捕获 stdout，
  落进退出时的刷屏），反馈改由 UI 层自己弹。
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from feedscope.core.browser import PROFILE_DIR, close_session, drop_stale_locks
from feedscope.core.const import PAGE_TIMEOUT_MS, chrome_launch_kwargs
from feedscope.core.i18n import t

LOGIN_TIMEOUT = 180  # 秒；3 分钟没扫上就退出
POLL_INTERVAL = 2


async def login(source_id: str = "zhihu", *, quiet: bool = False) -> bool:
    import feedscope.sources  # noqa: F401  import 即注册
    from feedscope.core.registry import all_sources, get_source

    try:
        source = get_source(source_id)
    except KeyError:
        ids = ", ".join(s.source_id for s in all_sources())
        _say(quiet, t("login.unknown_source", source=source_id, ids=ids))
        return False
    if not source.login_url:
        _say(quiet, t("login.not_needed", label=source.label_text))
        return False

    from playwright.async_api import async_playwright

    _say(quiet, t("login.starting", dir=PROFILE_DIR))
    _say(quiet, t("login.prompt", label=source.label_text))
    drop_stale_locks()
    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,  # 登录窗口必须可见（扫码/手输密码）
                **chrome_launch_kwargs(),  # channel/viewport/locale 等，与 browser.py 共用
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    source.login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
                )
                # 轮询单独兜底：判据钩子抛错报「登录检测出错」，不被外层误标成
                # 「浏览器启动失败」（浏览器明明活着，误导排障）
                try:
                    if await _await_login(context, source, quiet):
                        return True
                except Exception as exc:
                    _say(quiet, t("login.check_error", error=exc))
                    return False
            finally:
                await context.close()
    except Exception as exc:  # 浏览器没装/起不来/导航超时：给中文出口，不裸栈
        _say(quiet, t("login.browser_failed", error=exc))
        return False
    _say(quiet, t("login.timeout", seconds=LOGIN_TIMEOUT))
    return False


async def _await_login(context, source, quiet: bool) -> bool:
    """轮询源声明的判据（口径见 _logged_in）直到成功（True，已报喜）或超时（False）。"""
    deadline = asyncio.get_running_loop().time() + LOGIN_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if await _logged_in(context, source):
            if source.login_verify is not None:
                how = t("login.success_state")
            else:
                joiner = t("common.enum_comma")
                how = t("login.success_cookies", cookies=joiner.join(source.judging_cookies))
            _say(quiet, t("login.success", how=how))
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def yield_profile() -> None:
    """关掉无头会话让出 profile 锁（有头登录前必经；TUI 账号动线调用）。

    persistent profile 同一时刻只允许一个实例：无头取数会话不先关掉，
    有头登录窗口会撞锁起不来。登录完成后取数按需自然重开（get_session
    惰性启动）。调用方还要先释放各自持有的活页签（TUI 的列表 loader）。
    """
    await close_session()


async def _present_cookies(context, source) -> list[dict]:
    """读某源登录域下的全部 cookie（本地操作，不导航、不触发反爬）。"""
    domain = urlparse(source.login_url).netloc
    return await context.cookies(f"https://{domain}")


async def _logged_in(context, source) -> bool:
    """源声明的判据：login_verify 优先，否则判登 cookie 全集都在。

    cookie 全集口径与 login() 轮询一致（Source.judging_cookies：X 要
    auth_token+ct0 成对，未声明全集才退单枚 login_cookie）；Reddit 这类
    匿名访客也发 cookie 的源由 login_verify 做 JWT 解码。
    """
    if source.login_verify is not None:
        return bool(await source.login_verify(context))
    needed = source.judging_cookies
    if not needed:
        return False
    names = {c["name"] for c in await _present_cookies(context, source)}
    return set(needed) <= names


async def _clear_cookies(context, source) -> bool:
    """清掉源声明的登录 cookie；返回是否确有登录态被清掉。

    「确有」与判登共用 _logged_in（login_verify 优先）；半套 cookie
    （如只剩 auth_token 没有 ct0）本就不算已登录，登出反馈「本就未登录」；
    cookie 照样全清，不留脏状态。"""

    had = await _logged_in(context, source)
    for name in source.judging_cookies:
        await context.clear_cookies(name=name)
    return had


async def is_logged_in(source_id: str = "zhihu") -> bool:
    """当前 profile 是否已登录某源；无任何判据（login_verify / cookie）的源恒为 False。"""
    from feedscope.core.registry import get_source

    source = get_source(source_id)
    if source.login_verify is None and not source.judging_cookies:
        return False
    from feedscope.core.browser import get_session

    return await get_session().run(_logged_in, source)


async def logout(source_id: str = "zhihu") -> bool:
    """登出某源：清掉判登 cookie；返回是否确有登录态被清掉。"""
    from feedscope.core.registry import get_source

    source = get_source(source_id)
    if not source.judging_cookies:
        return False
    from feedscope.core.browser import get_session

    return await get_session().run(_clear_cookies, source)


def _say(quiet: bool, msg: str) -> None:
    """CLI 入口打印；TUI（quiet=True）由 UI 层自行反馈，不在 stdout 落字。"""
    if not quiet:
        print(msg)
