"""账号动线：撞墙自动询问与 Ctrl+U 手动登录/登出的编排（从 app.py 拆出）。

MainScreen 只留 Ctrl+U 转发，状态与全过程在这里：
- in_progress（有头登录进行中）：挡下刷新/切源，独占 profile 锁期间不碰浏览器
- refused（本次运行内已拒绝询问的源）：撞墙不再自动弹框，只留常驻
  「Ctrl+U 登录」提示；Ctrl+U 手动登录照常可用，登录成功/登出后清除，
  后续撞墙（登录过期、换账号）恢复自动询问
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedscope import login
from feedscope.core.source import Source
from feedscope.core.i18n import t
from feedscope.ui import feedback
from feedscope.ui.dialogs import AccountScreen

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp


class AccountFlow:
    """一个 App 一份账号动线（当前源语境由调用方给，不自己记选中位）。"""

    def __init__(self, app: FeedScopeApp) -> None:
        self.app = app
        self.in_progress = False  # 有头登录进行中（挡刷新，见 action_refresh）
        self.refused: set[str] = set()  # 已拒绝自动询问的源 id（本运行内有效）
        # 会话已失效但判登还被骗着的源 id（cookie 在、服务端已拒绝——知乎
        # 详情 403 形态）：Ctrl+U 据此直接给登录框，省「先登出再登录」一步。
        # 登录成功/登出即清除。不像 X 的 401 那样清 cookie：403 无法与风控
        # 彻底区分，会话可能仍有效，清了等于强制重登
        self.stale: set[str] = set()

    def mark_stale(self, source: Source) -> None:
        """取数撞 LoginRequired 时由 UI 各出口调用：记下会话失效。

        无脑记即可：真未登录的源（cookie 都没有）本来就走登录框，多记
        无副作用；这个集合只在「判登说已登录」时才改变 Ctrl+U 的分派。
        """
        self.stale.add(source.source_id)

    async def on_login_wall(self, source: Source, hint: str) -> None:
        """取数撞登录墙：弹登录框（等用户选「登录」才开浏览器，不擅自弹窗）。

        每个源一次；取消即记住（本运行内不再自动问），切页/刷新不被反复
        打断。调用方（_refresh）已在 worker 里，wait_for_dismiss 合法。
        """
        proceed = await self.app.push_screen(
            AccountScreen(source.label_text, "dialogs.login"), wait_for_dismiss=True
        )
        if proceed:
            self.app.run_worker(self.login(source), group="login", exclusive=True)
        else:
            self.refused.add(source.source_id)
            if (main := self.app.main) is not None:
                main.show_empty(hint)
            feedback.info(self.app, t("account.login_refused"))

    async def open(self, source: Source) -> None:
        """Ctrl+U 的账号框：按当前源状态分派——匿名源提示、已登录弹登出、
        未登录弹登录。

        走 worker 因为 push_screen 的 wait_for_dismiss 只能在 worker 里用。
        group 独立于列表加载的 default 组，Ctrl+U 不会打断在途的 _refresh。
        """
        if not source.login_url:
            feedback.info(self.app, t("account.no_login_needed", label=source.label_text))
            return
        if self.in_progress:
            feedback.info(self.app, t("account.login_in_progress"))
            return
        # stale 命中时跳过判登（cookie 还躺着只会骗人）：直接按未登录分派
        logged_in = source.source_id not in self.stale and await login.is_logged_in(
            source.source_id
        )
        action_key = "dialogs.logout" if logged_in else "dialogs.login"
        proceed = await self.app.push_screen(
            AccountScreen(source.label_text, action_key), wait_for_dismiss=True
        )
        if not proceed:
            if action_key == "dialogs.login":
                # 手动框里取消同样算拒绝：之后撞墙不再自动弹（登出框取消无此语义）
                self.refused.add(source.source_id)
            return
        # 后续动作放独立的 login 组：exclusive 会取消同组在途 worker，若与本
        # worker（account 组）同组，等于把调用者自己也排进取消名单——现在能
        # 活着只是因为派发后不再 await；分组错开即消除对这层时序的依赖。
        if logged_in:
            self.app.run_worker(self.logout(source), group="login", exclusive=True)
        else:
            self.app.run_worker(self.login(source), group="login", exclusive=True)

    async def login(self, source: Source) -> None:
        """关 headless session 让出 profile 锁 → 有头浏览器登录 → 刷新。

        登录期间置 in_progress，挡下刷新误触发 profile 锁冲突；完成后
        恢复并刷新（成功则列表出内容，失败/超时仍撞墙）。
        """
        self.in_progress = True
        try:
            if (main := self.app.main) is not None:
                await main.release_feed_loader()  # 释放活页签，别跟有头登录抢 profile
            await login.yield_profile()  # 关无头会话让出 profile 锁（语义在 login 侧）
            try:
                ok = await login.login(source.source_id, quiet=True)
            except Exception:
                ok = False
            if ok:
                self.refused.discard(source.source_id)  # 后续撞墙（过期）恢复自动询问
                self.stale.discard(source.source_id)  # 新会话已生效，失效记忆作废
                feedback.info(self.app, t("account.login_ok", label=source.label_text))
            else:
                # 未完成（超时/关窗放弃）：别再自动追问，重试入口在 toast 与常驻提示里
                self.refused.add(source.source_id)
                feedback.warn(
                    self.app,
                    t("account.login_incomplete", label=source.label_text),
                    title=t("account.title_login"),
                )
        finally:
            self.in_progress = False
            if self.app.main is not None:
                self.app.main.action_refresh()
                # action_refresh 有 in_progress 挡门：复位必须在刷新之前（finally 保序）

    async def logout(self, source: Source) -> None:
        """清掉当前源登录 cookie → 刷新（撞墙弹登录框，即换账号动线）。"""
        self.refused.discard(source.source_id)  # 主动登出 = 想换账号：撞墙要重新弹登录框
        self.stale.discard(source.source_id)  # cookie 已清，失效记忆没了意义
        try:
            had = await login.logout(source.source_id)
        except Exception as exc:
            feedback.fail(self.app, source, exc, action="feedback.action_logout_failed")
            return
        if had:
            feedback.info(self.app, t("account.logged_out", label=source.label_text))
        else:
            feedback.info(self.app, t("account.not_logged_in", label=source.label_text))
        if self.app.main is not None:
            self.app.main.action_refresh()
