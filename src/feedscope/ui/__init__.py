"""TUI 层：Textual 应用与界面。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from textual.binding import Binding
from textual.screen import Screen

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环

# 键位声明的类型别名：Textual 基类把 BINDINGS 声明为宽松的历史联合
# （Binding | 两种元组形态），子类全用 Binding 对象声明时按它对齐，
# mypy 的 list 不变检查才放行——别名在此一处，各屏不再各自与基类搏斗。
Bindings = list[Binding | tuple[str, str] | tuple[str, str, str]]


def tui_of(screen: Screen[Any]) -> FeedScopeApp:
    """任意屏的 self.app 收窄成 FeedScopeApp：本应用的 App 恒为 FeedScopeApp，
    窄化只为让 accounts/state 等自定义成员拿到静态类型。主屏家族 / 阅读屏 /
    收藏屏的 _tui 三处 cast 收口在这里（不覆写 Screen.app：MessagePump.app
    是可写 property，覆写签名过不了检）。"""
    return cast("FeedScopeApp", screen.app)
