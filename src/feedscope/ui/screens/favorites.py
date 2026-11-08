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
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Label, ListView

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources
from feedscope.core.source import FeedLoader, Source
from feedscope.core.text import display_width
from feedscope.ui import Bindings, feedback
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.layout import (
    META_COL_MAX,
    META_COL_SPARE,
    RESIZE_DEBOUNCE,
    list_title_width,
    meta_col_text,
)
from feedscope.ui.list_render import LIST_ROW_CSS, MARK_UNREAD, build_item
from feedscope.ui.screens.detail import DetailScreen

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环


class _FavListView(ListView):
    """Enter 归屏分派（show=False：页脚不露控件级键名，与主列表同一条纪律）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("enter", "screen.nav_enter", show=False),
    ]


class _FallbackSource(Source):
    """注册表里已不存在的源的占位（未来的源下架）：详情能力全关，Enter 走浏览器。

    跳过 Source.__init__：占位源不读配置不进任何流（空 feeds 走父类会误报
    「信息流已被全部隐藏」的警告，import 期还可能被真实配置触发）。
    """

    source_id = ""
    label = ""

    def __init__(self) -> None:
        self.feeds = []

    async def open_feed(self, name: str) -> FeedLoader:
        raise NotImplementedError


_FALLBACK = _FallbackSource()


class FavoritesScreen(Screen[None]):
    """收藏列表：Esc 返回，Enter 进详情/开浏览器，Ctrl+F 取消收藏。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("escape", "back", "bindings.back"),
        Binding("ctrl+f", "unfavorite", "bindings.favorite"),
        Binding("ctrl+o", "open_browser", "bindings.browser"),
        Binding("ctrl+y", "copy_link", "bindings.copy"),
    ]

    # 行样式与主屏同源（见 list_render.LIST_ROW_CSS 的说明）；本屏只留私有规则
    DEFAULT_CSS = LIST_ROW_CSS + """
    #fav-empty { display: none; padding: 1 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._sources: dict[str, Source] = {}  # 全注册表（含已停用源），挂载时解析一次
        self.items: list[FeedItem] = []  # 当前行序（收藏时间新→旧，行序 = items 序）
        self._score_width = 1 + META_COL_SPARE
        self._prev_sub_title: str | None = None  # 退屏恢复（同 ReadingScreen）
        self._last_width = 0
        self._resize_timer: Timer | None = None

    @property
    def _tui(self) -> FeedScopeApp:
        """self.app 的收窄视图（同 MainScreen._tui 的既有模式）。"""
        return cast("FeedScopeApp", self.app)

    def compose(self) -> ComposeResult:
        yield Header()
        yield _FavListView(id="fav-list")
        yield Label("", id="fav-empty")
        yield Footer()

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#fav-list", _FavListView))
        self._prev_sub_title = self.app.sub_title
        self._sources = {s.source_id: s for s in all_sources()}
        await self._rebuild()
        self.query_one("#fav-list", ListView).focus()

    async def on_screen_resume(self) -> None:
        # 详情屏退回：已读圆点 / 在详情里取消的收藏都要反映（见模块头）
        await self._rebuild()

    async def on_unmount(self) -> None:
        if self._prev_sub_title is not None:
            self.app.sub_title = self._prev_sub_title

    def on_resize(self, event) -> None:  # noqa: ANN001 - Textual Resize 事件
        """改宽防抖重建（标题折行算术依赖宽度；首布局前 region 为 0，首个 Resize 自愈）。"""
        if event.size.width in (self._last_width, 0):
            self._last_width = event.size.width
            return
        self._last_width = event.size.width
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self._rerender)

    def _rerender(self) -> None:
        self.run_worker(self._rebuild(), exclusive=True)

    # ---- 列表 ----

    def _source_of(self, it: FeedItem) -> Source:
        return self._sources.get(it.source, _FALLBACK)

    def _tag_of(self, it: FeedItem) -> str:
        """源名列文本：聚合流条目自带更具体的出处（订阅名/频道名）优先，
        否则用源词汇表显示名；注册表里都找不到的落回裸 source id。"""
        if it.tag:
            return it.tag
        src = self._sources.get(it.source)
        return src.label_text if src is not None else it.source

    def _tag_width_of(self) -> int:
        """源名列宽：批内最长源名 + 1（列内左对齐，各源名成列）。"""
        return max((display_width(self._tag_of(it)) for it in self.items), default=0) + 1

    def _score_width_of(self) -> int:
        """元信息列宽：跨源取各自源计算的最长值封顶 META_COL_MAX（多源版
        score_col_width——源名不拼进来，元信息保持各源在主列表的原生形态）。"""
        longest = max(
            (display_width(meta_col_text(self._source_of(it), it)) for it in self.items),
            default=1,
        )
        return min(longest, META_COL_MAX) + META_COL_SPARE

    async def _rebuild(self) -> None:
        """按当前收藏重建列表（挂载 / 每次 ScreenResume / resize 后调用；保留选中位）。"""
        view = self.query_one("#fav-list", ListView)
        idx = view.index
        pairs = self._tui.state.favorites()
        self.items = [it for _, it in pairs]
        await view.clear()
        empty = self.query_one("#fav-empty", Label)
        if self.items:
            self._score_width = self._score_width_of()
            tag_width = self._tag_width_of()
            width = list_title_width(view.region.width or 80, self._score_width, tag_width)
            await view.extend(
                [
                    build_item(
                        it,
                        width,
                        self._score_width,
                        self._source_of(it),
                        self._row_mark(it),
                        tag=self._tag_of(it),
                        tag_width=tag_width,
                    )
                    for it in self.items
                ]
            )
            view.index = 0 if idx is None else min(idx, len(self.items) - 1)
            empty.display = False
        else:
            empty.update(t("fav.empty"))
            empty.display = True
        self.app.sub_title = t("fav.status_count", n=len(self.items))

    # ---- 已读（与主屏同语义；标记后同步主屏同一行）----

    def _row_mark(self, it: FeedItem) -> str:
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _current(self) -> FeedItem | None:
        view = self.query_one("#fav-list", ListView)
        if not self.items or view.index is None:
            return None
        return self.items[view.index]

    def _mark_read(self, item: FeedItem) -> None:
        self._tui.state.mark_read(item.source, item.id)
        self._update_row_mark(item)
        if (main := self._tui.main) is not None:
            # 主屏同一行圆点 + 边栏角标同步（含过滤态重画；不在主列表则 no-op）
            main._sync_after_read(item)

    def _update_row_mark(self, item: FeedItem) -> None:
        """单行圆点就地重算（按 (source, id) 定位行，理由见 MainScreen 同名方法）。"""
        idx = next(
            (i for i, it in enumerate(self.items) if it.source == item.source and it.id == item.id),
            None,
        )
        if idx is None:
            return
        view = self.query_one("#fav-list", ListView)
        if idx >= len(view.children):
            return
        row = view.children[idx]
        mark = self._row_mark(item)
        row.query_one(".item-mark", Label).update(mark)
        row.set_class(not mark, "row-read")  # 标题置灰随圆点同进退

    # ---- 键位动作 ----

    def action_nav_enter(self) -> None:
        self.query_one("#fav-list", ListView).action_select_cursor()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_open_browser(self) -> None:
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self._mark_read(item)
        webbrowser.open(item.url)

    def action_unfavorite(self) -> None:
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self._tui.state.toggle_fav(item)
        feedback.info(self, t("fav.toast_removed"))
        self.run_worker(self._rebuild(), exclusive=True)

    def action_copy_link(self) -> None:
        """Ctrl+Y：复制当前收藏的链接（快照里的 url，不依赖源在线）。"""
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self.app.copy_to_clipboard(item.url)
        feedback.info(self, t("main.toast_copied"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "fav-list":
            return
        item = self._current()
        if item is None:
            return
        self._mark_read(item)
        source = self._source_of(item)
        if source.supports_detail and item.kind in source.detail_kinds:
            self.app.push_screen(DetailScreen(item, source, self.step_readable))
        else:
            webbrowser.open(item.url)

    def step_readable(self, delta: int) -> FeedItem | None:
        """详情屏 ←/→ 的供给端（镜像主屏 step_readable_item）：沿方向推进到下一条
        可进终端详情的收藏，同步选中并计已读；没有可读目标时返回 None。"""
        view = self.query_one("#fav-list", ListView)
        if not self.items or view.index is None:
            return None
        for idx in range(view.index + delta, len(self.items) if delta > 0 else -1, delta):
            it = self.items[idx]
            src = self._source_of(it)
            if src.supports_detail and it.kind in src.detail_kinds:
                view.index = idx
                self._mark_read(it)
                return it
        return None
