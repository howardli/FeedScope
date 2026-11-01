"""布局度量：列表屏与详情屏共用的可用宽度计算与 resize 防抖间隔。

魔数与 ui/app.py 里 CSS 的对应关系集中在这里——改 CSS 必须同步改这里，
这是两屏唯一允许出现宽度算术的地方。
"""

from __future__ import annotations

# 列表行的固定开销（不含元信息列）：滚动条(2) + ListItem padding 0 1(2)
# + .item-title padding-left(1)；元信息列宽随批内容自适应（见 app.score_col_width），
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

# 正文 markdown 可用宽 = 容器宽 - 8：
# 滚动条(2) + 左右 padding 0 2(4) + 2 列余量防 Rich 二次折行
MD_OVERHEAD = 8

# resize 防抖：拖拽改宽会连发 Resize，稳定后再整屏重排
RESIZE_DEBOUNCE = 0.15  # 秒


def list_title_width(container_width: int, meta_col_width: int) -> int:
    """列表标题列的折行可用宽；meta_col_width = 当前批的元信息列宽。"""
    return max(10, container_width - LIST_ROW_OVERHEAD - meta_col_width)


def md_body_width(container_width: int) -> int:
    """详情正文的折行可用宽。"""
    return max(20, container_width - MD_OVERHEAD)
