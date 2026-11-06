"""TUI 层：Textual 应用与界面。"""

from __future__ import annotations

from textual.binding import Binding

# 键位声明的类型别名：Textual 基类把 BINDINGS 声明为宽松的历史联合
# （Binding | 两种元组形态），子类全用 Binding 对象声明时按它对齐，
# mypy 的 list 不变检查才放行——别名在此一处，各屏不再各自与基类搏斗。
Bindings = list[Binding | tuple[str, str] | tuple[str, str, str]]
