"""HTML 媒体标签 -> 文本标记的共用件：TUI 显示不了图/视频，正文里换成「[图]/[视频] + 原始地址」，
地址留给用户自己贴浏览器看。知乎的 _md 在用，源内特例留在源文件里。

地址不能直接进 html2text：它会把 URL 里的 & 当实体转义坏（&x=1 -> &x;=1）。
所以先换成 NUL 包裹的记号（NUL 不会出现在正文里，html2text 原样放过），
跑完 html2text 再由 Tokens 还原。
"""

from __future__ import annotations

import html as _html
import re

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
    """「[图] 地址」；地址拿不到时退回纯标记。

    地址后补一个空格：紧跟其后的中文会被 markdown-it 的 linkify 吞进 URL
    （href 里混进 %E4%… 编码），空格划清边界；行尾多出来的空格渲染不可见。
    """
    return f"{label} {url} " if url else label


class Tokens:
    """NUL 记号池：stash(最终文本) 换记号绕过 html2text，expand 统一还原。"""

    def __init__(self) -> None:
        self._texts: list[str] = []

    def stash(self, text: str) -> str:
        self._texts.append(text)
        return f"\x00{len(self._texts) - 1}\x00"

    def expand(self, text: str) -> str:
        return _TOKEN_RE.sub(lambda m: self._texts[int(m.group(1))], text)
