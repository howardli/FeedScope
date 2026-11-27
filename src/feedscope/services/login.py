"""登录流程：有头 Chrome 手动登录（知乎扫码、X/Reddit 账号密码），检测到源声明的判据即成功。

为什么有头：二维码在手机上、账号密码要手输，窗口必须可见。
为什么独立命令：persistent profile 同一时刻只允许一个实例；`feedscope login`
子命令仍保留（纯命令行场景），但 TUI 内现在也能登录了（见下）。
哪些源需要登录、登录页在哪、拿什么判登，全部由 Source 声明（login_url /
login_cookie / logout_cookies 与判登谓词 Source.logged_in），本文件不认识
任何具体源——新增需要登录的源只改它自己的包，不动这里。

本文件也承载 TUI 内的登录 / 登出：
- `is_logged_in` / `logout` 复用同一份源声明判据，TUI 撞登录墙后无需退出
  即可弹浏览器登录、或一键登出换账号。
- `login(quiet=True)` 供 TUI 调用：抑制 stdout 打印（Textual 会捕获 stdout，
  落进退出时的刷屏），反馈改由 UI 层自己弹。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import feedscope.sources  # noqa: F401  import 即注册：登录按 source_id 取源
from feedscope.core.browser import (
    PAGE_TIMEOUT_MS,
    PROFILE_DIR,
    chrome_launch_kwargs,
    close_session,
    drop_stale_locks,
    get_session,
)
from feedscope.core.i18n import t
from feedscope.core.registry import all_sources, get_source
from feedscope.core.source import Source

if TYPE_CHECKING:  # 注解用：运行时保持函数内懒导入 playwright 的惯例
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT = 180  # 秒；3 分钟没扫上就退出
POLL_INTERVAL = 2


class _LoginAborted(Exception):
    """登录窗口被用户关掉（macOS 红灯/Cmd+W）：等不下去了，login() 转友好提示。"""


async def login(source_id: str = "zhihu", *, quiet: bool = False) -> bool:
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
                # 用户关掉登录窗口（macOS 红灯/Cmd+W）时 Chrome 进程不走、连接
                # 不断、判登轮询也不抛错——唯一信号是这一页没了。不监听的话
                # 要空等满 LOGIN_TIMEOUT，期间 TUI 被 in_progress 挡掉所有刷新
                window_gone = asyncio.Event()
                page.on("close", lambda _page: window_gone.set())
                await page.goto(
                    source.login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
                )
                # 轮询单独兜底：判据钩子抛错报「登录检测出错」，不被外层误标成
                # 「浏览器启动失败」（浏览器明明活着，误导排障）
                try:
                    if await _await_login(context, source, quiet, window_gone):
                        return True
                except _LoginAborted:
                    _say(quiet, t("login.window_closed"))
                    return False
                except Exception as exc:
                    logger.info("登录检测出错 source=%s", source.source_id, exc_info=True)
                    _say(quiet, t("login.check_error", error=exc))
                    return False
            finally:
                await context.close()
    except Exception as exc:  # 浏览器没装/起不来/导航超时：给中文出口，不裸栈
        logger.info("登录浏览器启动失败 source=%s", source.source_id, exc_info=True)
        _say(quiet, t("login.browser_failed", error=exc))
        return False
    _say(quiet, t("login.timeout", seconds=LOGIN_TIMEOUT))
    return False


async def _await_login(
    context: BrowserContext, source: Source, quiet: bool, window_gone: asyncio.Event | None = None
) -> bool:
    """轮询源声明的判据（口径见 _logged_in）直到成功（True，已报喜）或超时（False）。

    window_gone（登录页 close 事件）置位即抛 _LoginAborted：用户关窗放弃，
    不再空等（语义见 login() 里的接线注释）。
    """
    deadline = asyncio.get_running_loop().time() + LOGIN_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if await _logged_in(context, source):
            _say(quiet, t("login.success", how=source.login_evidence()))
            return True
        if window_gone is not None:
            if window_gone.is_set():
                raise _LoginAborted
            # 睡到下个轮询点，关窗信号先到先醒（不等满 POLL_INTERVAL）
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(window_gone.wait(), timeout=POLL_INTERVAL)
        else:
            await asyncio.sleep(POLL_INTERVAL)
    return False


async def yield_profile() -> None:
    """关掉无头会话让出 profile 锁（有头登录前必经；TUI 账号动线调用）。

    persistent profile 同一时刻只允许一个实例：无头取数会话不先关掉，
    有头登录窗口会撞锁起不来。登录完成后取数按需自然重开（get_session
    惰性启动）。调用方还要先释放各自持有的活页签（TUI 的列表 loader）。
    """
    await close_session()


async def _logged_in(context: BrowserContext, source: Source) -> bool:
    """源声明的判据：登录域的 cookie（名 -> 值）交给 Source.logged_in。

    与源取数入口的判登门（Source.require_login）是同一个谓词——登录轮询
    报成功的那一刻，取数一定过得去，不会两边口径分裂。
    """
    jar = {c["name"]: c["value"] for c in await context.cookies(source.login_origin)}
    return source.logged_in(jar)


async def _clear_cookies(context: BrowserContext, source: Source) -> bool:
    """清掉源声明的登录 cookie；返回是否确有登录态被清掉。

    「确有」与判登共用 _logged_in；半套 cookie（如只剩 auth_token 没有 ct0）
    本就不算已登录，登出反馈「本就未登录」；cookie 照样全清，不留脏状态。"""

    had = await _logged_in(context, source)
    for name in source.judging_cookies:
        await context.clear_cookies(name=name)
    return had


async def is_logged_in(source_id: str = "zhihu") -> bool:
    """当前 profile 是否已登录某源（判据 = Source.logged_in）；没有判登 cookie 的源恒为 False。"""
    source = get_source(source_id)
    if not source.judging_cookies:
        return False
    return await get_session().run(_logged_in, source)


async def logout(source_id: str = "zhihu") -> bool:
    """登出某源：清掉判登 cookie；返回是否确有登录态被清掉。"""
    source = get_source(source_id)
    if not source.judging_cookies:
        return False
    return await get_session().run(_clear_cookies, source)


def _say(quiet: bool, msg: str) -> None:
    """CLI 入口打印；TUI（quiet=True）由 UI 层自行反馈，不在 stdout 落字。"""
    if not quiet:
        print(msg)
