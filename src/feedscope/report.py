"""行式检查输出：doctor / probe 共用的 [OK]/[FAIL]/[SKIP]/[WARN] 状态行。

标记的宽度与取向只在这里定义一次——两个子命令的输出永远同形，
用户在一个工具里认得的标记在另一个里不用重学。

WARN 是「没坏但该看一眼」：不影响退出码（line 返回 False，调用方别并进
通过累计），用于 doctor 里需要用户知情、却不必判失败的事项（如数据文件
损坏后的留底）。
"""

from __future__ import annotations

from typing import Literal

State = Literal["OK", "FAIL", "SKIP", "WARN"]  # 状态行四态（doctor / probe 共用）

MARKS: dict[str, str] = {
    "OK": "[OK]  ",
    "FAIL": "[FAIL]",
    "SKIP": "[SKIP]",
    "WARN": "[WARN]",
}  # OK 补空格对齐


def line(state: State, label: str, detail: str = "") -> bool:
    """打一行状态；返回是否 OK（调用方累计通过项用——WARN 不算通过也不算失败）。"""
    print(f"{MARKS[state]} {label}" + (f" - {detail}" if detail else ""))
    return state == "OK"
