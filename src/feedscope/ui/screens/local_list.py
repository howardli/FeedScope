"""本地列表屏的共享骨架：数据全在本地内存的跨源条目列表（收藏屏 / 搜索屏）。

两张屏是同一台机器——分页挂载、改宽防抖重排、退回时重建、行首已读圆点、
Enter 进详情或跳浏览器、←/→ 换篇与 ]/[ 换未读、Ctrl+N 放量、/ 标题过滤。
此前是两份平行实现，并已开始漂移（收藏屏标已读后就地刷圆点、搜索屏要等
退回才刷；重建一个走合并式调度、一个走 exclusive worker）。机械件全部收在
这里，子类只剩「数据从哪来、空态与状态行写什么、屏上还有什么控件」：

    LIST_ID / EMPTY_ID              列表与空态提示控件的 id（compose 里要有）
    _rows()                         全量行集原料（收藏时间序 / 搜索命中序）
    _reload()                       重建前重取数据（收藏屏每次读快照；默认无事可做）
    _empty_text() / _status_text()  两段文案
    _data_ready()                   数据就位才重建（搜索屏的条目表是异步读的）

行↔条目的唯一映射：_shown()（全量，或 / 过滤后的子集）与 _mounted()（_shown
的已挂载前缀）。寻址一律走它们，别处不留第二份行集。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, ClassVar

from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Label, ListItem, ListView

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources
from feedscope.core.source import UNKNOWN_SOURCE, Source
from feedscope.ui import feedback, tui_of
from feedscope.ui.coalesce import CoalescedScheduler
from feedscope.ui.dialogs import ask_title_filter
from feedscope.ui.launch import copy_link, open_link
from feedscope.ui.layout import (
    META_COL_SPARE,
    RESIZE_DEBOUNCE,
    cross_source_tag,
    cross_source_widths,
    list_title_width,
    time_col_width,
)
from feedscope.ui.list_render import (
    MARK_UNREAD,
    build_item,
    jump_unread,
    step_to,
    update_row_mark,
)
from feedscope.ui.screens.detail import DetailScreen

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环


class LocalListScreen(Screen[None]):
    """本地列表屏基类：分页挂载 / 改宽重排 / 已读同步 / 换篇与未读导航的唯一实现。"""

    LIST_ID: ClassVar[str] = ""  # 列表控件 id
    EMPTY_ID: ClassVar[str] = ""  # 空态提示控件 id（构造处 markup=False：会拼入用户过滤词）
    PAGE: ClassVar[int] = 100  # 分页步长：ListView 没有虚拟化，几千条一次性挂载会冻结秒级

    def __init__(self) -> None:
        super().__init__()
        self._sources: dict[str, Source] = {}  # 全注册表（含已停用源），挂载时解析一次
        self._shown_limit = self.PAGE  # 已挂载行数上限（Ctrl+N 逐页放量；重建时回 PAGE）
        self._tag_w = 0  # 源名列宽（重建时算，Ctrl+N 追加行复用）
        self._time_w = 0  # 时间列宽（同上）
        self._score_width = 1 + META_COL_SPARE  # 元信息列宽（同上；首渲染前是占位值）
        # / 标题过滤（会话级，同主屏）：None = 未过滤。行集走 _shown()，
        # _rows() 保持全量（快照 / 计数 / 同步主屏都不受影响）
        self._filter: str | None = None
        self._prev_sub_title: str | None = None  # 退屏恢复（同 ReadingScreen）
        self._last_width = 0
        self._resize_timer: Timer | None = None
        # 整屏重建的合并式调度器（见 _rerender；机理说明在 ui.coalesce）。
        # 独立组：不与 / 过滤问答等同屏的其他 worker 互相取消
        self._rebuild_sched = CoalescedScheduler(
            self, self._rebuild, group="rebuild", alive=lambda: self.is_running
        )

    @property
    def _tui(self) -> FeedScopeApp:
        """self.app 的收窄视图（cast 收口在 ui.tui_of），同主屏家族。"""
        return tui_of(self)

    # ---- 子类提供的差异 ----

    @abstractmethod
    def _rows(self) -> list[FeedItem]:
        """全量行集原料（行序即列表序）。"""

    async def _reload(self) -> None:
        """每次重建前重取数据（数据会变的屏覆写；默认无事可做）。"""

    @abstractmethod
    def _empty_text(self) -> str:
        """空态提示文案（纯文本出口）。"""

    @abstractmethod
    def _status_text(self, total: int, mounted: int) -> str:
        """Header 副标题（状态行）：total = 行集总数，mounted = 已挂载条数。"""

    def _data_ready(self) -> bool:
        """数据就位才值得重建；异步装数据的屏覆写。"""
        return True

    def _remember_context(self) -> None:
        """挂载时：解析全注册表（收藏 / 结果条目的源可能已被 F2 停用——停用只是不出现在
        主屏，注册表仍在），记下副标题（退屏恢复）。"""
        self._sources = {s.source_id: s for s in all_sources()}
        self._prev_sub_title = self.app.sub_title

    # ---- 行集（行↔条目的唯一映射；寻址一律走这两个）----

    def _shown(self) -> list[FeedItem]:
        """当前渲染的行集（/ 过滤态 = 标题匹配子集）。"""
        rows = self._rows()
        if not self._filter:
            return rows
        return [it for it in rows if self._filter in it.title.lower()]

    def _mounted(self) -> list[FeedItem]:
        """已挂载进列表的行集 = _shown() 的前缀（分页逐步放量）。"""
        return self._shown()[: self._shown_limit]

    def _list_view(self) -> ListView:
        return self.query_one(f"#{self.LIST_ID}", ListView)

    def _empty_label(self) -> Label:
        return self.query_one(f"#{self.EMPTY_ID}", Label)

    def _source_of(self, it: FeedItem) -> Source:
        """条目自己的源；注册表里已不存在的（源下架）用占位源。"""
        return self._sources.get(it.source, UNKNOWN_SOURCE)

    def _current(self) -> FeedItem | None:
        view = self._list_view()
        mounted = self._mounted()
        if not mounted or view.index is None or view.index >= len(mounted):
            return None
        return mounted[view.index]

    # ---- 渲染 ----

    def _build_rows(self, items: list[FeedItem]) -> list[ListItem]:
        width = list_title_width(
            self._list_view().region.width,
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
                tag=cross_source_tag(it, self._sources),
                tag_width=self._tag_w,
                time_width=self._time_w,
            )
            for it in items
        ]

    def _rerender(self) -> None:
        """请求一次整屏重建（resize / resume / 过滤变化共用入口）。

        合并式调度收口在 ui.coalesce（在途时只标脏不并跑，跑完见脏自动再来
        一轮——两个重建的 clear/extend 交错会把同一行挂两遍）。"""
        if not self._data_ready():
            self._update_status()
            return
        self._rebuild_sched.trigger()

    async def _rebuild(self) -> None:
        """按当前数据重建列表（挂载 / ScreenResume / resize 后；保留选中位）。

        只挂第一页（防冻结，见 PAGE），其余 Ctrl+N 放量；列宽按全量行集算
        （追加行不用重排）。"""
        await self._reload()
        view = self._list_view()
        idx = view.index
        self._shown_limit = self.PAGE
        mounted = self._mounted()
        await view.clear()
        empty = self._empty_label()
        if mounted:
            self._tag_w, self._score_width = cross_source_widths(self._rows(), self._sources)
            self._time_w = time_col_width(self._rows())
            await view.extend(self._build_rows(mounted))  # 单次挂载 = 单次布局
            view.index = 0 if idx is None else min(idx, len(mounted) - 1)
            empty.display = False
        else:
            view.index = None
            empty.update(self._empty_text())  # 空态提示是纯文本出口（Label 构造处 markup=False）
            empty.display = True
        self._update_status()

    def _update_status(self) -> None:
        self.app.sub_title = self._status_text(len(self._shown()), len(self._mounted()))

    def action_more(self) -> None:
        """Ctrl+N：挂载下一页（与主屏加载更多同键同义；数据全在本地，纯渲染放量）。"""
        shown = self._shown()
        if self._shown_limit >= len(shown):
            feedback.info(self, t("main.toast_no_more"))
            return
        prev = self._shown_limit
        self._shown_limit = min(prev + self.PAGE, len(shown))
        self.run_worker(self._mount_batch(shown[prev : self._shown_limit]))

    async def _mount_batch(self, batch: list[FeedItem]) -> None:
        """追加一页行（单次挂载 = 单次布局）；期间整屏重建了就放弃迟到批。"""
        view = self._list_view()
        if not batch:
            return
        await view.extend(self._build_rows(batch))
        self._update_status()
        if view.children:
            self._empty_label().display = False

    # ---- 生命周期 ----

    def on_screen_resume(self) -> None:
        # 详情屏退回：已读圆点 / 在详情里取消的收藏都要反映。走 _rerender 的
        # 合并式调度而非直 await：与 / 过滤等屏内触发共用一条串行道
        self._rerender()

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

    # ---- 已读（与主屏同语义；标记后同步主屏同一行）----

    def _row_mark(self, it: FeedItem) -> str:
        """行首圆点二态：未读 ●、已读空（未读是持久态，只在打开时消失）。"""
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _mark_read(self, item: FeedItem) -> None:
        """打开即已读（Enter / Ctrl+O / ←→ 换篇）：本屏行圆点就地刷新，并同步主屏
        同一条目的行圆点与边栏角标（主屏无 resume 重建，靠这里代更新）。"""
        self._tui.state.mark_read(item.source, item.id)
        self._update_row_mark(item)
        if (main := self._tui.main) is not None:
            main._sync_after_read(item)  # 含过滤态重画；不在主列表则 no-op

    def _update_row_mark(self, item: FeedItem) -> None:
        """单行圆点就地重算（定位与置灰收口在 list_render.update_row_mark，主屏同名
        方法同一条路；行集给已挂载的页——未挂载的页没有行可寻）。"""
        update_row_mark(self._list_view(), self._mounted(), item, self._row_mark(item))

    # ---- 键位动作 ----

    def action_nav_enter(self) -> None:
        self._list_view().action_select_cursor()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_copy_link(self) -> None:
        """Ctrl+Y：复制当前条目的链接（本地快照里的 url，不依赖源在线）。"""
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

    def action_toggle_read(self) -> None:
        """Ctrl+R：当前条目切回未读 / 标已读（主列表同键同义，主屏同一行同步）。"""
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
        """环扫到下一条未读（本体在 list_render.jump_unread，与主屏共用）。"""
        state = self._tui.state
        if not jump_unread(
            self._list_view(),
            self._mounted(),
            lambda it: state.is_read(it.source, it.id),
            delta,
        ):
            feedback.info(self, t("main.no_unread"))

    def action_title_filter(self) -> None:
        """/：标题过滤（会话级，同主屏语义）：Enter 设词/空提交清除/Esc 不变。"""
        self.run_worker(self._title_filter_flow(), exclusive=True)

    async def _title_filter_flow(self) -> None:
        new = await ask_title_filter(self, self._filter)  # 问答与 toast 收口在 dialogs
        if new == self._filter:
            return
        self._filter = new
        # 弹层关闭还会触发 on_screen_resume 重建：都走 _rerender 的合并式调度
        # 串行，两路 clear/extend 交错会把同一行挂两遍
        self._rerender()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != self.LIST_ID:
            return
        item = self._current()
        if item is not None:
            self._open_item(item)

    def _open_item(self, item: FeedItem) -> None:
        """Enter：能进终端详情就进（收藏时存过详情的条目，即使源已打不开它——源下架 /
        类型不再进详情——也进详情屏回退显示存档），否则跳浏览器。"""
        self._mark_read(item)  # 进详情与浏览器兜底都是「打开」
        source = self._source_of(item)
        # 判据收口在 Source.opens_detail（与主屏共用）
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

    # ---- 详情屏 ←/→ 与 ]/[ 的供给端 ----

    def step_readable(self, delta: int) -> FeedItem | None:
        """详情屏 ←/→ 的供给端（镜像主屏 step_readable_item）：沿已挂载行集推进到
        下一条可进终端详情的条目（未挂载的页没有行可选），同步选中并计已读；
        没有可读目标时返回 None。"""
        return self._step(delta, unread_only=False)

    def step_unread(self, delta: int) -> FeedItem | None:
        """详情屏 ]/[ 的供给端：只停在未读的可读条目上。"""
        return self._step(delta, unread_only=True)

    def _step(self, delta: int, *, unread_only: bool) -> FeedItem | None:
        target = step_to(
            self._list_view(),
            self._mounted(),
            delta,
            readable=lambda it: self._source_of(it).opens_detail(it),
            is_read=lambda it: self._tui.state.is_read(it.source, it.id),
            unread_only=unread_only,
        )
        if target is not None:
            self._mark_read(target)
        return target
