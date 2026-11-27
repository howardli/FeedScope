"""列表行的渲染件与光标导航件：行怎么画、光标怎么走。

- 渲染：FeedItem -> ListItem 部件（圆点 / 源名列 / 时间列 / 元信息 / 折行标题）。
  度量与 CSS 常量的对应关系在 ui/layout.py（只算不画），本模块只做组装。
- 导航：jump_unread（环扫找未读）与 step_to（推进到下一篇可读）——主屏 /
  收藏屏 / 搜索屏共用。step_to 曾是三份同形的循环，改一处漏两处。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from textual.containers import Horizontal
from textual.widgets import Label, ListItem, ListView

from feedscope.core.models import FeedItem
from feedscope.core.source import Source
from feedscope.core.timefmt import fmt_relative
from feedscope.ui.layout import meta_col_text
from feedscope.ui.markup import escape
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
/* read rows: title dimmed one level (meta is already dim) so read/unread is
   legible at a glance even where the dot column is subtle (light theme) */
ListItem.row-read .item-title { color: $text-muted; }
/* source tag column (cross-source lists: favorites and search results; width set
   per-batch inline): left-aligned and muted so cross-source rows stay visually uniform */
.item-tag { text-wrap: nowrap; color: $text-muted; }
/* relative-time column (width set per-batch inline, absent when the batch has
   no timestamps): right-aligned so the unit edge lines up into a column */
.item-time { text-align: right; text-wrap: nowrap; color: $text-muted; }
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
    time_width: int = 0,
) -> ListItem:
    """一条 FeedItem -> 列表行部件（圆点 + 源名 + 时间 + 元信息 + 折行标题）。

    标题按 width 折行（规范版：避头尾 + 词不拆），逐行转义；元信息列宽
    逐行 inline 设置（列宽随批内容自适应，见 layout.score_col_width），
    右对齐与超宽折行走 CSS（.item-score / .item-title）。mark 是未读圆点
    （MainScreen._row_mark 的产物，缓存先画与刷新共用同一规则；已读为空串）。
    tag/tag_width 是跨源列表（收藏屏）的源名列：左对齐、批内统一宽，
    让源名成列、元信息回到各源原生形态——源名拼进元信息会让右对齐列
    左缘参差且超限折行，跨源混排时尤其乱。主列表整屏同源，不传即无此列。
    time_width 是相对时间列（layout.time_col_width 的产物，批内有
    published 才 >0）：右对齐成列，纯数字档位（0m/Nm/Nh/Nd/日期）。
    """
    title = "\n".join(escape(line) for line in wrap_display(it.title, width))
    score = Label(meta_col(source, it), classes="item-score")
    score.styles.width = score_width
    cells: list[Label] = [Label(mark, classes="item-mark")]
    if tag:
        # 订阅名/频道名是用户与第三方数据：Label 默认解析 markup，'[AI] 周刊'
        # 会被当样式标签吞掉、含闭合标记的直接抛 MarkupError（同 FeedNavItem）
        tag_label = Label(escape(tag), classes="item-tag")
        tag_label.styles.width = tag_width
        cells.append(tag_label)
    if time_width:
        time_label = Label(fmt_relative(it.published), classes="item-time")
        time_label.styles.width = time_width
        cells.append(time_label)
    cells.append(score)
    cells.append(Label(title, classes="item-title"))
    # 二态圆点语义下 mark 为空 = 已读：行加 row-read 类（标题置灰，CSS 见上）
    item = ListItem(Horizontal(*cells, classes="item-row"))
    if not mark:
        item.add_class("row-read")
    return item


def update_row_mark(
    view: ListView, rows: Sequence[FeedItem], item: FeedItem, mark: str
) -> None:
    """单行圆点就地重算：按 (source, id) 在 rows 里定位（行号即 view.children
    下标——行身份是键不是对象，收藏屏回写主屏传的是快照副本，等值比对会
    失配），更新圆点并随 mark 置灰/恢复标题。主屏与收藏屏共用；行还没画上
    （在途/未挂载的页）直接返回，下批渲染自然对。"""
    idx = next(
        (i for i, it in enumerate(rows) if it.source == item.source and it.id == item.id),
        None,
    )
    if idx is None or idx >= len(view.children):
        return
    row = view.children[idx]
    row.query_one(".item-mark", Label).update(mark)
    row.set_class(not mark, "row-read")  # 标题置灰随圆点同进退


def jump_unread(
    view: ListView,
    rows: Sequence[FeedItem],
    is_read: Callable[[FeedItem], bool],
    delta: int,
) -> bool:
    """沿 delta 方向环扫一圈找首条未读：命中即选中并返回 True（列表自动滚到行）；
    一圈无未读 / 空表返回 False（调用方给反馈）。主屏与收藏屏共用。"""
    if not rows:
        return False
    cur = view.index or 0
    for step in range(1, len(rows) + 1):
        i = (cur + delta * step) % len(rows)
        if not is_read(rows[i]):
            view.index = i
            return True
    return False


def step_to(
    view: ListView,
    rows: Sequence[FeedItem],
    delta: int,
    *,
    readable: Callable[[FeedItem], bool],
    is_read: Callable[[FeedItem], bool],
    unread_only: bool = False,
) -> FeedItem | None:
    """沿 delta 方向推进到下一条「可读」的行：同步列表选中并返回它；到边 / 空表
    不动选中、返回 None（调用方给反馈）。unread_only 时只停在未读的可读行上
    （newsboat 的 next-unread：读着一篇直接跳到下一篇没读过的）。

    可读 = readable(it)（Enter 会进终端详情的类型——阅读流要留在终端里，
    阅读中突然弹出浏览器是事故）。不在这里计已读：调用方拿到目标后按自己的
    动线标记（主屏还要顺手记续读位置）。"""
    if not rows or view.index is None:
        return None
    for idx in range(view.index + delta, len(rows) if delta > 0 else -1, delta):
        it = rows[idx]
        if not readable(it):
            continue
        if unread_only and is_read(it):
            continue
        view.index = idx
        return it
    return None
