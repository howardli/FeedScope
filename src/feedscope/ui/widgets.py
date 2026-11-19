"""主屏的配套部件：站点 tab、信息流边栏项、列表视图（从 main.py 外移）。

纯展示/输入部件，不含屏级状态；与 MainScreen 的相互引用只在 FeedListView
的 resize 自愈一处（运行时懒导入 MainScreen，见 on_resize 注释）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding
from textual.events import Resize
from textual.message import Message
from textual.timer import Timer
from textual.widgets import ListView, Static

from feedscope.ui import Bindings
from feedscope.ui.layout import RESIZE_DEBOUNCE

if TYPE_CHECKING:
    pass  # 运行时导入会与 main.py 成环


class SourceTabPressed(Message):
    """顶部站点 tab 被点击：与 Tab 循环共用同一条切源路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class FeedNavPressed(Message):
    """左侧信息流项被点击：与 ←/→ 共用同一条切流路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class SourceTab(Static):
    """顶部站点 tab：显示源名，点击切到该源；高亮由 CSS class 决定。

    不可聚焦（can_focus=False 默认）：点击不抢列表焦点，Tab 键语义完全不变。
    """

    def __init__(self, idx: int, label: str, active: bool) -> None:
        super().__init__(
            label,
            id=f"source-tab-{idx}",
            classes="source-tab" + (" source-tab-active" if active else ""),
        )
        self.idx = idx

    def on_click(self) -> None:
        self.post_message(SourceTabPressed(self.idx))


class FeedNavItem(Static):
    """左侧信息流项：流名 + 未读角标，点击切流（键盘走 ←/→）；高亮由 CSS class 决定。"""

    def __init__(self, idx: int, label: str, active: bool, unread: int = 0) -> None:
        # 角标是纯数字不进 i18n；≥1000 显示 999+ 防撑爆 16 宽边栏，0 不占位
        text = f"{label} [dim]{unread if unread < 1000 else '999+'}[/dim]" if unread else label
        super().__init__(
            text,
            id=f"feed-nav-{idx}",
            classes="feed-nav-item" + (" feed-nav-active" if active else ""),
        )
        self.idx = idx

    def on_click(self) -> None:
        self.post_message(FeedNavPressed(self.idx))


class FeedListView(ListView):
    """列表视图：Tab/Shift+Tab 切网站（widget 层绑定压过 Screen 焦点切换）。

    导航键改派为裸动作名（不带 app. 前缀）：沿焦点链冒泡到主屏的
    action_nav_*（列表是主屏唯一可聚焦控件，主屏常态下必经此路）。
    """

    BINDINGS: ClassVar[Bindings] = [
        # 切站键位页脚不亮（极简页脚只留 f1/^q，提示全在 F1 帮助页）：
        # Tab 是切站唯一键位，来路在帮助页与 README 键位表
        Binding("tab", "screen.next_source", "bindings.switch_source", show=False),
        Binding("shift+tab", "screen.prev_source", "bindings.switch_source", show=False),
        # ↑/↓/Enter 同键改派主屏导航动作（压过 ListView 原生选中/移动）：
        # 焦点在列表上是主屏常态，区域焦点 _pane 决定这组键到哪——
        # 逻辑集中在主屏，这里只负责把键活着送过去
        Binding("up", "screen.nav_up", show=False),
        Binding("down", "screen.nav_down", show=False),
        Binding("enter", "screen.nav_enter", show=False),
        # home/end/pageup/pagedown 原生是列表视口滚动：键盘在边栏/源栏时
        # 滚一个看不见的列表违反「焦点在哪键就作用在哪」，一并改派分派
        Binding("home", 'screen.nav_scroll("home")', show=False),
        Binding("end", 'screen.nav_scroll("end")', show=False),
        Binding("pageup", 'screen.nav_scroll("pageup")', show=False),
        Binding("pagedown", 'screen.nav_scroll("pagedown")', show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_width = 0
        self._resize_timer: Timer | None = None

    def on_resize(self, event: Resize) -> None:
        if event.size.width == self._last_width:
            return
        self._last_width = event.size.width
        # 懒导入防成环（main.py 导入本模块，反向 isinstance 只能在用时取）
        from feedscope.ui.screens.main import MainScreen

        screen = self.screen
        if not isinstance(screen, MainScreen) or not screen.items:
            return  # 首布局（尚无内容）不算；空列表重排也是空转
        # 拖拽改宽连发 Resize，稳定后再整屏重排（防抖与详情屏同款）；宽度
        # 与 _last_width 比对（初值 0），若列表竟在首布局前建好（窄宽挤压
        # 在左），首个 Resize 也会触发重排自愈——详情屏的 rendered-width
        # 自愈同思路，这里列表无渲染态可记，用 0 初值起比对
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, screen.rebuild_items)

    def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
