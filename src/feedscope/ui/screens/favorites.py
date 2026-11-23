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

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Label, ListItem, ListView

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources
from feedscope.core.source import UNKNOWN_SOURCE, Source
from feedscope.ui import Bindings, feedback, tui_of
from feedscope.ui.coalesce import CoalescedScheduler
from feedscope.ui.dialogs import ask_title_filter
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.launch import copy_link, open_link
from feedscope.ui.layout import (
    META_COL_SPARE,
    RESIZE_DEBOUNCE,
    list_title_width,
    time_col_width,
)
from feedscope.ui.list_render import (
    LIST_ROW_CSS,
    MARK_UNREAD,
    build_item,
    cross_source_tag,
    cross_source_widths,
    jump_unread,
    update_row_mark,
)
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.help import push_help

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环


class _FavListView(ListView):
    """Enter 归屏分派（show=False：页脚不露控件级键名，与主列表同一条纪律；
    描述给 F1 帮助页——控件要随 action_help 传进 push_help 才被扫到）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("enter", "screen.nav_enter", "bindings.open_item", show=False),
    ]


_PAGE = 100  # 分页步长：ListView 没有虚拟化，几千条收藏一次性挂载会冻结秒级
             #（主列表 SliceFeedLoader 同一动机；数据全在本地，Ctrl+N 只是渲染放量）


class FavoritesScreen(Screen[None]):
    """收藏列表：Esc 返回，Enter 进详情/开浏览器，Ctrl+F 取消收藏。"""

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
        self._sources: dict[str, Source] = {}  # 全注册表（含已停用源），挂载时解析一次
        self.items: list[FeedItem] = []  # 当前行序（收藏时间新→旧，行序 = items 序）
        # / 标题过滤（会话级，同主屏）：None = 未过滤；行集走 _shown()，
        # self.items 保持全量（快照/取消收藏/同步主屏都不受影响）
        self._filter: str | None = None
        self._shown_limit = _PAGE  # 已挂载行数上限（Ctrl+N 逐页放量；重建时回 _PAGE）
        self._tag_w = 0  # 源名列宽（重建时算，Ctrl+N 追加行复用）
        self._time_w = 0  # 时间列宽（同上）
        self._score_width = 1 + META_COL_SPARE
        self._prev_sub_title: str | None = None  # 退屏恢复（同 ReadingScreen）
        self._last_width = 0
        self._resize_timer: Timer | None = None
        # 整屏重建的合并式调度器（见 _rerender；机理说明在 ui.coalesce）
        self._rebuild_sched = CoalescedScheduler(self, self._rebuild)

    @property
    def _tui(self) -> FeedScopeApp:
        """self.app 的收窄视图（cast 收口在 ui.tui_of），同主屏家族。"""
        return tui_of(self)

    def compose(self) -> ComposeResult:
        yield Header()
        yield _FavListView(id="fav-list")
        # markup=False：空态提示会拼入用户过滤词，纯文本出口不做标记解析
        yield Label("", id="fav-empty", markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#fav-list", _FavListView))
        self._prev_sub_title = self.app.sub_title
        self._sources = {s.source_id: s for s in all_sources()}
        await self._rebuild()
        self.query_one("#fav-list", ListView).focus()

    def on_screen_resume(self) -> None:
        # 详情屏退回：已读圆点 / 在详情里取消的收藏都要反映（见模块头）。
        # 走 _rerender 的 exclusive worker 而非直 await：与 / 过滤等屏内触发
        # 共用一条串行道，两路 clear/extend 交错会把同一行挂两遍
        self._rerender()

    async def on_unmount(self) -> None:
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

    def _rerender(self) -> None:
        """请求一次整屏重建（resize / resume / 过滤变化共用入口）。

        合并式调度收口在 ui.coalesce（同主屏边栏刷新；在途时只标脏不并跑，
        跑完见脏自动再来一轮——两个重建的 clear/extend 交错会把同一行挂两遍）。"""
        self._rebuild_sched.trigger()

    # ---- 列表 ----

    def _source_of(self, it: FeedItem) -> Source:
        return self._sources.get(it.source, UNKNOWN_SOURCE)  # 已下架的源用占位源

    def _tag_of(self, it: FeedItem) -> str:
        """源名列文本（规则见 list_render.cross_source_tag，搜索结果共用）。"""
        return cross_source_tag(it, self._sources)

    def _widths_of(self) -> tuple[int, int]:
        """(源名列宽, 元信息列宽)：按各条目自己的源算（list_render.cross_source_widths）。"""
        return cross_source_widths(self.items, self._sources)

    def _shown(self) -> list[FeedItem]:
        """当前渲染的行集（/ 过滤态 = 标题匹配子集）；行↔条目映射的唯一入口。"""
        if not self._filter:
            return self.items
        q = self._filter
        return [it for it in self.items if q in it.title.lower()]

    def _mounted(self) -> list[FeedItem]:
        """已挂载进列表的行集 = _shown() 的前缀（分页逐步放量）。"""
        return self._shown()[: self._shown_limit]

    def _build_rows(self, items: list[FeedItem]) -> list[ListItem]:
        width = list_title_width(
            self.query_one("#fav-list", ListView).region.width,
            self._score_width,
            self._tag_w,
            self._time_w,
        )
        return [
            build_item(
                it,
                width,
                self._score_width,
                self._source_of(it),
                self._row_mark(it),
                tag=self._tag_of(it),
                tag_width=self._tag_w,
                time_width=self._time_w,
            )
            for it in items
        ]

    async def _rebuild(self) -> None:
        """按当前收藏重建列表（挂载 / 每次 ScreenResume / resize 后调用；保留选中位）。

        只挂前 _PAGE 条（防冻结，见 _PAGE），其余 Ctrl+N 放量；列宽按全量
        算（追加行不用重排）。
        """
        view = self.query_one("#fav-list", ListView)
        idx = view.index
        pairs = self._tui.state.favorites()
        self.items = [it for _, it in pairs]
        shown = self._shown()
        self._shown_limit = _PAGE
        mounted = shown[:_PAGE]
        await view.clear()
        empty = self.query_one("#fav-empty", Label)
        if mounted:
            self._tag_w, self._score_width = self._widths_of()
            self._time_w = time_col_width(self.items)  # 快照里带时间的条目成列
            await view.extend(self._build_rows(mounted))
            view.index = 0 if idx is None else min(idx, len(mounted) - 1)
            empty.display = False
        elif self.items:
            # 空态提示是纯文本出口（Label 构造处 markup=False）：过滤词原样给
            empty.update(t("fav.empty_filter", q=self._filter or ""))
            empty.display = True  # 有收藏但被过滤光：指路去关过滤
        else:
            empty.update(t("fav.empty"))
            empty.display = True
        self._update_status(len(shown), len(mounted))

    def _update_status(self, total: int, mounted: int) -> None:
        if self._filter:
            # Header 副标题不做标记解析（实测）：过滤词原样给，转义反而会露出反斜杠
            self.app.sub_title = t("fav.status_filtered", n=total, q=self._filter)
        elif mounted < total:
            self.app.sub_title = t("fav.status_paged", n=total, m=mounted)
        else:
            self.app.sub_title = t("fav.status_count", n=total)

    def action_more(self) -> None:
        """Ctrl+N：挂载下一页（与主屏加载更多同键同义；数据全在本地，纯渲染放量）。"""
        shown = self._shown()
        if self._shown_limit >= len(shown):
            feedback.info(self, t("main.toast_no_more"))
            return
        prev = self._shown_limit
        self._shown_limit = min(prev + _PAGE, len(shown))
        batch = shown[prev : self._shown_limit]
        self.run_worker(self._mount_batch(batch))

    async def _mount_batch(self, batch: list[FeedItem]) -> None:
        """追加一页行（单次挂载 = 单次布局）；期间整屏重建了就放弃迟到批。"""
        view = self.query_one("#fav-list", ListView)
        if not batch:
            return
        await view.extend(self._build_rows(batch))
        self._update_status(len(self._shown()), len(view.children))
        self._hide_empty_if_filled()

    def _hide_empty_if_filled(self) -> None:
        if len(self.query_one("#fav-list", ListView).children):
            self.query_one("#fav-empty", Label).display = False

    # ---- 已读（与主屏同语义；标记后同步主屏同一行）----

    def _row_mark(self, it: FeedItem) -> str:
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _current(self) -> FeedItem | None:
        view = self.query_one("#fav-list", ListView)
        mounted = self._mounted()
        if not mounted or view.index is None or view.index >= len(mounted):
            return None
        return mounted[view.index]

    def _mark_read(self, item: FeedItem) -> None:
        self._tui.state.mark_read(item.source, item.id)
        self._update_row_mark(item)
        if (main := self._tui.main) is not None:
            # 主屏同一行圆点 + 边栏角标同步（含过滤态重画；不在主列表则 no-op）
            main._sync_after_read(item)

    def _update_row_mark(self, item: FeedItem) -> None:
        """单行圆点就地重算（定位与置灰收口在 list_render.update_row_mark，
        主屏同名方法同一条路；行集给已挂载的页——未挂载的页没有行可寻）。"""
        update_row_mark(
            self.query_one("#fav-list", ListView), self._mounted(), item, self._row_mark(item)
        )

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
        open_link(self, item.url)

    def action_unfavorite(self) -> None:
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self._tui.state.toggle_fav(item)
        feedback.info(self, t("fav.toast_removed"))
        self._rerender()

    def action_toggle_read(self) -> None:
        """Ctrl+R：当前收藏切回未读 / 标已读（主列表同键同义，主屏同一行同步）。"""
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        read = self._tui.state.toggle_read(item.source, item.id)
        feedback.info(self, t("main.toast_marked_read" if read else "main.toast_marked_unread"))
        self._update_row_mark(item)
        if (main := self._tui.main) is not None:
            main._sync_after_read(item)

    def action_next_unread(self) -> None:
        self._jump_unread(+1)

    def action_prev_unread(self) -> None:
        self._jump_unread(-1)

    def _jump_unread(self, delta: int) -> None:
        """环扫到下一条未读的收藏（本体在 list_render.jump_unread，与主屏共用）。"""
        state = self._tui.state
        if not jump_unread(
            self.query_one("#fav-list", ListView),
            self._mounted(),
            lambda it: state.is_read(it.source, it.id),
            delta,
        ):
            feedback.info(self, t("main.no_unread"))

    def action_copy_link(self) -> None:
        """Ctrl+Y：复制当前收藏的链接（快照里的 url，不依赖源在线）。"""
        item = self._current()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        copy_link(self, item.url)

    def action_title_filter(self) -> None:
        """/：标题过滤（会话级，同主屏语义）：Enter 设词/空提交清除/Esc 不变。"""
        self.run_worker(self._title_filter_flow(), exclusive=True)

    async def _title_filter_flow(self) -> None:
        new = await ask_title_filter(self, self._filter)  # 问答与 toast 收口在 dialogs
        if new == self._filter:
            return
        self._filter = new
        # 弹层关闭还会触发 on_screen_resume 重建：都走 _rerender 的合并式调度
        # 串行，两路 clear/extend 交错会把同一行挂两遍（sidebar 竞态同族）
        self._rerender()

    def action_help(self) -> None:
        """F1：键位帮助（全局 + 本屏，从声明自动生成；入口收口在 push_help，
        列表控件随行——Enter 的声明在控件上，缺了它帮助页少一行）。"""
        push_help(self, self.query_one("#fav-list", _FavListView))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "fav-list":
            return
        item = self._current()
        if item is None:
            return
        self._mark_read(item)
        source = self._source_of(item)
        # 判据收口在 Source.opens_detail（与主屏共用）；收藏时存过详情的条目即使源
        # 已打不开它（源下架 / 类型不再进详情），也进详情屏回退显示存档
        if source.opens_detail(item) or self._tui.state.archive_of(item) is not None:
            # 跨源列表：相邻条目可能来自别的源，详情屏按条目重新取源
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
            open_link(self, item.url)

    def step_readable(self, delta: int) -> FeedItem | None:
        """详情屏 ←/→ 的供给端（镜像主屏 step_readable_item）：沿方向推进到下一条
        可进终端详情的收藏（沿已挂载行集——未挂载的页没有行可选），同步选中
        并计已读；没有可读目标时返回 None。"""
        return self._step(delta, unread_only=False)

    def step_unread(self, delta: int) -> FeedItem | None:
        """详情屏 ]/[ 的供给端：只停在未读的可读收藏上。"""
        return self._step(delta, unread_only=True)

    def _step(self, delta: int, *, unread_only: bool) -> FeedItem | None:
        view = self.query_one("#fav-list", ListView)
        mounted = self._mounted()
        if not mounted or view.index is None:
            return None
        for idx in range(view.index + delta, len(mounted) if delta > 0 else -1, delta):
            it = mounted[idx]
            if not self._source_of(it).opens_detail(it):
                continue
            if unread_only and self._tui.state.is_read(it.source, it.id):
                continue
            view.index = idx
            self._mark_read(it)
            return it
        return None
