"""弹层的方向键焦点移动：与主屏（app.py 的 nav_*）同一套空间语义。

弹层结构都是「纵向列表 + 底部按钮行」：列表到底再 ↓ 移交按钮行、按钮上
按 ↑ 回列表、按钮行内 ←/→ 平移——不碰 Tab 也能全键盘走完任何弹层。
列表的 ↑/↓ 是控件级原生键（先在本列表内移动），「到底走出」必须由列表
子类在到边时自己移交焦点；按钮/输入框不绑方向键，屏级绑定直接命中
（屏级先于 App 级，主屏 nav_* 的弹层守卫不会掺和）。
"""

from __future__ import annotations

from typing import ClassVar, TypeVar

from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import ListView, SelectionList

T = TypeVar("T")


class ArrowModalScreen(ModalScreen[T]):
    """弹层基类：←/↑ = 焦点后移、→/↓ = 焦点前移（焦点链 = 布局顺序）。

    必须是 ModalScreen 子类才能进绑定的 MRO 合并（裸 mixin 的 BINDINGS
    会被 _merge_bindings 跳过）；列表类控件自己绑 ↑/↓ 优先在列表内移动，
    这里的屏级绑定只在聚焦控件不消费方向键（按钮/输入框空档）时命中。
    需要给 → 留屏内语义的（F2 的管理订阅）覆写 action_nav_right 即可。
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("left", "nav_left", show=False),
        Binding("right", "nav_right", show=False),
        Binding("up", "nav_up", show=False),
        Binding("down", "nav_down", show=False),
    ]

    def action_nav_left(self) -> None:
        self.focus_previous()  # focus_next/previous 是 Screen 的方法

    def action_nav_right(self) -> None:
        self.focus_next()

    def action_nav_up(self) -> None:
        self.focus_previous()

    def action_nav_down(self) -> None:
        self.focus_next()


class FocusNavSelectionList(SelectionList):
    """源勾选列表：末行再 ↓ 移交按钮行（首行 ↑ 留原生夹持——上方无可聚焦）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("down", "cursor_down_or_exit", show=False),
    ]

    def action_cursor_down_or_exit(self) -> None:
        if self.highlighted is None or self.highlighted >= self.option_count - 1:
            self.screen.focus_next()  # 到底（或空表）再 ↓：出列表进按钮行
            return
        self.action_cursor_down()


class FocusNavListView(ListView):
    """订阅列表：末行再 ↓ 移交按钮行（订阅列表的 Enter 是改名，不受影响）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("down", "cursor_down_or_exit", show=False),
    ]

    def action_cursor_down_or_exit(self) -> None:
        if self.index is None or self.index >= len(self.children) - 1:
            self.screen.focus_next()
            return
        self.action_cursor_down()
