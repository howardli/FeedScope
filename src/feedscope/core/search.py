"""本地搜索：跨「列表缓存 + 收藏快照」找条目（路线图里的全文搜索）。

数据层早就齐了——每个流的列表缓存是一份 JSON（≤60 流 × ≤200 条）、收藏是
另一份，标题 / 作者 / 时间都在，RSS 与 Telegram 的正文还随条目缓存。这里只做
两件事：把它们读成一份去重的条目表，以及按关键词筛。纯本地、零请求。

匹配规则：空白分隔的多个词全部命中才算（AND，大小写不敏感）；先看标题，
标题不全中再看正文（只有 RSS / Telegram 带正文——HN / 知乎 / X / Reddit /
V2EX 的详情不落盘，只能搜到标题级）。结果标题命中在前，各组内新的在前。
"""

from __future__ import annotations

import html as html_mod
import re
from collections.abc import Sequence

from feedscope.core import feed_cache
from feedscope.core.models import FeedItem

MAX_RESULTS = 200  # 上屏条数上限：再多的命中对「找一条」没有增量价值，列表却要挂成百上千行

_TAG_RE = re.compile(r"<[^>]*>")


def body_text(item: FeedItem) -> str:
    """条目正文的纯文本：RSS 的 HTML 去标签、Telegram 的 markdown 原样；没有正文的源回空串。

    extra 里这两个键的约定见 core/models（rss: content_html / telegram: content_md）。"""
    html = item.extra.get("content_html")
    if isinstance(html, str) and html:
        return html_mod.unescape(_TAG_RE.sub(" ", html))
    md = item.extra.get("content_md")
    return md if isinstance(md, str) else ""


def collect(favorites: Sequence[FeedItem]) -> list[FeedItem]:
    """可搜的条目表：收藏快照在前（最全、永不淘汰），随后是全部列表缓存；
    同一 (source, id) 只留首次出现（RSS 的条目同时活在「全部」与单订阅流里）。"""
    items = list(favorites)
    seen = {(it.source, it.id) for it in items}
    for _source, _feed, cached in feed_cache.load_all():
        items.extend(it for it in cached if (it.source, it.id) not in seen)
        seen.update((it.source, it.id) for it in cached)
    return items


def search(items: Sequence[FeedItem], query: str) -> list[FeedItem]:
    """关键词筛条目：标题命中的在前、正文命中的在后，各组按发布时间新→旧。"""
    terms = query.casefold().split()
    if not terms:
        return []
    by_title: list[FeedItem] = []
    by_body: list[FeedItem] = []
    for it in items:
        title = it.title.casefold()
        if all(term in title for term in terms):
            by_title.append(it)
            continue
        body = body_text(it).casefold()  # 标题不全中才取正文（取正文是这里唯一的花销）
        if body and all(term in body for term in terms):
            by_body.append(it)
    def newest(it: FeedItem) -> float:
        return it.published or 0  # 无时间的条目沉底（与聚合流同规则）

    by_title.sort(key=newest, reverse=True)
    by_body.sort(key=newest, reverse=True)
    return (by_title + by_body)[:MAX_RESULTS]
