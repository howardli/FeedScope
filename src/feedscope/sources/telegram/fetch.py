"""取数层：t.me/s 频道预览页抓取 + 「全部」聚合流的并发编排。

- 单频道：GET https://t.me/s/<名>（可选 ?before=<最小id> 翻更旧一页，页内
  ~20 条）；纯 HTML 无 JSON，免登录免 JS。预览页没有 ETag/Last-Modified
  （不走 RSS 的条件请求），刷新就是整页重拉——一页 148KB 级，阅读节奏下无害
- 聚合「全部」：并发拉全部频道（信号量限并发对源礼貌），单频道失败只损失
  自己的条目（同 RSS fetch_merged 的失败隔离哲学）
"""

from __future__ import annotations

import asyncio

from feedscope.core import http
from feedscope.core.config import RssFeed
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, SourceError, dedup_append
from feedscope.sources.telegram.parse import parse_channel_html

# 同步拉多少个频道：并发太猛对 t.me 不礼貌（RSS 同款上限）
MAX_PARALLEL = 8

_SITE = "https://t.me/s"


async def fetch_channel_html(name: str, before: int | None = None) -> str:
    """一个频道的一页预览 HTML；before = 已见最小消息号（翻更旧一页）。"""
    params = {"before": str(before)} if before else None
    r = await http.get(f"{_SITE}/{name}", source=f"Telegram @{name}", params=params)
    return r.text


class TelegramFeedLoader(FeedLoader):
    """单频道翻页：下一页游标 = 本页解析出的最小消息号；翻空即到头。"""

    def __init__(self, channel: str, label: str) -> None:
        self._channel = channel
        self._label = label
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._before: int | None = None
        self.done = False

    async def load_more(self) -> list[FeedItem]:
        if self.done:
            return []
        html = await fetch_channel_html(self._channel, self._before)
        items = parse_channel_html(html, channel=self._channel, label=self._label)
        new = dedup_append(self.items, self._seen, items)
        if not new:
            self.done = True  # 翻到头（或该 before 段空页）：真到头不再打扰
            return []
        self._before = min(int(it.id.rsplit("/", 1)[-1]) for it in new)
        return new

    async def close(self) -> None:
        pass  # 连接池由 core/http 进程级持有，无源内资源


async def _fetch_one(feed: RssFeed) -> list[FeedItem]:
    """拉一个频道的首页并解析；时间倒序（最新在前）。"""
    html = await fetch_channel_html(feed.key)
    items = parse_channel_html(html, channel=feed.key, label=feed.label, all_view=True)
    return items[::-1]


async def fetch_merged(feeds: list[RssFeed]) -> tuple[list[FeedItem], list[str]]:
    """并发拉全部频道 ->「全部」聚合：按发布时间倒序合并。

    返回 (条目, 失败原因列表)；单频道失败隔离（只损失自己），全部失败由
    调用方决定报错。时间缺失的条目垫底（罕见，预览页都有 datetime）。
    """
    sem = asyncio.Semaphore(MAX_PARALLEL)
    errors: list[str] = []

    async def guarded(feed: RssFeed) -> list[FeedItem]:
        async with sem:
            try:
                return await _fetch_one(feed)
            except SourceError as exc:
                errors.append(f"@{feed.key}：{exc}")
                return []

    batches = await asyncio.gather(*(guarded(f) for f in feeds))
    items = [it for batch in batches for it in batch]
    items.sort(key=lambda it: str(it.extra.get("published") or ""), reverse=True)
    return items, errors
