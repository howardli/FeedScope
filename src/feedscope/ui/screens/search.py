"""本地搜索屏（主屏 F6）：跨全部列表缓存与收藏找条目。

路线图里的「全文搜索已缓存条目」：数据是现成的（每流的列表缓存 JSON + 收藏
快照），这里只是给它一个入口——输入即搜（纯本地、零请求），标题命中的排在
正文命中的前面；↑↓ 选、Enter 进详情（跨源：按条目取源）、Esc 返回。

分页挂载 / 改宽重排 / 已读同步 / 换篇的机械件在 LocalListScreen（收藏屏
共用）；本屏只有搜索特有的三样：条目表的异步装载、输入即搜、输入框与结果
列表之间的焦点往返。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, Label

from feedscope.core import search
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.ui import Bindings
from feedscope.ui.focus_nav import FocusNavListView
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.list_render import LIST_ROW_CSS
from feedscope.ui.screens.help import push_help
from feedscope.ui.screens.local_list import LocalListScreen


class _SearchInput(Input):
    """↓ 从输入框下到结果列表（输入框不消费方向键，屏级绑定在这里命中）。"""

    BINDINGS: ClassVar[Bindings] = [Binding("down", "screen.focus_results", show=False)]


class _ResultListView(FocusNavListView):
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


class SearchScreen(LocalListScreen):
    """输入即搜：结果行与主列表同款（圆点 / 源名列 / 时间列 / 元信息列）。"""

    LIST_ID = "search-list"
    EMPTY_ID = "search-empty"

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
        self._items: list[FeedItem] = []  # 可搜的条目表（收藏 + 各流列表缓存，去重）
        self._query = ""
        self._results: list[FeedItem] = []  # 当前关键词的命中（标题命中在前）
        self._loaded = False  # 条目表读完（挂载后异步读的一小段里还不算就位）

    def compose(self) -> ComposeResult:
        yield Header()
        yield _SearchInput(placeholder=t("search.placeholder"), id="search-input")
        yield _ResultListView(id="search-list")
        yield Label("", id="search-empty", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#search-list", _ResultListView))
        self._remember_context()
        self.query_one("#search-input", Input).focus()
        self.app.sub_title = t("search.collecting")
        favorites = [it for _, it in self._tui.state.favorites()]
        # 读几十个缓存文件（RSS 的还带全文）：放工作线程，别卡事件循环
        self._items = await asyncio.to_thread(search.collect, favorites)
        self._loaded = True
        self._rerun()

    # ---- LocalListScreen 的差异件 ----

    def _rows(self) -> list[FeedItem]:
        return self._results

    def _data_ready(self) -> bool:
        # 条目表还在读（挂载后的一小段）时不重建：读完后 on_mount 会重建
        return self._loaded

    def _empty_text(self) -> str:
        return t("search.empty", q=self._query) if self._query.strip() else t("search.hint")

    def _status_text(self, total: int, mounted: int) -> str:
        if mounted < total:
            return t("search.status", n=total, m=mounted)
        return t("search.status_all", n=total)

    # ---- 搜索特有 ----

    def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value
        self._rerun()

    def _rerun(self) -> None:
        """关键词变了：重筛 + 回到首页重建。"""
        self._results = search.search(self._items, self._query)
        self._rerender()

    def action_focus_results(self) -> None:
        if self._results:
            view = self._list_view()
            view.focus()
            if view.index is None:
                view.index = 0

    def action_help(self) -> None:
        push_help(self, self.query_one("#search-list", _ResultListView))
