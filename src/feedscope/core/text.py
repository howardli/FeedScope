"""终端文本工具：显示宽度、按宽截断与零宽字符清理，渲染层与解析层共用。

显示宽度 = 全宽(F/W)字符 2 列、其余 1 列；中文网页文本里到处是 U+200B
零宽字符，进终端前清掉。这些是「终端显示」的通用算术，既不属于 ui（渲染）
也不属于某个源（个性化解析），放 core 让两侧都能 import——parse 层不许依赖
ui（sources/x/parse.py 曾为此本地复制这两行），core 是它唯一能共享的地方。
"""

from __future__ import annotations

import unicodedata

# 源码里用转义、别放字面量：字面零宽字符在源码里不可见，极易漏改
_ZWSP = "\u200b"


def char_width(ch: str) -> int:
    """终端显示宽度：全宽（F/W）字符 2 列，其余 1 列。"""
    return 2 if unicodedata.east_asian_width(ch) in "FW" else 1


def display_width(text: str) -> int:
    """整串的终端显示宽度（逐字符求和）。"""
    return sum(char_width(c) for c in text)


def truncate_display(text: str, limit: int) -> str:
    """按显示宽度截断（超长补省略号「…」）；不超长原样返回。"""
    if display_width(text) <= limit:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def strip_zwsp(text: str) -> str:
    """清掉零宽字符（U+200B）并 strip，中文网页文本进终端前的统一清洗。"""
    return (text or "").replace(_ZWSP, "").strip()
