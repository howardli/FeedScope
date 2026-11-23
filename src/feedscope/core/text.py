"""终端文本工具：显示宽度、按宽截断与零宽字符清理，渲染层与解析层共用。

显示宽度 = 全宽(F/W)字符 2 列、其余 1 列；中文网页文本里到处是 U+200B
零宽字符，进终端前清掉。这些是「终端显示」的通用算术，既不属于 ui（渲染）
也不属于某个源（个性化解析），放 core 让两侧都能 import——parse 层不许依赖
ui（sources/x/parse.py 曾为此本地复制这两行），core 是它唯一能共享的地方。
"""

from __future__ import annotations

import re
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


def is_cjk(ch: str) -> bool:
    """是否中日韩字符（表意文字 / 中文标点 / 全角形式）：输入法上屏内容的判定范围。"""
    return (
        "\u4e00" <= ch <= "\u9fff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u3000" <= ch <= "\u303f"
        or "\uff00" <= ch <= "\uffef"
    )


# Markdown 行内链接（含图片）与自动链接：[文字](地址 "可选标题") / ![图](地址) /
# <https://…>。正文里的链接一律由 html_to_md / media.mark 产成行内链接形态，
# 这里不引 Markdown 解析器——只为「把链接列出来给键盘选」这一件事
_LINK_RE = re.compile(
    r"\[((?:\\.|[^\\\[\]])*)\]\(\s*(<[^>\s]*>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)"
)
_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")


def links_in(markdown: str) -> list[tuple[str, str]]:
    """正文里的链接：(链接文字, 地址)，按出现顺序，同一地址只留首次。

    给阅读屏的链接选择器用——[图片]/[视频]/@提及 这些链接此前只能鼠标点，
    纯键盘用户跟进不了。行内链接与 <https://…> 自动链接都认；纯锚点（#x）
    不算（本应用没有文内跳转）。"""
    found: list[tuple[int, str, str]] = [
        (m.start(), m.group(1), m.group(2)) for m in _LINK_RE.finditer(markdown)
    ]
    found += [(m.start(), m.group(1), m.group(1)) for m in _AUTOLINK_RE.finditer(markdown)]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, text, href in sorted(found):
        href = href.strip("<>")
        if href.startswith("#") or href in seen:
            continue
        seen.add(href)
        out.append((text.strip(), href))
    return out
