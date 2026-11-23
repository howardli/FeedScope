"""主屏导航基类：三面板方向键导航与切站/切流（从 main.py 外移）。

继承链：MainSetupScreen（源启用编排）→ MainNavScreen（本模块：导航）
→ MainReadScreen（行集与已读）→ MainScreen（取数上屏）。本层只管
「焦点在哪、键往哪去」；tab/边栏渲染与刷新由子类实现（抽象钩子见
MainSetupScreen），取数状态（sources/source_idx/feed_idx）由 MainScreen
初始化（注解契约在 MainSetupScreen）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from feedscope.core.i18n import t
from feedscope.core.source import Source
from feedscope.ui import Bindings, feedback
from feedscope.ui.screens.main_setup import MainSetupScreen
from feedscope.ui.widgets import FeedListView


class Pane(StrEnum):
    """主屏的区域焦点（纯逻辑层，Textual 焦点始终留在列表）。"""

    LIST = "list"
    SIDEBAR = "sidebar"
    TABS = "tabs"


class MainNavScreen(MainSetupScreen):
    """导航多路合一：顶部 tab 点击 / Tab 循环切站，侧栏点击 / ←→ 切流，
    全屏方向键的三面板边界跃迁（列表 ⇄ 边栏 ⇄ 源栏）。"""

    # 两轴导航：Tab/Shift+Tab 切站、←/→ 切流。键位全部非打印键（输入法免疫：
    # 中文输入法只拦字母/数字进候选框）。屏级绑定只在本屏前台时命中——
    # 弹层/详情屏在前台时这些键归它们管，主屏动作不需要守卫。
    # CLI 惯例的空间语义：↑/↓ 在纵向列表里移动选中；首行再 ↑ 上探源栏
    # （顶部区域在所有内容之上；源栏 ↓ 下探边栏，与边栏首流 ↑ 互逆）；
    # 其余区域移动走横向（列表 ← 进边栏、边栏 → 回列表 / ← 去源栏）；
    # 列表 → 抵屏幕右缘无事。详情/评论屏的 ←/→ 换篇在各屏自己的绑定。
    BINDINGS: ClassVar[Bindings] = [
        Binding("left", "nav_left", "bindings.sidebar", show=False),
        Binding("right", "nav_right", "bindings.right_edge", show=False),
        Binding("up", "nav_up", "bindings.move", show=False),
        Binding("down", "nav_down", "bindings.move", show=False),
        Binding("enter", "nav_enter", "bindings.open", show=False),
        Binding("escape", "nav_back", "bindings.back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        # 主屏区域焦点（默认列表），方向键边界跃迁（见 _enter_pane）；
        # source_id -> 上次选的信息流（切站回到上次看的板块，F2 收尾时
        # 会按新启用集清洗，见 MainSetupScreen._apply_enabled）
        self._pane = Pane.LIST
        self._feed_memory: dict[str, int] = {}

    # ---- 当前位置（子类的取数/渲染沿这两个属性寻址）----

    @property
    def current_source(self) -> Source:
        return self.sources[self.source_idx]

    @property
    def current_feed(self) -> str:
        feeds = self.current_source.feeds
        return feeds[self.feed_idx] if feeds else ""

    def _restore_feed_idx(self) -> None:
        """切站后停回该站上次的信息流（_feed_memory）；越界（流被隐藏/删除）回 0。

        MainSetupScreen 声明的抽象钩子在这里落地（F2 启用集变化后也要回停）。"""
        feeds = self.current_source.feeds if self.sources else []
        mem = self._feed_memory.get(self.current_source.source_id, 0) if self.sources else 0
        self.feed_idx = mem if feeds and 0 <= mem < len(feeds) else 0

    # ---- 切站 / 切流 ----

    async def _goto_source(self, target: int) -> None:
        """切到第 target 个源：Tab 循环（±1）与 tab 点击共用。

        两个入口都只在主屏生效（列表绑定 / 主屏点击），不会在详情屏下触发。
        只有一个源或已在目标源时不做任何事。
        """
        if len(self.sources) < 2:
            return
        target %= len(self.sources)
        if target == self.source_idx:
            return
        if self._login_blocks(self.sources[target]):
            return
        self.source_idx = target
        self._restore_feed_idx()  # 回到该站上次的信息流
        await self._render_source_tabs()  # 高亮跟随（键盘/点击都走这里）
        await self._render_feed_sidebar()
        if self._pane != Pane.TABS:
            self._enter_pane(Pane.SIDEBAR)  # 切站落边栏：先挑流再回列表（源栏态连切不被拽走）
        self.action_refresh()

    def _login_blocks(self, source: Source) -> bool:
        """有头登录进行中且目标源走浏览器通道（声明了登录页）：切过去也刷不出来
        （profile 被登录窗口独占，action_refresh 会挡），列表却停在上一个流的
        内容上——tab/边栏高亮与列表错位，回车还会拿新源去开旧条的详情。干脆不切。"""
        if self._tui.accounts.in_progress and source.login_url:
            feedback.info(self, t("account.login_switch_later", label=source.label_text))
            return True
        return False

    async def action_next_source(self) -> None:
        await self._goto_source(self.source_idx + 1)

    async def action_prev_source(self) -> None:
        await self._goto_source(self.source_idx - 1)

    async def action_select_source(self, idx: int) -> None:
        """顶部 tab 点击的落点：直接切到第 idx 个源。"""
        await self._goto_source(idx)

    async def action_select_feed(self, idx: int) -> None:
        feeds = self.current_source.feeds
        if not feeds or not 0 <= idx < len(feeds):
            return
        if self.feed_idx == idx:
            return  # 已在该流，不重复刷新
        if self._login_blocks(self.current_source):  # 同站切流同样刷不出来：不动选中
            return
        self.feed_idx = idx
        self._feed_memory[self.current_source.source_id] = idx  # 切站回来还在这个板块
        await self._render_feed_sidebar()  # 高亮跟随（←/→ /点击都走这里）
        self.action_refresh()

    # ---- 方向键分派 ----

    async def action_nav_left(self) -> None:
        """← 的单点分派：源栏态切上一个站；边栏态去源栏；列表态进边栏——
        对齐当前流、不换流（空间语义：← 永远是向左的区域移动）。"""
        if self._pane == Pane.TABS:
            await self.action_prev_source()  # 与 Tab 同一条切站路径
        elif self._pane == Pane.SIDEBAR:
            self._enter_pane(Pane.TABS)
        else:
            self._enter_pane(Pane.SIDEBAR)

    async def action_nav_right(self) -> None:
        """→ 的单点分派：源栏态切下一个站；边栏态回列表；列表态抵屏幕右缘，
        静默无事（与源栏顶、列表底的同款边界行为）。"""
        if self._pane == Pane.TABS:
            await self.action_next_source()
        elif self._pane == Pane.SIDEBAR:
            self._enter_pane(Pane.LIST)

    async def _step_feed(self, delta: int) -> None:
        """边栏 ↑/↓ 切流：末流到边给反馈；首个流再 ↑ 上探源栏（与列表首条
        ↑ 同规——顶部区域在所有内容之上；源栏 ↓ 下探与之互逆）。

        列表态不再切流——← 是区域移动（进边栏停当前流），切流只发生在
        边栏的 ↑/↓（纵向列表的本职）。
        """
        if not self.sources:
            return
        target = self.feed_idx + delta
        if not 0 <= target < len(self.current_source.feeds):
            if delta < 0:
                self._enter_pane(Pane.TABS)  # 首个流再 ↑：上探源栏
            else:
                feedback.info(self, t("main.toast_last_feed"))
            return
        await self.action_select_feed(target)

    # ---- 全屏方向键导航：列表 ⇄ 左侧边栏 ⇄ 顶部源栏（边界跃迁）----

    def _enter_pane(self, pane: Pane) -> None:
        """区域焦点切换（撞到边界继续往相邻区域走，键位表见 README）。

        pane 是纯逻辑层：Textual 焦点始终留在列表（主屏唯一可聚焦控件，
        Tab 切站的 widget 绑定才能随时命中、连按不退化），非列表态给 #feed
        挂 pane-aside 让列表光标转「暗」（仿 unfocused，见 CSS）；容器
        pane-focus 标记聚焦区域，_render_* 全量重建子项不受影响。
        """
        if pane == self._pane:
            return
        if pane == Pane.TABS and len(self.sources) < 2:
            return  # 单源无站可切，跃迁进去只剩空操作（与 Tab 切站同款静默）
        self._pane = pane
        self.query_one("#source-tabs", Horizontal).set_class(pane == Pane.TABS, "pane-focus")
        self.query_one("#feed-nav", Vertical).set_class(pane == Pane.SIDEBAR, "pane-focus")
        self.query_one("#feed", FeedListView).set_class(pane != Pane.LIST, "pane-aside")
        if pane != Pane.LIST:
            feedback.info(
                self,
                t("main.hint_top_bar") if pane == Pane.TABS else t("main.hint_sidebar"),
            )

    async def action_nav_up(self) -> None:
        """↑ 的单点分派：边栏态切上一个流（首个流再 ↑ 上探源栏，由
        _step_feed 分派）；源栏态抵屏幕上缘；列表态移文章选中（首条再
        ↑ 上探源栏——顶部区域在所有内容之上）。焦点丢了先找回。"""
        if self._pane == Pane.SIDEBAR:
            await self._step_feed(-1)
            return
        if self._pane == Pane.TABS:
            return
        view = self.query_one("#feed", FeedListView)
        if not view.has_focus:
            view.focus()  # 焦点丢失（测试置空/竞态）：↑ 先把焦点找回来
            return
        if view.index in (None, 0):
            self._enter_pane(Pane.TABS)  # 首条再 ↑：上探源栏
        else:
            view.action_cursor_up()

    async def action_nav_down(self) -> None:
        """↓ 的单点分派：边栏态切下一个流（末流到边给反馈）；源栏态下探
        边栏（唯一的纵向区域移动：顶部 → 边栏）；列表态移文章选中。"""
        if self._pane == Pane.SIDEBAR:
            await self._step_feed(1)
            return
        if self._pane == Pane.TABS:
            self._enter_pane(Pane.SIDEBAR)
            return
        view = self.query_one("#feed", FeedListView)
        if not view.has_focus:
            view.focus()
            return
        view.action_cursor_down()

    def action_nav_enter(self) -> None:
        """Enter 的单点分派：边栏/源栏态回列表（流/站已随行走就地切换）；
        列表态 = 原生选中（进详情/开浏览器，与改派前同一条路径）。

        详情屏 Enter 进评论层是屏级绑定（先命中），到不了这。
        """
        if self._pane == Pane.LIST:
            self.query_one("#feed", FeedListView).action_select_cursor()
        else:
            self._enter_pane(Pane.LIST)

    def action_nav_back(self) -> None:
        """屏级 Esc：边栏/源栏态先回列表（逐层返回的最内层）。

        弹层/详情屏的 Esc 由各自屏级绑定先命中，落不到这。
        """
        if self._pane != Pane.LIST:
            self._enter_pane(Pane.LIST)

    async def action_nav_scroll(self, key: str) -> None:
        """home/end/pageup/pagedown：列表视口滚动，只在列表态生效——键盘
        在边栏/源栏时去滚一个看不见的列表，违反「焦点在哪键就作用在哪」。
        """
        if self._pane != Pane.LIST:
            return
        view = self.query_one("#feed", FeedListView)
        fn = {
            "home": view.action_scroll_home,
            "end": view.action_scroll_end,
            "pageup": view.action_page_up,
            "pagedown": view.action_page_down,
        }[key]
        maybe = fn()  # 新版 Textual 的滚动动作返回 AwaitComplete，可等可不等
        if maybe is not None and hasattr(maybe, "__await__"):
            await maybe
