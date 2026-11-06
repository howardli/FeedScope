"""RSS 源（rss）：通用订阅聚合——「全部」合并时间线 + 每订阅一个信息流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：httpx 条件请求（ETag/Last-Modified）+ 聚合流的并发编排
- parse.py  解析层：feedparser -> FeedItem / 详情 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（订阅型骨架走
  core.SubscribedSource，本文件只剩 RSS 的差异）

与 zhihu/x 的本质差异：feeds 不是代码写死的内置流，而是用户在 sources.toml
[rss.feeds] 里的订阅表（数据驱动的加法合成）；无登录、无评论层；详情读
feed 自带正文（content 优先、summary 兜底），摘要型源正文短，Ctrl+O 跳浏
览器补全。
"""

from __future__ import annotations

from typing import Any, ClassVar

from feedscope.core.config import Subscription
from feedscope.core.media import html_to_md
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import FeedLoader, SubscribedSource
from feedscope.core.timefmt import fmt_unix
from feedscope.sources.rss.fetch import fetch_body, fetch_merged
from feedscope.sources.rss.parse import SOURCE_ID, parse_feed


@register
class RssSource(SubscribedSource):
    source_id = SOURCE_ID
    label = "RSS"
    supports_detail = True
    subscribable = True  # feeds 来自用户订阅：F2 里 → 进订阅管理屏
    detail_kinds = frozenset({""})  # 全部条目（kind 空）Enter 都进终端详情
    detail_unit_label = "正文"  # 无楼层，详情屏尾行与 probe 用这个词

    all_feed: ClassVar[str] = "all"  # 聚合流名（「全部」），feeds 首位
    sub_noun: ClassVar[str] = "订阅"
    reserved_feed_keys = (all_feed,)
    _meta_extra_key = "feed_label"  # 详情元信息首段 = 订阅显示名

    async def _fetch_merged(
        self, feeds: list[Subscription]
    ) -> tuple[list[FeedItem], list[str]]:
        return await fetch_merged(feeds)

    async def _open_one(self, feed: Subscription) -> FeedLoader:
        items = parse_feed(
            await fetch_body(feed),
            feed_key=feed.key,
            feed_label=feed.label,
            all_view=False,
            fallback_url=feed.url,
        )
        # 0 条 = 大概率源坏了（改版/地址失效），静态首屏出口统一报
        return self.static_feed(items, feed.label)

    def _detail_time(self, extra: dict[str, Any]) -> str:
        return fmt_unix(extra.get("published") or None)

    def _detail_body(self, extra: dict[str, Any]) -> str:
        return html_to_md(str(extra.get("content_html") or ""))
