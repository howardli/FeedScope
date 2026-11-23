"""本地搜索屏（主屏 F6）：跨全部列表缓存与收藏找条目。

路线图里的「全文搜索已缓存条目」：数据是现成的（每流的列表缓存 JSON + 收藏
快照），这里只是给它一个入口——输入即搜（纯本地、零请求），标题命中的排在
正文命中的前面；↑↓ 选、Enter 进详情（跨源：按条目取源）、Esc 返回。
机械件（分页挂载 / 改宽重排 / 返回时重建）与收藏屏同形。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from feedscope.core import search
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources
from feedscope.core.source import UNKNOWN_SOURCE, Source
from feedscope.ui import Bindings, feedback, tui_of
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.launch import copy_link, open_link
from feedscope.ui.layout import RESIZE_DEBOUNCE, list_title_width, time_col_width
from feedscope.ui.list_render import (
    LIST_ROW_CSS,
    MARK_UNREAD,
    build_item,
    cross_source_tag,
    cross_source_widths,
)
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.help import push_help

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环

_PAGE = 100  # 分页步长：ListView 没有虚拟化（同收藏屏），Ctrl+N 只是渲染放量


class _SearchInput(Input):
    """↓ 从输入框下到结果列表（输入框不消费方向键，屏级绑定在这里命中）。"""

    BINDINGS: ClassVar[Bindings] = [Binding("down", "screen.focus_results", show=False)]


class _ResultListView(ListView):
    """Enter 归屏分派（与主列表 / 收藏列表同一纪律）；首行再 ↑ 回输入框接着改词。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("enter", "screen.nav_enter", "bindings.open_item", show=False),
        Binding("up", "cursor_up_or_input", show=False),
    ]

    def action_cursor_up_or_input(self) -> None:
        if self.index is None or self.index <= 0:
            self.screen.query_one("#search-input", Input).focus()
        else:
            self.action_cursor_up()


class SearchScreen(Screen[None]):
    """输入即搜：结果行与主列表同款（圆点 / 源名列 / 时间列 / 元信息列）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("escape", "back", "bindings.back"),
        Binding("ctrl+y", "copy_link", "bindings.copy", show=False),
        Binding("ctrl+o", "open_browser", "bindings.browser", show=False),
        Binding("ctrl+n", "more", "bindings.more", show=False),
        Binding("f1", "help", "bindings.help"),
    ]

    DEFAULT_CSS = LIST_ROW_CSS + """
    #search-input { dock: top; margin: 0 1; }
    #search-list { height: 1fr; }
    #search-empty { display: none; padding: 1 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._sources: dict[str, Source] = {}  # 全注册表（含已停用源），挂载时解析一次
        self._items: list[FeedItem] = []  # 可搜的条目表（收藏 + 各流列表缓存，去重）
        self._query = ""
        self._results: list[FeedItem] = []  # 当前关键词的命中（标题命中在前）
        self._shown_limit = _PAGE  # 已挂载行数上限（Ctrl+N 逐页放量；重建时回 _PAGE）
        self._tag_w = 0
        self._score_width = 1
        self._time_w = 0
        self._prev_sub_title: str | None = None
        self._last_width = 0
        self._resize_timer: Timer | None = None

    @property
    def _tui(self) -> FeedScopeApp:
        return tui_of(self)

    def compose(self) -> ComposeResult:
        yield Header()
        yield _SearchInput(placeholder=t("search.placeholder"), id="search-input")
        yield _ResultListView(id="search-list")
        yield Label("", id="search-empty", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#search-list", _ResultListView))
        self._prev_sub_title = self.app.sub_title
        self._sources = {s.source_id: s for s in all_sources()}
        self.query_one("#search-input", Input).focus()
        self._update_status()
        self.app.sub_title = t("search.collecting")
        favorites = [it for _, it in self._tui.state.favorites()]
        # 读几十个缓存文件（RSS 的还带全文）：放工作线程，别卡事件循环
        self._items = await asyncio.to_thread(search.collect, favorites)
        self._rerun()

    def on_screen_resume(self) -> None:
        self._rerender()  # 详情屏退回：已读圆点要跟着变

    def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
        if self._prev_sub_title is not None:
            self.app.sub_title = self._prev_sub_title

    def on_resize(self, event: Resize) -> None:
        """改宽防抖重建（标题折行算术依赖宽度；首布局前 region 为 0，首个 Resize 自愈）。"""
        if event.size.width in (self._last_width, 0):
            self._last_width = event.size.width
            return
        self._last_width = event.size.width
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self._rerender)

    # ---- 搜索与渲染 ----

    def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value
        self._rerun()

    def _rerun(self) -> None:
        """关键词变了：重筛 + 回到首页重建。"""
        self._results = search.search(self._items, self._query)
        self._shown_limit = _PAGE
        self._rerender()

    def _rerender(self) -> None:
        if self._items:
            self.run_worker(self._rebuild(), group="rebuild", exclusive=True)
        else:  # 条目表还在读（挂载后的一小段）：读完后 on_mount 会重建
            self._update_status()

    async def _rebuild(self) -> None:
        view = self.query_one("#search-list", ListView)
        mounted = self._results[: self._shown_limit]
        idx = view.index
        await view.clear()
        empty = self.query_one("#search-empty", Label)
        if mounted:
            self._tag_w, self._score_width = cross_source_widths(mounted, self._sources)
            self._time_w = time_col_width(mounted)
            await view.extend(self._build_rows(mounted))
            view.index = 0 if idx is None else min(idx, len(mounted) - 1)
            empty.display = False
        else:
            view.index = None
            empty.update(
                t("search.empty", q=self._query) if self._query.strip() else t("search.hint")
            )
            empty.display = True
        self._update_status()

    def _update_status(self) -> None:
        shown = min(len(self._results), self._shown_limit)
        self.app.sub_title = (
            t("search.status", n=len(self._results), m=shown)
            if shown < len(self._results)
            else t("search.status_all", n=len(self._results))
        )

    def _build_rows(self, items: list[FeedItem]) -> list[ListItem]:
        width = list_title_width(
            self.query_one("#search-list", ListView).region.width,
            self._score_width,
            self._tag_w,
            self._time_w,
        )
        return [
            build_item(
                it,
                width,
                self._score_width,
                self._sources.get(it.source, UNKNOWN_SOURCE),
                self._row_mark(it),
                tag=cross_source_tag(it, self._sources),
                tag_width=self._tag_w,
                time_width=self._time_w,
            )
            for it in items
        ]

    def _source_of(self, it: FeedItem) -> Source:
        """条目自己的源；注册表里已不存在的（源下架）用占位源。"""
        return self._sources.get(it.source, UNKNOWN_SOURCE)

    def _row_mark(self, it: FeedItem) -> str:
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _mounted(self) -> list[FeedItem]:
        return self._results[: self._shown_limit]

    def _current(self) -> FeedItem | None:
        view = self.query_one("#search-list", ListView)
        mounted = self._mounted()
        if not mounted or view.index is None or view.index >= len(mounted):
            return None
        return mounted[view.index]

    # ---- 键位动作 ----

    def action_focus_results(self) -> None:
        if self._results:
            view = self.query_one("#search-list", ListView)
            view.focus()
            if view.index is None:
                view.index = 0

    def action_nav_enter(self) -> None:
        self.query_one("#search-list", ListView).action_select_cursor()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_help(self) -> None:
        push_help(self, self.query_one("#search-list", _ResultListView))

    def action_more(self) -> None:
        if self._shown_limit >= len(self._results):
            feedback.info(self, t("main.toast_no_more"))
            return
        self._shown_limit = min(self._shown_limit + _PAGE, len(self._results))
        self.run_worker(self._rebuild(), group="rebuild", exclusive=True)

    def action_copy_link(self) -> None:
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        copy_link(self, item.url)

    def action_open_browser(self) -> None:
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self._mark_read(item)
        open_link(self, item.url)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = self._current()
        if item is None:
            return
        self._mark_read(item)
        source = self._sources.get(item.source)
        if source is not None and source.opens_detail(item):
            # 结果跨源：相邻条目来自不同源，详情屏按条目取源
            self.app.push_screen(
                DetailScreen(
                    item,
                    source,
                    self.step_readable,
                    step_unread=self.step_unread,
                    source_of=self._source_of,
                )
            )
        else:
            open_link(self, item.url)  # 源已下架 / 该类型不进详情

    def _mark_read(self, item: FeedItem) -> None:
        self._tui.state.mark_read(item.source, item.id)
        if (main := self._tui.main) is not None:
            main._sync_after_read(item)  # 主屏同一行圆点 + 边栏角标同步；本屏的行退回时重建

    def step_readable(self, delta: int) -> FeedItem | None:
        """详情屏 ←/→ 的供给端：沿结果推进到下一条可进终端详情的条目。"""
        return self._step(delta, unread_only=False)

    def step_unread(self, delta: int) -> FeedItem | None:
        """详情屏 ]/[ 的供给端：只停在未读的可读结果上。"""
        return self._step(delta, unread_only=True)

    def _step(self, delta: int, *, unread_only: bool) -> FeedItem | None:
        view = self.query_one("#search-list", ListView)
        mounted = self._mounted()
        if not mounted or view.index is None:
            return None
        for idx in range(view.index + delta, len(mounted) if delta > 0 else -1, delta):
            it = mounted[idx]
            source = self._sources.get(it.source)
            if source is None or not source.opens_detail(it):
                continue
            if unread_only and self._tui.state.is_read(it.source, it.id):
                continue
            view.index = idx
            self._mark_read(it)
            return it
        return None

