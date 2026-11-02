"""取数层：订阅抓取（httpx 条件请求）+「全部」聚合流的并发编排。

- 条件请求（ETag / Last-Modified）：模块级缓存按 URL 记验证器与上次响应体，
  304 直接回放缓存正文——F5 反复刷新对源友好，也省流量。缓存只在进程内：
  重启后第一次仍是无条件 GET，语义最简单也够用（阅读器不是离线仓库）。
- 聚合拉取：信号量限并发（对源礼貌），单订阅失败隔离——一个死源只损失
  自己的条目，错误以短消息列表上交编排层决定报错还是降级。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from feedscope.core import http
from feedscope.core.config import RssFeed
from feedscope.core.models import FeedItem
from feedscope.core.source import SourceError
from feedscope.sources.rss.parse import parse_feed

# 同步拉多少个订阅：并发太猛会被源侧限流（RSS 站点普遍小水管）
MAX_PARALLEL = 8

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

    合并规则：按发布时间倒序（缺时间沉底，保持订阅书写顺序）；同一 URL
    多源出现只留一份（转载/聚合源常见），保留书写顺序靠前的订阅版本。
    """
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(feed: RssFeed) -> tuple[list[FeedItem], str | None]:
        async with sem:
            try:
                body = await fetch_body(feed)
            except Exception as exc:  # 单订阅失败隔离：错误上交，不拖垮聚合
                return [], f"{feed.label}：{exc}"
            return parse_feed(body, feed_key=feed.key, feed_label=feed.label, all_view=True), None

    pairs = await asyncio.gather(*(one(f) for f in feeds))
    errors = [err for _, err in pairs if err]
    merged: list[FeedItem] = []
    seen_urls: set[str] = set()
    for items, _ in pairs:
        for it in items:
            if it.url:
                if it.url in seen_urls:
                    continue
                seen_urls.add(it.url)
            merged.append(it)
    merged.sort(key=lambda it: -float(it.extra.get("published") or 0.0))
    return merged, errors
