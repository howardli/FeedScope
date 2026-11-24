"""主屏行集与已读基类：_shown 行集、已读同步、过滤与未读导航（从 main.py 外移）。

继承链：MainSetupScreen（源启用编排）→ MainNavScreen（导航）→
MainReadScreen（本模块）→ MainScreen（取数上屏）。本层拥有「行集」的
唯一映射 _shown()（全量 / F4 未读子集 / 标题过滤的 AND 组合）与已读
状态的行级同步；渲染（rebuild_items）与当前选中（current_item）由
MainScreen 实现（抽象钩子），items 全量列表由其初始化（注解契约）。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from textual.binding import Binding
from textual.widgets import ListView

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.ui import Bindings, feedback
from feedscope.ui.dialogs import ConfirmScreen, ask_title_filter
from feedscope.ui.list_render import MARK_UNREAD, jump_unread, update_row_mark
from feedscope.ui.screens.main_nav import MainNavScreen


class MainReadScreen(MainNavScreen):
    """行集（F4 未读 / 标题过滤）、行首圆点已读态、未读跳转与清仓。"""

    # 取数族状态（MainScreen.__init__ 初始化）：行集的原料永远是全量
    items: list[FeedItem]

    # 渲染与选中在 MainScreen（取数上屏层），本层只声明契约
    @abstractmethod
    async def rebuild_items(self) -> None:
        """按当前宽度/过滤态重排列表（实现见 MainScreen）。"""

    @abstractmethod
    def current_item(self) -> FeedItem | None:
        """列表当前选中条目（实现见 MainScreen）。"""

    @abstractmethod
    def _refresh_sidebar(self) -> None:
        """请求边栏角标重建（实现见 MainScreen）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("ctrl+r", "toggle_read", "bindings.toggle_read", show=False),
        Binding("f4", "unread_only", "bindings.unread_only", show=False),
        # / 标题过滤（newsboat 惯例）：弹输入层，Enter 设词/空清除、Esc 不变；
        # [/] 环扫跳未读——与回答屏楼层步进（] 下一楼）同键同语义
        Binding("/", "title_filter", "bindings.title_filter", show=False),
        Binding("]", "next_unread", "bindings.next_unread", show=False),
        Binding("[", "prev_unread", "bindings.prev_unread", show=False),
        # 全部已读用 Ctrl+A 而不是 Ctrl+Shift+R：终端没启用键盘增强协议时
        # Shift 不可区分（发的是同一个字节）——真终端按 Ctrl+Shift+R 会落成
        # Ctrl+R 单条切换，Pilot 直注事件测不出来；Ctrl+A 各终端都可靠送达
        Binding("ctrl+a", "mark_all_read", "bindings.mark_all_read", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        # F4 只看未读：会话级开关不落盘（重启回全量）；只影响渲染层，
        # self.items 始终是全量（loader 契约 / Ctrl+N / 收藏快照都不受影响）
        self._unread_only = False
        # / 标题过滤（会话级，同 F4 只动渲染层）：None = 未过滤；小写子串匹配
        self._title_filter: str | None = None

    # ---- 行集（行↔条目的唯一映射；渲染/同步/计数全部走这里）----

    def _shown(self) -> list[FeedItem]:
        """当前渲染的行集：全量 / 未读子集（F4）/ 标题过滤（/）的 AND 组合。"""
        shown = self.items
        if self._unread_only:
            shown = [it for it in shown if not self._tui.state.is_read(it.source, it.id)]
        if self._title_filter:
            q = self._title_filter
            shown = [it for it in shown if q in it.title.lower()]
        return shown

    def _empty_hint(self) -> str:
        """过滤态空表的专属指路（F4 与 / 并存时给更具体的标题过滤那条）。"""
        if self._title_filter:
            return t("main.empty_filter", q=self._title_filter)
        if self._unread_only:
            return t("main.empty_unread_only")
        return t("main.empty_no_content")

    # ---- 已读/未读（状态在 core/store.py，行首圆点见 ui/list_render.py）----

    def _row_mark(self, it: FeedItem) -> str:
        """行首圆点二态：未读 ●（编辑器蓝）、已读空。

        未读是持久态——刷新/重启/缓存先画共用同一条规则，圆点只随「打开」
        消失（传统阅读器语义）；「本批新了几条」不进行标记，归状态行计数。
        """
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _mark_read(self, item: FeedItem) -> None:
        """打开即已读（Enter 进详情 / 浏览器兜底 / ←/→ 换篇 / Ctrl+O）。

        顺带把续读位置记到这一条（只改内存，随下面标已读的落盘一起写出）：
        打开是「我在读这儿」的明确信号，异常退出（没有退出路径）时下次启动
        至少回到最后打开的这条。"""
        state = self._tui.state
        state.hold_position(self.current_source.source_id, self.current_feed, item.id)
        self._drop_resume()  # 用户自己打开了条目：启动时的续读目标不再适用
        state.mark_read(item.source, item.id)
        self._sync_after_read(item)

    def _sync_after_read(self, item: FeedItem | None = None) -> None:
        """已读变化后的行与边栏同步（收藏屏代读也走这里）。

        过滤态整表重画——读过的行直接消失（newsboat 同款语义）并保选中位；
        全量态就地点一行圆标；边栏角标两种态都要刷新。
        """
        self._refresh_sidebar()
        if self._unread_only:
            # rebuild 走独立组（同组 exclusive 只互相串行）：与 _refresh 共用
            # default 组会把在途刷新整个取消——状态行卡在「加载中」且无失败
            # 提示。过滤态重画与刷新的最终 _paint_items 都经 _shown()，并发
            # 各画各的仍然一致
            self.run_worker(self.rebuild_items(), group="rebuild", exclusive=True)
        elif item is not None:
            self._update_row_mark(item)

    def _update_row_mark(self, item: FeedItem) -> None:
        """单行圆点就地重算（标记/切换不重建整表；重建路径自会走 _row_mark）。
        定位与置灰收口在 list_render.update_row_mark——按 (source, id) 寻址
        而不是对象相等（收藏屏回写传的是快照副本）；收藏屏同名方法同一条路。"""
        update_row_mark(
            self.query_one("#feed", ListView), self._shown(), item, self._row_mark(item)
        )

    # ---- 动作：过滤与未读导航 ----

    def action_unread_only(self) -> None:
        """F4：只看未读开关（会话级，重启回全量；不影响 loader/Ctrl+N 的全量数据）。"""
        self._unread_only = not self._unread_only
        feedback.info(
            self, t("main.toast_unread_on" if self._unread_only else "main.toast_unread_off")
        )
        # rebuild 走独立组（组语义见 _sync_after_read 的注释）
        self.run_worker(self.rebuild_items(), group="rebuild", exclusive=True)

    def action_title_filter(self) -> None:
        """/：标题过滤（newsboat 惯例，会话级）。弹输入层：Enter 设词（大小写
        不敏感子串）/ 空提交 = 清除 / Esc = 不变。走 worker：wait_for_dismiss
        只能在 worker 里用（同 _mark_all_flow 的既有形态）。"""
        self.app.run_worker(self._title_filter_flow(), group="titlefilter")

    async def _title_filter_flow(self) -> None:
        new = await ask_title_filter(self, self._title_filter)  # 问答与 toast 收口在 dialogs
        if new == self._title_filter:
            return  # Esc/词没变：不动现有过滤（取消输入 ≠ 清除过滤）
        self._title_filter = new
        await self.rebuild_items()

    def action_next_unread(self) -> None:
        """]：环扫跳到下一条未读（详情屏换楼层同键）。"""
        self._jump_unread(+1)

    def action_prev_unread(self) -> None:
        """[：环扫跳到上一条未读。"""
        self._jump_unread(-1)

    def _jump_unread(self, delta: int) -> None:
        """沿 delta 方向环扫一圈找首条未读（环扫本体在 list_render.jump_unread，
        收藏屏共用）；一圈无未读 toast 收尾。F4 过滤态天然兼容（行集本就是未读）。"""
        if not jump_unread(
            self.query_one("#feed", ListView),
            self._shown(),
            lambda it: self._tui.state.is_read(it.source, it.id),
            delta,
        ):
            feedback.info(self, t("main.no_unread"))
            return
        self._drop_resume()  # 用户自己动了选中

    # ---- 动作：清仓与单条切换 ----

    def action_mark_all_read(self) -> None:
        """Ctrl+A：当前流全部标已读。过滤态下标的是**全部**（确认文案即全量数）。"""
        if not self.items:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self.app.run_worker(self._mark_all_flow(), group="markall")

    async def _mark_all_flow(self) -> None:
        """清仓流：轻确认 → 批量置已读 + 角标清零 → 行圆点全清/过滤态清空。"""
        ok = await self.app.push_screen(
            ConfirmScreen(t("main.confirm_mark_all", n=len(self.items))), wait_for_dismiss=True
        )
        if not ok:
            return
        n = len(self.items)
        self._tui.state.mark_all_read(self.items, self.current_feed)
        feedback.info(self, t("main.toast_marked_all", n=n))
        await self.rebuild_items()
        self._refresh_sidebar()

    def action_toggle_read(self) -> None:
        """Ctrl+R：当前条目手动切换已读/未读（想重看的标回未读）。

        统一走 _sync_after_read：过滤态下切成已读 = 行从视图消失 + 角标递减
        （单行点圆点在过滤态会失配——_shown 已不含刚读的条目）。
        """
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))  # 空态/加载中按键不无声
            return
        read = self._tui.state.toggle_read(item.source, item.id)
        feedback.info(self, t("main.toast_marked_read" if read else "main.toast_marked_unread"))
        self._sync_after_read(item)
