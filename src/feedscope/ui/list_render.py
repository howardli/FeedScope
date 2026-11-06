"""列表行的渲染件：FeedItem -> ListItem 部件（纯展示，与度量算术同族）。

从 main.py 下沉的三个纯函数（main 只留「何时重建」，「怎么建一行」在这）：
元信息列文本、折行标题、整行部件。度量与 CSS 常量的对应关系在
ui/layout.py，本模块只做组装。
"""

from __future__ import annotations

from rich.markup import escape
from textual.containers import Horizontal
from textual.widgets import Label, ListItem

from feedscope.core.models import FeedItem
from feedscope.core.source import Source
from feedscope.ui.layout import meta_col_text
from feedscope.ui.wrap import wrap_display


def meta_col(source: Source, it: FeedItem) -> str:
    """元信息列的 markup 文本（规则见 layout.meta_col_text）；空元信息占一格空位。"""
    text = meta_col_text(source, it)
    return f"[dim]{escape(text)}[/dim]" if text else " "


def build_item(it: FeedItem, width: int, score_width: int, source: Source) -> ListItem:
    """一条 FeedItem -> 列表行部件（元信息列 + 折行标题）。

    标题按 width 折行（规范版：避头尾 + 词不拆），逐行转义；元信息列宽
    逐行 inline 设置（列宽随批内容自适应，见 layout.score_col_width），
    右对齐与超宽折行走 CSS（.item-score / .item-title）。
    """
    title = "\n".join(escape(line) for line in wrap_display(it.title, width))
    score = Label(meta_col(source, it), classes="item-score")
    score.styles.width = score_width
    return ListItem(
        Horizontal(
            score,
            Label(title, classes="item-title"),
            classes="item-row",
        )
    )
