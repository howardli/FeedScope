"""布局度量：列表屏与详情屏共用的可用宽度计算、各列宽度与 resize 防抖间隔。

魔数与 CSS 的对应关系集中在这里——改 CSS 必须同步改这里，
这是两屏唯一允许出现宽度算术的地方。本模块只算不画（不 import 任何控件）：
把一条条目画成行部件是 ui/list_render.py 的事。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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

# 相对时间列（.item-time）：fmt_relative 的最长文本（MM-DD / YY-MM）5 列
# 定宽；批内任一条带 published 才成列（+1 列间隙），全无则 0——tag 列同款
# 的「批内有才占列」先例，无时间可取的源（知乎热榜）行式与从前一致
TIME_COL_WIDTH = 5

# 正文 markdown 可用宽 = 容器宽 - 8：
# 滚动条(2) + 左右 padding 0 2(4) + 2 列余量防 Rich 二次折行
MD_OVERHEAD = 8

# resize 防抖：拖拽改宽会连发 Resize，稳定后再整屏重排
RESIZE_DEBOUNCE = 0.15  # 秒


def list_title_width(
    container_width: int, meta_col_width: int, tag_width: int = 0, time_width: int = 0
) -> int:
    """列表标题列的折行可用宽；meta_col_width = 当前批的元信息列宽，
    tag_width = 源名列宽（收藏屏跨源列表用，主列表为 0），
    time_width = 相对时间列宽（批内有 published 才非 0）。

    首布局前 region.width 为 0：按常规终端 80 先折一版（resize 自愈重排
    会按真实宽重来，两屏同一策略——兜底收在这里，调用方不再各写各的）。"""
    return max(
        10,
        (container_width or 80)
        - LIST_ROW_OVERHEAD
        - MARK_COL_WIDTH
        - tag_width
        - time_width
        - meta_col_width,
    )


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


def tag_col_width(items: list[FeedItem]) -> int:
    """源名列宽：批内最长 tag + 1（列内左对齐成列）；批内无 tag 为 0（不占列）。

    主列表的聚合流（RSS/Telegram「全部」）与收藏屏跨源列表共用；单源流
    整屏同源，tag 全空 → 列宽 0，行式与从前一致。
    """
    longest = max((display_width(it.tag) for it in items), default=0)
    return longest + 1 if longest else 0


def time_col_width(items: list[FeedItem]) -> int:
    """时间列宽：批内任一条带 published 即定宽（TIME_COL_WIDTH + 1 间隙），
    全无为 0（不占列）。定宽不随内容自适应——右对齐成列，NM/Nh/Nd/日期
    各档位宽度接近，让列稳定比省一两列更可读（源名列左对齐同理成列）。
    """
    return TIME_COL_WIDTH + 1 if any(it.published for it in items) else 0


# ---- 跨源列表（收藏屏 / 搜索结果）：源名列文本与两列宽度按「各条目自己的源」算 ----


def cross_source_tag(item: FeedItem, sources: Mapping[str, Source]) -> str:
    """源名列文本：聚合流条目自带更具体的出处（订阅名/频道名）优先，否则用源
    显示名；注册表里都找不到的落回裸 source id。"""
    if item.tag:
        return item.tag
    src = sources.get(item.source)
    return src.label_text if src is not None else item.source


def cross_source_widths(
    items: Sequence[FeedItem], sources: Mapping[str, Source]
) -> tuple[int, int]:
    """(源名列宽, 元信息列宽)：源名列 = 批内最长 + 1；元信息列按各条目自己的源
    取最长、封顶 META_COL_MAX（多源版 score_col_width——源名不拼进来，元信息
    保持各源在主列表的原生形态）。"""
    tag = max((display_width(cross_source_tag(it, sources)) for it in items), default=0)
    metas = (
        meta_col_text(sources[it.source], it) if it.source in sources else it.meta for it in items
    )
    score = max((display_width(text) for text in metas), default=1)
    return tag + 1, min(score, META_COL_MAX) + META_COL_SPARE
