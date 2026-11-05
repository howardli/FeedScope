"""取数层：订阅抓取（httpx 条件请求）+「全部」聚合流（共享编排）。

- 条件请求（ETag / Last-Modified）：模块级缓存按 URL 记验证器与上次响应体，
  304 直接回放缓存正文——F5 反复刷新对源友好，也省流量。缓存只在进程内：
  重启后第一次仍是无条件 GET，语义最简单也够用（阅读器不是离线仓库）。
- 聚合编排（并发/失败隔离/倒序合并）见 core.source.merge_feeds；RSS 的差异
  是聚合视图解析、跨订阅 URL 去重、按发布时间（unix 秒）倒序。
"""

from __future__ import annotations

from dataclasses import dataclass

from feedscope.core import http
from feedscope.core.config import RssFeed
from feedscope.core.models import FeedItem
from feedscope.core.source import SourceError, merge_feeds
from feedscope.sources.rss.parse import parse_feed

# RSS 专用 Accept：部分源对浏览器 UA + */* 的组合反而回 HTML 页，声明清楚要 feed
_ACCEPT = "application/rss+xml, application/atom+xml, application/feed+json, application/xml, text/xml, */*"


@dataclass
class _CondEntry:
    """一个订阅的条件请求缓存（验证器 + 上次响应体）。"""

    etag: str | None
    last_modified: str | None
    body: bytes


_CACHE: dict[str, _CondEntry] = {}


def _conditional_headers(cached: _CondEntry | None) -> dict[str, str]:
    headers = {"Accept": _ACCEPT}
    if cached is not None:
        if cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
    return headers


async def fetch_body(feed: RssFeed) -> bytes:
    """抓一个订阅的 XML 字节；304 回放缓存体，200 记验证器并更新缓存。"""
    cached = _CACHE.get(feed.url)
    r = await http.get(
        feed.url,
        source=f"RSS「{feed.label}」",
        headers=_conditional_headers(cached),
        allow_304=True,
    )
    if r.status_code == 304:
        if cached is not None:
            return cached.body
        # 无缓存却 304（服务器不合规）：去掉条件头再来一次，还 304 才是真没辙
        r = await http.get(feed.url, source=f"RSS「{feed.label}」", headers={"Accept": _ACCEPT})
        if r.status_code == 304:
            raise SourceError(f"RSS「{feed.label}」返回 304 但本地没有可用的缓存内容")
    _CACHE[feed.url] = _CondEntry(
        etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"), body=r.content
    )
    return r.content


async def fetch_merged(feeds: list[RssFeed]) -> tuple[list[FeedItem], list[str]]:
    """全部订阅并发拉取并聚合成一条时间线：(合并条目, 失败订阅的短消息列表)。

    编排（并发/失败隔离/倒序合并）见 core.source.merge_feeds；RSS 的差异：
    聚合视图解析（all_view，订阅名进元信息列）、跨订阅 URL 去重（同一 URL
    多源覆盖只留书写顺序靠前的一份）、按发布时间（unix 秒）倒序。
    """

    async def one(feed: RssFeed) -> list[FeedItem]:
        body = await fetch_body(feed)
        return parse_feed(body, feed_key=feed.key, feed_label=feed.label, all_view=True)

    return await merge_feeds(
        feeds,
        one,
        error_label=lambda f: f.label,
        sort_key=lambda it: float(it.extra.get("published") or 0.0),
        dedup_urls=True,
    )
