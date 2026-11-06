"""弹层的方向键焦点移动：与主屏同一套「方向键管焦点」的键位语言。

弹层结构都是「纵向列表 + 底部按钮行」：列表到底再 ↓ 移交按钮行、按钮上
按 ↑ 回列表、按钮行内 ←/→ 平移——不碰 Tab 也能全键盘走完任何弹层。
列表的 ↑/↓ 是控件级原生键（先在本列表内移动），「到底走出」必须由列表
子类在到边时自己移交焦点；按钮/输入框不绑方向键，屏级绑定直接命中
（屏级先于 App 级，主屏的导航绑定不会掺和——主屏键已随 MainScreen 归屏）。

ArrowModalScreen 的焦点链是线性的（布局顺序），←/↑ 与 →/↓ 两两同义——
不假称空间语义；四个方向仍各留一个 action 方法，因为 → 是覆写点
（F2 源管理里「列表上 → 进订阅管理」，见 source_setup 的 action_nav_right）。
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


def _exit_at_end(widget, *, at_end: bool) -> None:
    """列表的 ↓ 统一出口：末行（或空表）再 ↓ 出列表进按钮行，否则本列表内下移。

    FocusNav 两族列表共用的「到底走出」约定——各列表只判自己的到边条件
    （SelectionList 看 highlighted/option_count，ListView 看 index/children）。
    """
    if at_end:
        widget.screen.focus_next()
    else:
        widget.action_cursor_down()


class FocusNavSelectionList(SelectionList):
    """源勾选列表：末行再 ↓ 移交按钮行（首行 ↑ 留原生夹持——上方无可聚焦）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("down", "cursor_down_or_exit", show=False),
    ]

    def action_cursor_down_or_exit(self) -> None:
        _exit_at_end(
            self,
            at_end=self.highlighted is None or self.highlighted >= self.option_count - 1,
        )


class FocusNavListView(ListView):
    """订阅列表：末行再 ↓ 移交按钮行（订阅列表的 Enter 是改名，不受影响）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("down", "cursor_down_or_exit", show=False),
    ]

    def action_cursor_down_or_exit(self) -> None:
        _exit_at_end(self, at_end=self.index is None or self.index >= len(self.children) - 1)
