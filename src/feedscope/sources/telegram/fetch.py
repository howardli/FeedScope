"""取数层：t.me/s 频道预览页抓取 + 「全部」聚合流（共享编排）。

- 单频道：GET https://t.me/s/<名>（可选 ?before=<最小id> 翻更旧一页，页内
  ~20 条）；纯 HTML 无 JSON，免登录免 JS。预览页没有 ETag/Last-Modified
  （不走 RSS 的条件请求），刷新就是整页重拉——一页 148KB 级，阅读节奏下无害
- 聚合「全部」的编排（并发/失败隔离/倒序合并）见 core.source.merge_feeds；
  Telegram 的差异是按发布时间（ISO 字符串）倒序、错误标签带 @
"""

from __future__ import annotations

from feedscope.core import http
from feedscope.core.config import Subscription
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, dedup_append, merge_feeds
from feedscope.sources.telegram.parse import parse_channel_html

_SITE = "https://t.me/s"


async def fetch_channel_html(name: str, before: int | None = None) -> str:
    """一个频道的一页预览 HTML；before = 已见最小消息号（翻更旧一页）。"""
    params = {"before": str(before)} if before else None
    r = await http.get(f"{_SITE}/{name}", source=f"Telegram @{name}", params=params)
    return r.text


class TelegramFeedLoader(FeedLoader):
    """单频道翻页：下一页游标 = 本页解析出的最小消息号；翻空即到头。

    每页倒排追加（预览页是聊天式旧→新，阅读器时间线要新→旧）：翻页后
    更旧的整页缀在更旧的位置上，全局严格最新在前——与「全部」聚合流
    及所有源的时间线方向一致。
    """

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
        new = dedup_append(self.items, self._seen, items[::-1])
        if not new:
            self.done = True  # 翻到头（或该 before 段空页）：真到头不再打扰
            return []
        self._before = min(int(it.id.rsplit("/", 1)[-1]) for it in new)
        return new


async def _fetch_one(feed: Subscription) -> list[FeedItem]:
    """拉一个频道的首页并解析；时间倒序（最新在前）。"""
    html = await fetch_channel_html(feed.key)
    items = parse_channel_html(html, channel=feed.key, label=feed.label, all_view=True)
    return items[::-1]


async def fetch_merged(feeds: list[Subscription]) -> tuple[list[FeedItem], list[str]]:
    """并发拉全部频道 ->「全部」聚合：(条目, 失败原因列表)。

    编排见 core.source.merge_feeds；Telegram 的差异：按发布时间（ISO 字符串）
    倒序、错误标签带 @（时间缺失的条目垫底，罕见，预览页都有 datetime）。
    """
    return await merge_feeds(
        feeds,
        _fetch_one,
        error_label=lambda f: f"@{f.key}",
        sort_key=lambda it: str(it.extra.get("published") or ""),
    )
