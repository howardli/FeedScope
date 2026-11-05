"""登录流程：有头 Chrome 手动登录（知乎扫码、X 账号密码），检测到源声明的 cookie 即成功。

为什么有头：二维码在手机上、账号密码要手输，窗口必须可见。
为什么独立命令：persistent profile 同一时刻只允许一个实例；`feedscope login`
子命令仍保留（纯命令行场景），但 TUI 内现在也能登录了（见下）。
哪些源需要登录、登录页在哪、拿哪个 cookie 判登，全部由 Source 的 ClassVar
声明（login_url / login_cookie），本文件不认识任何具体源——新增需要登录的
源只改它自己的包，不动这里。

自 Step 12 起，本文件也承载 TUI 内的登录 / 登出：
- `is_logged_in` / `logout` 复用 Source.logout_cookies（判登 cookie 全集），
  TUI 撞登录墙后无需退出即可弹浏览器登录、或一键登出换账号。
- `login(quiet=True)` 供 TUI 调用：抑制 stdout 打印（Textual 会捕获 stdout，
  落进退出时的刷屏），反馈改由 UI 层自己弹。
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from feedscope.core.browser import PROFILE_DIR, drop_stale_locks
from feedscope.core.const import PAGE_TIMEOUT_MS, chrome_launch_kwargs

LOGIN_TIMEOUT = 180  # 秒；3 分钟没扫上就退出
POLL_INTERVAL = 2


async def login(source_id: str = "zhihu", *, quiet: bool = False) -> bool:
    import feedscope.sources  # noqa: F401  import 即注册
    from feedscope.core.registry import all_sources, get_source

    try:
        source = get_source(source_id)
    except KeyError:
        ids = ", ".join(s.source_id for s in all_sources())
        _say(quiet, f"未知源：{source_id}（已注册：{ids}）")
        return False
    if not source.login_url:
        _say(quiet, f"{source.label} 无需登录（匿名可访问）")
        return False

    from playwright.async_api import async_playwright

    domain = urlparse(source.login_url).netloc
    _say(quiet, f"启动浏览器（profile: {PROFILE_DIR}）")
    _say(quiet, f"请在弹出的 Chrome 窗口里登录 {source.label}，完成后本命令自动退出。")
    drop_stale_locks()
    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,  # 登录窗口必须可见（扫码/手输密码）
                **chrome_launch_kwargs(),  # channel/viewport/locale 等，与 browser.py 共用
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                source.login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
            )
            try:
                deadline = asyncio.get_running_loop().time() + LOGIN_TIMEOUT
                while asyncio.get_running_loop().time() < deadline:
                    if source.login_verify is not None:
                        # 浏览器级判据（声明 login_verify 的源）：本地判定，零网络
                        # （如 Reddit 的 token_v2 JWT 解码）——有的源匿名访客也发
                        # 判登 cookie（Reddit 的 token_v2 打开登录页即有），
                        # cookie 出现 ≠ 登录完成
                        ok = bool(await source.login_verify(context))
                    else:
                        cookies = await context.cookies(f"https://{domain}")
                        ok = any(c["name"] == source.login_cookie for c in cookies)
                    if ok:
                        how = (
                            "登录态已写入 profile"
                            if source.login_verify is not None
                            else f"{source.login_cookie} 已写入 profile"
                        )
                        _say(quiet, f"登录成功：{how}，之后无头复用。")
                        return True
                    await asyncio.sleep(POLL_INTERVAL)
            finally:
                await context.close()
    except Exception as exc:  # 浏览器没装/起不来/导航超时：给中文出口，不裸栈
        _say(quiet, f"浏览器启动或打开登录页失败：{exc}")
        return False
    _say(quiet, f"超时：{LOGIN_TIMEOUT} 秒内未检测到登录，重新运行 feedscope login 再试。")
    return False


async def _present_cookies(context, source) -> list[dict]:
    """读某源登录域下的全部 cookie（本地操作，不导航、不触发反爬）。"""
    domain = urlparse(source.login_url).netloc
    return await context.cookies(f"https://{domain}")


async def _has_all_cookies(context, source) -> bool:
    """判登 cookie 全集（logout_cookies）都在 = 已登录；X 要求 auth_token+ct0 成对。"""
    cookies = await _present_cookies(context, source)
    names = {c["name"] for c in cookies}
    return set(source.logout_cookies) <= names


async def _clear_cookies(context, source) -> bool:
    """清掉源声明的登录 cookie；返回是否确有登录态被清掉。

    「确有」的判据与 _has_all_cookies 对称（判登全集都在才算）——半套
    cookie（如只剩 auth_token 没有 ct0）本就不算已登录，登出反馈「本就
    未登录」；cookie 照样全清，不留脏状态。"""
    names = {c["name"] for c in await _present_cookies(context, source)}
    had = set(source.logout_cookies) <= names
    for name in source.logout_cookies:
        await context.clear_cookies(name=name)
    return had


async def is_logged_in(source_id: str = "zhihu") -> bool:
    """当前 profile 是否已登录某源；匿名源（无 logout_cookies）恒为 False。"""
    from feedscope.core.registry import get_source

    source = get_source(source_id)
    if not source.logout_cookies:
        return False
    from feedscope.core.browser import get_session

    return await get_session().run(_has_all_cookies, source)


async def logout(source_id: str = "zhihu") -> bool:
    """登出某源：清掉判登 cookie；返回是否确有登录态被清掉。"""
    from feedscope.core.registry import get_source

    source = get_source(source_id)
    if not source.logout_cookies:
        return False
    from feedscope.core.browser import get_session

    return await get_session().run(_clear_cookies, source)


def _say(quiet: bool, msg: str) -> None:
    """CLI 入口打印；TUI（quiet=True）由 UI 层自行反馈，不在 stdout 落字。"""
    if not quiet:
        print(msg)
