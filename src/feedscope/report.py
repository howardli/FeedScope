"""行式检查输出：doctor / probe 共用的 [OK]/[FAIL]/[SKIP] 状态行。

标记的宽度与配色取向只在这里定义一次——两个子命令的输出永远同形，
用户在一个工具里认得的标记在另一个里不用重学。
"""

from __future__ import annotations

from typing import Literal

State = Literal["OK", "FAIL", "SKIP"]  # 状态行三态（doctor / probe 共用）

MARKS: dict[str, str] = {"OK": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}  # OK 补空格对齐三态


def line(state: State, label: str, detail: str = "") -> bool:
    """打一行状态；返回是否 OK（调用方累计通过项用）。"""
    print(f"{MARKS[state]} {label}" + (f" - {detail}" if detail else ""))
    return state == "OK"
