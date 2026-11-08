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

# 行首圆点的二态字符（列宽与折行算术见 layout.MARK_COL_WIDTH）：● 未读
# （编辑器蓝，直到打开才消失——传统阅读器语义，刷新不动它）、空 = 已读。
# 「本批新抓到几条」是瞬态信息，归顶部状态行（main.status_with_new）
MARK_UNREAD = "●"

# 列表行的共享样式（主屏与收藏屏各吃一份——Textual 的 DEFAULT_CSS 只作用
# 于本屏子树，跨屏不继承；两屏渲染同一套行部件，样式必须同源，否则收藏
# 屏会落回控件默认：行被拉伸填满、圆点无固定宽、标题无 1fr 折行）。
# 选中行的高亮底=编辑器蓝：蓝点叠蓝底不可见，高亮态圆点改走选中文字的
# 同源变量（主屏 pane-aside 的恢复规则留在 MainScreen 自己的 CSS 里）。
# 串内注释保持 ASCII：这是样式常量不是文案（无硬编码文案门禁的边界）
LIST_ROW_CSS = """
ListItem { padding: 0 1; height: auto; }
.item-row { height: auto; }
.item-mark { width: 2; color: $primary; }
ListItem.-highlight .item-mark { color: $block-cursor-foreground; }
/* source tag column (favorites only; width set per-batch inline): left-aligned
   and muted so cross-source rows stay visually uniform */
.item-tag { text-wrap: nowrap; color: $text-muted; }
/* score width is set per-row inline (see layout.score_col_width); right-aligned,
   overlong bylines wrap (full names must stay visible) */
.item-score { text-align: right; text-wrap: wrap; }
.item-title { width: 1fr; text-wrap: wrap; padding-left: 1; }
"""


def meta_col(source: Source, it: FeedItem) -> str:
    """元信息列的 markup 文本（规则见 layout.meta_col_text）；空元信息占一格空位。"""
    text = meta_col_text(source, it)
    return f"[dim]{escape(text)}[/dim]" if text else " "


def build_item(
    it: FeedItem,
    width: int,
    score_width: int,
    source: Source,
    mark: str = "",
    tag: str = "",
    tag_width: int = 0,
) -> ListItem:
    """一条 FeedItem -> 列表行部件（圆点列 + 可选源名列 + 元信息列 + 折行标题）。

    标题按 width 折行（规范版：避头尾 + 词不拆），逐行转义；元信息列宽
    逐行 inline 设置（列宽随批内容自适应，见 layout.score_col_width），
    右对齐与超宽折行走 CSS（.item-score / .item-title）。mark 是未读圆点
    （MainScreen._row_mark 的产物，缓存先画与刷新共用同一规则；已读为空串）。
    tag/tag_width 是跨源列表（收藏屏）的源名列：左对齐、批内统一宽，
    让源名成列、元信息回到各源原生形态——源名拼进元信息会让右对齐列
    左缘参差且超限折行，跨源混排时尤其乱。主列表整屏同源，不传即无此列。
    """
    title = "\n".join(escape(line) for line in wrap_display(it.title, width))
    score = Label(meta_col(source, it), classes="item-score")
    score.styles.width = score_width
    cells: list[Label] = [Label(mark, classes="item-mark")]
    if tag:
        tag_label = Label(tag, classes="item-tag")
        tag_label.styles.width = tag_width
        cells.append(tag_label)
    cells.append(score)
    cells.append(Label(title, classes="item-title"))
    return ListItem(Horizontal(*cells, classes="item-row"))
