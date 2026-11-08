"""布局度量：列表屏与详情屏共用的可用宽度计算、元信息列与 resize 防抖间隔。

魔数与 CSS 的对应关系集中在这里——改 CSS 必须同步改这里，
这是两屏唯一允许出现宽度算术的地方。
"""

from __future__ import annotations

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.source import Source
from feedscope.core.text import display_width

# 列表行的固定开销（不含元信息列）：滚动条(2) + ListItem padding 0 1(2)
# + .item-title padding-left(1)；元信息列宽随批内容自适应（见 score_col_width），
# 折行算术在 list_title_width 把两者合起来
LIST_ROW_OVERHEAD = 5

# 元信息列（.item-score）宽度算术：列宽 = 批内最长文本 + SPARE 余量（逐行
# inline 设置，CSS 不写死）。X 署名「名字 @handle」完整可读靠折行而不是加宽
# 列——正文都能折行，署名同理：超过列宽的在空格处断开，名字一行、@账号
# 一行（右对齐 + text-wrap: wrap，绝不缩略）。MAX 是列宽上限，保标题列有
# 足够宽度；18 ≈ 放得下任何 @handle（上限 15 字符）+ 常见短署名单行，
# 知乎热榜「2606 万热度」这类短元信息也只占实际所需。
META_COL_SPARE = 2
META_COL_MAX = 18

# 行首圆点列（.item-mark）：未读二态（● 未读 / 空 已读），固定 2 列、不折行；
# 折行算术在 list_title_width 一并扣除，标题可用宽不含它
MARK_COL_WIDTH = 2

# 正文 markdown 可用宽 = 容器宽 - 8：
# 滚动条(2) + 左右 padding 0 2(4) + 2 列余量防 Rich 二次折行
MD_OVERHEAD = 8

# resize 防抖：拖拽改宽会连发 Resize，稳定后再整屏重排
RESIZE_DEBOUNCE = 0.15  # 秒


def list_title_width(container_width: int, meta_col_width: int) -> int:
    """列表标题列的折行可用宽；meta_col_width = 当前批的元信息列宽。"""
    return max(10, container_width - LIST_ROW_OVERHEAD - MARK_COL_WIDTH - meta_col_width)


def md_body_width(container_width: int) -> int:
    """详情正文的折行可用宽。"""
    return max(20, container_width - MD_OVERHEAD)


def meta_col_text(source: Source, it: FeedItem) -> str:
    """元信息列文本：所有源、所有流同一条规则——「[类型标签] 作者/热度/回复数」。

    标签预告 Enter 的行为（非详情类型跳浏览器，词汇表归源，见 Source.kind_labels）；
    有标签时与元信息并列显示，不再顶掉作者。任何情况下不截断——列宽随批内
    最长内容自适应（见 score_col_width），超上限的长署名在列内折行（换行可以，
    缩略不行），名字与账号必须完整可见。
    """
    tag = t(source.kind_labels.get(it.kind, ""))
    return f"[{tag}] {it.meta}".strip() if tag else it.meta


def score_col_width(source: Source, items: list[FeedItem]) -> int:
    """元信息列宽：批内最长文本的显示宽 + 2 余量（META_COL_MAX 封顶）。

    自适应让知乎热榜「2606 万热度」这类短元信息只占实际所需；超过 MAX 的
    长署名不加宽列（标题列优先），在列内折行——名字一行、@账号一行，完整
    可见。空列表给 1（空态不显示列表）。
    """
    longest = max((display_width(meta_col_text(source, it)) for it in items), default=1)
    return min(longest, META_COL_MAX) + META_COL_SPARE
