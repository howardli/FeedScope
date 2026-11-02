"""HTML 媒体标签 -> 文本标记的共用件：TUI 显示不了图/视频，正文里换成
「[图片]/[视频]」markdown 链接（地址折叠可点击，见 mark）。知乎/X/RSS/V2EX
在用，源内特例留在源文件里。

地址不能直接进 html2text：它会把 URL 里的 & 当实体转义坏（&x=1 -> &x;=1）。
所以先换成 NUL 包裹的记号（NUL 不会出现在正文里，html2text 原样放过），
跑完 html2text 再由 Tokens 还原。
"""

from __future__ import annotations

import html as _html
import re

from feedscope.core.const import new_h2t
from feedscope.core.text import strip_zwsp

_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*["\']([^"\']*)["\']')
_TOKEN_RE = re.compile("\x00(\\d+)\x00")

# <video>…</video> 整块（嵌套 <source> 一起吃掉），自闭合也认；没有闭合标签的残片不碰
VIDEO_RE = re.compile(r"<video\b[^>]*(?:/>|>.*?</video>)", re.IGNORECASE | re.DOTALL)


def attrs(tag: str) -> dict[str, str]:
    """开标签 -> 属性字典：键转小写，值反转义（src 里的 &amp; 还原成 &）。"""
    return {k.lower(): _html.unescape(v) for k, v in _ATTR_RE.findall(tag)}


def pick_url(tag: str, attr_order: tuple[str, ...]) -> str:
    """按优先级取第一个可用地址：跳过 data: 懒加载占位，// 开头补全 https:。"""
    a = attrs(tag)
    for name in attr_order:
        url = a.get(name, "").strip()
        if url and not url.startswith("data:"):
            return "https:" + url if url.startswith("//") else url
    return ""


def video_url(block: str) -> str:
    """<video> 整块 -> 播放地址：嵌套 <source src> 优先，其次 video 标签自己的 src。"""
    m = re.search(r"<source\b[^>]*>", block, re.IGNORECASE)
    if m:
        url = pick_url(m.group(0), ("src",))
        if url:
            return url
    m = re.search(r"<video\b[^>]*>", block, re.IGNORECASE)
    return pick_url(m.group(0), ("src", "data-src")) if m else ""


def mark(label: str, url: str) -> str:
    """媒体记号 -> markdown 链接「[图片](地址)」；地址拿不到时退回纯标记。

    链接形式比裸 URL 平铺好两截：Textual 的 Markdown 把它渲染成可点击
    组件（点击即开浏览器），URL 折叠不占行宽；显式链接语法也不会被
    markdown-it 的 linkify 吞掉后续中文。URL 里的 ) 截断链接语法，转义
    成 %29（维基类图片地址常见）。
    """
    if not url:
        return label
    return f"{label}({url.replace(')', '%29')})"


class Tokens:
    """NUL 记号池：stash(最终文本) 换记号绕过 html2text，expand 统一还原。"""

    def __init__(self) -> None:
        self._texts: list[str] = []

    def stash(self, text: str) -> str:
        self._texts.append(text)
        return f"\x00{len(self._texts) - 1}\x00"

    def expand(self, text: str) -> str:
        return _TOKEN_RE.sub(lambda m: self._texts[int(m.group(1))], text)


# img 标签（要求带 src）：进 html2text 前先 stash 成 [图片](src) 记号——
# ignore_images 会连 src 一起丢，原图直链就没；绕行也防 & 被实体转义
IMG_RE = re.compile(r"""<img[^>]*?src=(["'])([^"']+)\1[^>]*>""", re.IGNORECASE)


def html_to_md(html: str) -> str:
    """整段 HTML -> markdown：img 换 [图片](原图直链)。RSS 与 V2EX 同管线。

    图片必须 stash 成 NUL 记号绕过 html2text 而不是先变 <a> 再转：链接文字
    若带字面方括号（<a>[图片]</a>），html2text 会生成 [[图片]](url) 双层括号，
    markdown 渲染不了；URL 里的 & 也会被它实体转义弄坏。
    """
    if not html:
        return ""
    tok = Tokens()
    stashed = IMG_RE.sub(lambda m: tok.stash(mark("[图片]", pick_url(m.group(0), ("src",)))), html)
    return strip_zwsp(tok.expand(new_h2t().handle(stashed))).strip()
