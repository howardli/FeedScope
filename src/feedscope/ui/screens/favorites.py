"""收藏屏（F3）：跨源收藏列表，数据全部来自本地快照（core/store.favorites）。

- 列表只读 favorites.json 快照（零请求、离线可用）：收藏的防失效价值正在于
  「条目从热榜流走 / 回答被删之后仍可读」；Enter 才按条目自己的源走
  open_detail 在线抓（要最新评论），RSS / Telegram 正文在 extra 里天然零请求
- 源实例从全注册表解析（收藏条目的源可能已被 F2 停用——停用只是不出现在
  主屏，注册表仍在）；注册表里都找不到的（未来的源下架）占位源兜底开浏览器
- ScreenResume 整屏重建：从详情屏退回时，已读圆点、在详情里按 Ctrl+F 取消
  的收藏都要反映——本屏不感知谁改了状态，重建是唯一可靠的同步
- 打开即已读与主屏同语义（Enter / Ctrl+O / ←/→ 换篇）；标记后顺带同步主屏
  同一条目的行圆点（主屏无 resume 重建，靠这里代更新）

分页挂载 / 改宽重排 / 已读同步 / 换篇与未读导航的机械件在 LocalListScreen
（搜索屏共用）；本屏只有收藏特有的三样：数据来自快照、Ctrl+F 取消收藏、
/ 过滤的空态与状态行文案。
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Label, ListView

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.ui import Bindings, feedback
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.list_render import LIST_ROW_CSS
from feedscope.ui.screens.help import push_help
from feedscope.ui.screens.local_list import LocalListScreen


class _FavListView(ListView):
    """Enter 归屏分派（show=False：页脚不露控件级键名，与主列表同一条纪律；
    描述给 F1 帮助页——控件要随 action_help 传进 push_help 才被扫到）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("enter", "screen.nav_enter", "bindings.open_item", show=False),
    ]


class FavoritesScreen(LocalListScreen):
    """收藏列表：Esc 返回，Enter 进详情/开浏览器，Ctrl+F 取消收藏。"""

    LIST_ID = "fav-list"
    EMPTY_ID = "fav-empty"

    # 页脚只留 Esc 返回 + F1 帮助（其余键提示退入 F1，绑定照常）
    BINDINGS: ClassVar[Bindings] = [
        Binding("escape", "back", "bindings.back"),
        Binding("ctrl+f", "unfavorite", "bindings.favorite", show=False),
        Binding("ctrl+o", "open_browser", "bindings.browser", show=False),
        Binding("ctrl+y", "copy_link", "bindings.copy", show=False),
        Binding("ctrl+n", "more", "bindings.more", show=False),
        Binding("/", "title_filter", "bindings.title_filter", show=False),
        # 稍后读队列的未读三件套在这里最要紧（想回头细看的标回未读）：与主列表同键同义
        Binding("ctrl+r", "toggle_read", "bindings.toggle_read", show=False),
        Binding("]", "next_unread", "bindings.next_unread", show=False),
        Binding("[", "prev_unread", "bindings.prev_unread", show=False),
        Binding("f1", "help", "bindings.help"),
    ]

    # 行样式与主屏同源（见 list_render.LIST_ROW_CSS 的说明）；本屏只留私有规则
    DEFAULT_CSS = LIST_ROW_CSS + """
    #fav-empty { display: none; padding: 1 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.items: list[FeedItem] = []  # 当前行序（收藏时间新→旧，行序 = items 序）

    def compose(self) -> ComposeResult:
        yield Header()
        yield _FavListView(id="fav-list")
        # markup=False：空态提示会拼入用户过滤词，纯文本出口不做标记解析
        yield Label("", id="fav-empty", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#fav-list", _FavListView))
        self._remember_context()
        await self._rebuild()
        self._list_view().focus()

    # ---- LocalListScreen 的差异件 ----

    def _rows(self) -> list[FeedItem]:
        return self.items

    async def _reload(self) -> None:
        """每次重建都重读快照：收藏可能在详情屏里被取消。"""
        self.items = [it for _, it in self._tui.state.favorites()]

    def _empty_text(self) -> str:
        if self.items:
            return t("fav.empty_filter", q=self._filter or "")  # 有收藏但被过滤光：指路去关过滤
        return t("fav.empty")

    def _status_text(self, total: int, mounted: int) -> str:
        if self._filter:
            # Header 副标题不做标记解析（实测）：过滤词原样给，转义反而会露出反斜杠
            return t("fav.status_filtered", n=total, q=self._filter)
        if mounted < total:
            return t("fav.status_paged", n=total, m=mounted)
        return t("fav.status_count", n=total)

    # ---- 收藏特有 ----

    def action_unfavorite(self) -> None:
        """Ctrl+F：取消当前收藏（全量快照随之删除），列表经合并式调度重建。"""
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self._tui.state.toggle_fav(item)
        feedback.info(self, t("fav.toast_removed"))
        self._rerender()

    def action_help(self) -> None:
        """F1：键位帮助（全局 + 本屏，从声明自动生成；入口收口在 push_help，
        列表控件随行——Enter 的声明在控件上，缺了它帮助页少一行）。"""
        push_help(self, self.query_one("#fav-list", _FavListView))
