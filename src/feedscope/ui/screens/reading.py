"""全屏阅读屏的共享骨架，两层收口：

- ReadingScreen（所有阅读屏）：Header + 正文滚动容器 + Footer 的屏级机械件
  ——Esc 返回、resize 防抖重排、尾行 hint 生命周期、副标题保存恢复、
  卸载清理（停定时器 + 关 loader）。阅读屏家族的公共样式也挂在这层
  （DEFAULT_CSS 沿 MRO 合并，子类 CSS 只写自己的增量，不再字符串拼接）。
- PagedReadingScreen（loader 驱动的分页阅读屏，detail.py / answer.py）：
  「首批 → Ctrl+N 增量」的动线机械件——Ctrl+N 分派（首载失败态转重试）、
  load_more 的陈旧 loader 守卫与尾行状态机（到头 → EOF 标记，没到头 →
  加载预告）、←/→ 换目标的取消在途 / 换出旧 loader / 清屏重载。
  DetailLoader 与 CommentLoader 同形（load_more/done/close），问题屏与
  回答屏各自的翻页机械件在这里唯一实现。

语义样式类（子类直接用，规则只此一份）：
- .read-title/.read-meta/.read-main：标题行 / 元信息行 / 主正文
- .floor-head/.floor-body：楼层头 / 楼层正文（含 .sub 缩进变体）
- .tail-line：尾部状态行（加载预告 / EOF 标记 / 空态），细分
  .tail-hint（Ctrl+N 预告）/ .tail-eof（（没有更多…）·（完））
"""

from __future__ import annotations

import dataclasses
import webbrowser
from abc import abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from rich.markup import escape
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Label, Static

from feedscope.core.models import FeedItem
from feedscope.core.source import CommentLoader, DetailLoader, Source
from feedscope.ui import Bindings, feedback
from feedscope.ui.layout import RESIZE_DEBOUNCE, md_body_width


def divider() -> Static:
    """全宽分隔线（dim 灰）：楼层/区块之间的边界，超宽由容器裁掉。"""
    return Static("[dim]" + "─" * 200 + "[/dim]", classes="answer-divider")


def dim_line(text: str, **kwargs) -> Static:
    """dim 灰的一行尾注（EOF 标记 / 空态等同族）：markup 只写一次。"""
    return Static(f"[dim]{text}[/dim]", **kwargs)


def floor_head(
    lead: str, author: str, fields: Sequence[str], *, classes: str, name: str | None = None
) -> Static:
    """楼层头部件：lead（如「回答 3」；空 = 无编号的子回复头）+ 署名 + dim 字段串。

    问题屏的楼层头 / 回答屏的评论头与子回复头三处同构，收进阅读屏家族
    共享（与 divider / dim_line 同层）；fields 里的空串自动略过。
    """
    parts: list[str] = []
    if lead:
        parts.append(f"[b]{lead}[/b]")
    parts.append(f"[b]{escape(author)}[/b]")
    parts.extend(f"[dim]{escape(x)}[/dim]" for x in fields if x)
    return Static("  ".join(parts), classes=classes, name=name)


# 正文统一格式（所有源同形）：Markdown 标题抹平成段落样式——Textual 缺省
# 给 H1 居中加粗、H2 下划线、H3 加粗、H5/H6 甚至 ansi_cyan，且 Header 基类
# color $text（auto 87%，比正文亮）+ margin 2 0 1 0；统一为正文的前景色/
# 样式/对齐/边距（App 基类 color: $foreground，段落即继承它），标题不再有
# 专属版式。蓝色与下划线只留给链接（theme.variables）。
# 这条必须进各屏的 CSS 而非 DEFAULT_CSS：要压过 Markdown 组件自带的
# DEFAULT_CSS（屏级 DEFAULT_CSS 压不过 widget 自己的，CSS 才压得过），
# 两屏以常量拼接共享。
READING_CSS = """
MarkdownH1, MarkdownH2, MarkdownH3, MarkdownH4, MarkdownH5, MarkdownH6 {
    color: $foreground;
    background: transparent;
    text-style: none;
    content-align: left middle;
    margin: 0 0 1 0;
}
"""


class ReadingScreen(Screen[None]):
    """阅读屏基类：resize 防抖整屏重排 + hint 生命周期 + 副标题/定时器/ loader 清理。

    子类契约（全部抽象）：
    - _body_scroll：正文滚动容器（重排/挂载的目标）
    - _render_doc：整屏渲染（首批与重排共用同一条路径）
    - _hint_widget：尾行动线预告
    - _reset_render_state：换目标/重排前清渲染态（楼层序号等子类字段）
    - _close_loader：卸载时关闭持有的 loader（幂等，出错自己吞）
    子类自管 _rendered 的置位时机（首批渲染完成才吃 resize 重排）。
    """

    BINDINGS: ClassVar[Bindings] = [Binding("escape", "back", "返回")]

    # 家族共享样式挂 DEFAULT_CSS（沿 MRO 与子类 CSS 合并）：子类 CSS 只写
    # 增量，原先五对逐字相同的规则（#detail-title ↔ #answer-title 等）归位
    # 成语义类。例外见 READING_CSS（要压过 widget 自带 DEFAULT_CSS 的规则）
    DEFAULT_CSS = """
    /* 楼层/区块分隔线（divider()）：两屏共用，超宽由容器裁掉 */
    .answer-divider { width: 1fr; height: 1; }

    /* 标题行（加粗 + ascii 边框）与元信息行 */
    .read-title { text-style: bold; padding: 1 2; border: ascii; width: 1fr; text-wrap: wrap; }
    .read-meta { padding: 0 2; }
    /* 主正文（问题描述 / 回答全文） */
    .read-main { padding: 0 2; }
    /* 楼层头（窄终端长头折行不裁剪）与楼层正文 */
    .floor-head { padding: 1 2 0 2; text-wrap: wrap; }
    .floor-body { padding: 0 2; }
    /* 子回复的缩进变体（回答屏的楼中楼） */
    .floor-head.sub, .floor-body.sub { margin-left: 3; }
    /* 尾部状态行（加载预告 / EOF 标记 / 空态） */
    .tail-line { padding: 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._prev_sub_title: str | None = None  # 进屏前的 Header 副标题（退屏恢复）
        self._rendered = False  # 首批已上屏；此后 resize 才需要重排
        self._rendered_width = 0  # 渲染时实际使用的容器宽（首布局前渲染 = 0，见 _render_done）
        self._last_width = 0
        self._resize_timer: Timer | None = None
        self._hint: Static | None = None

    @abstractmethod
    def _body_scroll(self) -> VerticalScroll:
        """正文滚动容器。"""

    @abstractmethod
    async def _render_doc(self) -> None:
        """整屏渲染（首批与 resize 重排共用；单次 mount = 单次布局）。"""

    @abstractmethod
    def _hint_widget(self) -> Static:
        """尾行动线预告（只留 Footer 没有的语义增量）。"""

    @abstractmethod
    def _reset_render_state(self) -> None:
        """换目标/重排前清渲染态（楼层序号归零等）。"""

    @abstractmethod
    async def _close_loader(self) -> None:
        """卸载时关掉持有的 loader（幂等，出错自己吞）。"""

    async def _mount_body(self) -> None:
        """on_mount 的屏内钩子：聚焦正文容器 + 触发首载（子类覆写，先调 focus）。"""
        self._body_scroll().focus()

    async def on_mount(self) -> None:
        self._prev_sub_title = self.app.sub_title
        await self._mount_body()

    # ---- resize 重排：拖拽改宽连发 Resize，稳定后整屏重排（不重新抓取）----

    def _render_done(self) -> None:
        """首批上屏完成（子类在 _render_doc 后调用）：记录渲染实际使用的容器宽。

        折行宽取自当时的 region.width——若渲染发生在首布局之前（零请求的
        详情/回答屏会这样），宽度是 0、正文被钳到最小宽挤压在左侧；resize
        重排的触发条件与这里记录的宽度比对（而非跳过「第一次」），这种
        渲染后的首个 Resize 就会整屏重排自愈，不用用户手动改窗口救。
        """
        self._rendered = True
        self._rendered_width = self._body_scroll().region.width

    def on_resize(self, event: Resize) -> None:
        if event.size.width in (self._last_width, self._rendered_width):
            self._last_width = event.size.width
            return  # 宽度没变 / 与渲染时同宽：无需重排（含首布局——渲染即按此宽）
        self._last_width = event.size.width
        if not self._rendered:
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self._rerender)

    def _rerender(self) -> None:
        if not self._rendered:
            return
        scroll_y = self._body_scroll().scroll_y
        # 同一屏同时只跑一次重排：连续 resize 的后一次要取代前一次，避免两套
        # remove_children/mount 交错出重复楼层
        self.run_worker(self._rerender_body(scroll_y), group="rerender", exclusive=True)

    async def _rerender_body(self, scroll_y: float) -> None:
        body = self._body_scroll()
        await body.remove_children()
        self._hint = None
        self._reset_render_state()
        try:
            await self._render_doc()
        except Exception:
            return  # 屏幕正在被关闭：放弃重排
        self._render_done()  # 重排后的折行宽才是当前有效宽
        # 重排后恢复阅读位置（内容高度要等一次刷新才更新，故延后滚动）
        body.call_after_refresh(body.scroll_to, y=scroll_y, animate=False)

    # ---- 尾行 hint 的挂载/摘除（随楼层增删反复挂载，class 不用 id 防撞重）----

    async def _mount_hint(self) -> None:
        self._remove_hint()
        self._hint = self._hint_widget()
        await self._body_scroll().mount(self._hint)

    def _remove_hint(self) -> None:
        if self._hint is not None:
            w, self._hint = self._hint, None
            try:
                w.remove()  # 同步发起摘除；重复挂载的防护靠 class 不用 id
            except Exception:
                pass

    # ---- 键位与清理 ----

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
        if self._prev_sub_title is not None:
            self.app.sub_title = self._prev_sub_title
        await self._close_loader()


class PagedReadingScreen(ReadingScreen):
    """分页阅读屏：Ctrl+N 翻页动线 / 尾行状态机 / ←/→ 换目标重载的唯一实现。

    子类注入（全部抽象或声明）：
    - _worker_group：本屏取数 worker 的组名（首载/重试/翻页同组互斥；换目标
      与卸载时整组取消，重排组 rerender 一并取消——在途重排不能再拿旧 doc 渲）
    - _mount_batch(new)：把一批新楼层渲染上屏（楼层部件是屏差异）
    - _retry()：首载失败后的原地重试（摘失败行重走首载）
    - _next_target / _no_target_text / _adopt_target：←/→ 换目标三件套
      （拿相邻目标、边界反馈文案、收下目标重走首载）

    契约：self.loader 是本屏的翻页 loader（None = 首载失败或在途，Ctrl+N
    转重试）；_exhausted 只由本层的尾行状态机与子类首载置位。楼层词 _unit
    与词汇表键位文案来自源声明（Source 的 *_unit_label）。
    """

    _worker_group: ClassVar[str] = "page"
    source: Source  # 源实例（失败文案与能力判定）
    item: FeedItem  # 正在读的条目（Ctrl+O 与屏间传递）

    loader: DetailLoader | CommentLoader | None = None
    _exhausted: bool = False  # 翻页对象已到头，Ctrl+N 不再触发加载

    # ---- 翻页（Ctrl+N：加载更多 / 失败态重试）----

    def action_more(self) -> None:
        if self.loader is None:
            # 首载失败（或还在途）时 Ctrl+N = 原地重试：比起 Esc 退出再进，动线不断
            self.run_worker(self._retry(), group=self._worker_group, exclusive=True)
            return
        if self._exhausted:
            # 常驻尾行之外再给瞬时反馈：按键不无声
            feedback.info(self.app, f"没有更多{self._unit}了")
            return
        self.run_worker(self._load_more(), group=self._worker_group, exclusive=True)

    async def _load_more(self) -> None:
        body = self._body_scroll()
        loader = self.loader
        if loader is None:
            return
        self._remove_hint()
        loading = Label("[dim]加载更多…[/dim]", classes="tail-line more-loading")
        await body.mount(loading)
        try:
            new = await loader.load_more()
        except Exception as exc:
            # 不再静默：失败要告诉用户（否则按 Ctrl+N 无反馈，误以为按键失灵）
            feedback.fail(self.app, self.source, exc, action="加载更多失败")
            new = []
        if loader is not self.loader:
            return  # 期间换目标/关闭：旧 loader 的迟到结果直接丢弃
        if loading.parent is not None:  # 期间可能整屏重排过，loading 已被清掉
            loading.remove()
        # 最后一批可能正好取尽：done 当场生效，不再让用户多按一次 Ctrl+N；
        # 拉空但没到头（网慢/懒加载没触发）不判死，尾行保留预告可再按
        self._exhausted = loader.done
        if new:
            await self._mount_batch(new)
        await self._mount_tail()

    @abstractmethod
    async def _mount_batch(self, new: list[Any]) -> None:
        """把一批新楼层渲染上屏（单次挂载 = 单次布局；序号推进在子类）。"""

    async def _mount_tail(self) -> None:
        """尾行状态机：到头挂 EOF 标记，没到头挂 Ctrl+N 预告。"""
        if self._exhausted:
            self._remove_hint()
            await self._body_scroll().mount(
                dim_line(f"（没有更多{self._unit}了）", classes="tail-line tail-eof")
            )
        else:
            await self._mount_hint()

    @abstractmethod
    async def _retry(self) -> None:
        """首载失败后的原地重试（重走首载；Ctrl+N 在 loader 为 None 时进来）。"""

    # ---- ←/→ 换目标（换篇 / 换楼层）----

    async def _retarget(self, delta: int) -> None:
        """换目标的共享机械件：拿目标 → 取消在途（取数与重排）→ 换出旧
        loader → 清渲染态清屏 → 子类 _adopt_target 重走首载。

        沿方向没有目标时不动任何状态，只给反馈（不换屏、不闪黑）。
        """
        target = self._next_target(delta)
        if target is None:
            feedback.info(self.app, self._no_target_text(delta))
            return
        self.workers.cancel_group(self, self._worker_group)
        self.workers.cancel_group(self, "rerender")
        await self._close_page_loader()
        self._exhausted = False
        self._reset_render_state()
        self._rendered = False
        self._remove_hint()
        await self._body_scroll().remove_children()
        await self._adopt_target(target)

    @abstractmethod
    def _next_target(self, delta: int) -> Any | None:
        """沿 delta 方向拿下一个可读目标（None = 到边，不动状态只反馈）。"""

    @abstractmethod
    def _no_target_text(self, delta: int) -> str:
        """到边反馈文案（前后方向的措辞由子类定）。"""

    @abstractmethod
    async def _adopt_target(self, target: Any) -> None:
        """收下新目标：更新屏内字段并重走首载（网络请求 / 同步渲染差异在子类）。"""

    @property
    @abstractmethod
    def _unit(self) -> str:
        """翻页对象的单位词（楼层/评论词，读源词汇表）。"""

    # ---- 共享小件 ----

    async def _close_page_loader(self) -> None:
        """换出并关闭本屏翻页 loader（幂等；swap-out + close 惯用法唯一实现）。"""
        loader, self.loader = self.loader, None
        if loader is not None:
            await loader.close()

    async def _close_loader(self) -> None:
        await self._close_page_loader()  # 卸载清理 = 换出并关掉翻页 loader

    def action_open_browser(self) -> None:
        webbrowser.open(self._browser_url())

    def _browser_url(self) -> str:
        """Ctrl+O 的目标：缺省当前条目；回答屏覆写带楼层直达链接。"""
        return self.item.url

    def _md_width(self, indent: int = 0) -> int:
        """正文折行宽（折行算术与 CSS 常量的对应关系集中在 ui/layout.py）。"""
        return max(md_body_width(self._body_scroll().region.width) - indent, 20)

    def _relabel_bindings(self, labels: dict[str, str], *, hide: str = "") -> None:
        """类级占位键位文案换源词汇表；hide 指定的键整体隐藏（保留绑定，
        误按仍有反馈路径，只是 Footer 不预告）。"""
        for key, label in labels.items():
            self._bindings.key_to_bindings[key] = [
                dataclasses.replace(b, description=label, show=b.show and key != hide)
                for b in self._bindings.key_to_bindings.get(key, [])
            ]
