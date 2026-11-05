"""全屏阅读屏的共享骨架：问题详情屏与回答详情屏共用的机械件。

两屏（detail.py / answer.py）结构同形：Header + 正文滚动容器 + Footer，
Esc 返回、resize 防抖重排、尾行 hint 生命周期、副标题保存恢复、卸载
清理（停定时器 + 关 loader）——这些与「读的是什么」无关的部分收进
ReadingScreen，子类只剩各自的渲染与加载编排。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static

from feedscope.ui.layout import RESIZE_DEBOUNCE

# 两屏共享的样式：Textual 的 CSS 类属性不沿 MRO 合并（子类定义自己的 CSS
# 会整体遮蔽基类的），共享规则以模块常量供两屏拼进各自的 CSS——显式拼接
# 而不是依赖框架的继承细节
READING_CSS = """
/* 正文统一格式（所有源同形）：Markdown 标题抹平成段落样式——Textual 缺省
   给 H1 居中加粗、H2 下划线、H3 加粗、H5/H6 甚至 ansi_cyan，且 Header 基类
   color $text（auto 87%，比正文亮）+ margin 2 0 1 0；统一为正文的前景色/
   样式/对齐/边距（App 基类 color: $foreground，段落即继承它），标题不再有
   专属版式。蓝色与下划线只留给链接（theme.variables） */
MarkdownH1, MarkdownH2, MarkdownH3, MarkdownH4, MarkdownH5, MarkdownH6 {
    color: $foreground;
    background: transparent;
    text-style: none;
    content-align: left middle;
    margin: 0 0 1 0;
}

/* 楼层/区块分隔线（ui.widgets.divider）：两屏共用，超宽由容器裁掉 */
.answer-divider { width: 1fr; height: 1; }
"""


class ReadingScreen(Screen[None]):
    """阅读屏基类：resize 防抖整屏重排 + hint 生命周期 + 副标题/定时器/ loader 清理。

    子类契约（全部抽象）：
    - _body_scroll：正文滚动容器（重排/挂载的目标）
    - _render_doc：整屏渲染（首批与重排共用同一条路径）
    - _hint_widget：尾行动线预告
    - _reset_render_state：换目标/重排前清渲染态（楼层序号等子类字段）
    - _close_loader：卸载时关闭持有的 loader（幂等）
    子类自管 _rendered 的置位时机（首批渲染完成才吃 resize 重排）。
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._prev_sub_title: str | None = None  # 进屏前的 Header 副标题（退屏恢复）
        self._rendered = False  # 首批已上屏；此后 resize 才需要重排
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

    def on_resize(self, event: Resize) -> None:
        if event.size.width == self._last_width:
            return
        first_layout = self._last_width == 0  # 首次布局不算
        self._last_width = event.size.width
        if first_layout or not self._rendered:
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self._rerender)

    def _rerender(self) -> None:
        if not self._rendered:
            return
        scroll_y = self._body_scroll().scroll_y
        self.run_worker(self._rerender_body(scroll_y))

    async def _rerender_body(self, scroll_y: float) -> None:
        body = self._body_scroll()
        await body.remove_children()
        self._hint = None
        self._reset_render_state()
        try:
            await self._render_doc()
        except Exception:
            return  # 屏幕正在被关闭：放弃重排
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
