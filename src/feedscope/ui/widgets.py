"""屏间共享的小部件工厂：多屏复用、不属于任何一屏的部件放这里。"""

from __future__ import annotations

from textual.widgets import Static


def divider() -> Static:
    """全宽分隔线（dim 灰）：楼层/区块之间的边界，超宽由容器裁掉。"""
    return Static("[dim]" + "─" * 200 + "[/dim]", classes="answer-divider")
