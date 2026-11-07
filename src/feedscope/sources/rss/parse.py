"""解析层：feedparser 结果 -> FeedItem / 详情 markdown，纯函数不碰网络。

RSS/Atom 野源格式千奇百怪，容错原则「能救则救、救不了就丢条不丢流」：
标题带 HTML、缺 author、缺发布时间都只是字段级缺失；guid 与 link 双缺才
整条跳过（无法寻址，进详情 / 跳浏览器都没有落点）。
"""

from __future__ import annotations

import calendar
import html as html_mod
import re
from time import struct_time
from urllib.parse import urljoin

import feedparser

from feedscope.core.models import FeedItem
from feedscope.core.i18n import t
from feedscope.core.text import strip_zwsp

SOURCE_ID = "rss"

# 标题/作者里的残余标签（<b>标题</b>、署名 <a> 之类），清理成纯文本
_TAGS = re.compile(r"<[^>]*>")
# 首页 HTML 里的 <link> 声明（自动发现 feed 用）
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_HREF = re.compile(r"""href=(["'])(.*?)\1""", re.IGNORECASE)
_FEED_TYPES = ("rss+xml", "atom+xml", "feed+json", "feed+xml")


def _clean(text: object) -> str:
    """字段级清洗：去标签、还原实体、去零宽；None / 非 str 回空串。"""
    if not isinstance(text, str):
        return ""
    return strip_zwsp(html_mod.unescape(_TAGS.sub("", text)))


def _epoch(st: object) -> float:
    """feedparser 的 *_parsed（struct_time，UTC）-> unix 秒；缺省 0（排序沉底）。"""
    return float(calendar.timegm(st)) if isinstance(st, struct_time) and st.tm_year > 1 else 0.0


def _content_html(entry: dict) -> str:
    """正文 HTML：优先 content（多为全文），退 summary（多为摘要）。"""
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("value"), str):
            return first["value"]
    summary = entry.get("summary")
    return summary if isinstance(summary, str) else ""


def probe(body: bytes | str) -> tuple[str, int]:
    """(feed 自述标题, 条目数)：订阅管理判断「这是不是 feed」与取默认显示名。"""
    parsed = feedparser.parse(body)
    title = _clean(parsed.feed.get("title")) if getattr(parsed, "feed", None) else ""
    return title, len(parsed.entries)


def discover_feed_link(html: str, base_url: str) -> str:
    """首页 HTML -> feed 地址：<link rel="alternate" type="…rss/atom…">，
    相对 href 以 base_url 绝对化；找不到回空串。"""
    for tag in _LINK_TAG.findall(html):
        low = tag.lower()
        if "alternate" not in low or not any(t in low for t in _FEED_TYPES):
            continue
        m = _HREF.search(tag)
        if m and m.group(2):
            return urljoin(base_url, m.group(2))
    return ""


def parse_feed(
    body: bytes | str,
    *,
    feed_key: str,
    feed_label: str,
    all_view: bool,
    fallback_url: str = "",
) -> list[FeedItem]:
    """一个订阅的 XML -> FeedItem 列表（保持源内顺序，通常已是倒序）。

    all_view 决定元信息列内容：聚合「全部」里必须能认出条目来自哪个订阅
    （订阅名打头），单订阅视图里订阅名是噪音、只留作者。extra 带 detail
    所需的全部字段（feed 自带正文，进详情不重新抓）。
    """
    parsed = feedparser.parse(body)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = entry.get("link") or fallback_url
        guid = entry.get("id")
        if not guid and not link:
            continue  # 双缺无法寻址
        author = _clean(entry.get("author"))
        meta_parts = ([feed_label] if all_view else []) + ([author] if author else [])
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=f"{feed_key}/{guid or link}",
                title=_clean(entry.get("title")) or t("words.no_title"),
                url=link,
                meta=" · ".join(meta_parts),
                extra={
                    "feed_key": feed_key,
                    "feed_label": feed_label,
                    "author": author,
                    "published": _epoch(
                        entry.get("published_parsed") or entry.get("updated_parsed")
                    ),
                    "content_html": _content_html(entry),
                },
            )
        )
    return items
