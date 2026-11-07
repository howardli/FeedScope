"""Telegram 频道源（telegram）：纯订阅聚合——「全部」合并时间线 + 每频道一流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：t.me/s 预览页（免登录 SSR HTML）+ 聚合流的并发编排
- parse.py  解析层：预览 HTML -> FeedItem / 正文 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（订阅型骨架走
  core.SubscribedSource，本文件只剩 Telegram 的差异）

与 RSS 完全同形（代码级近亲）：feeds 来自 [telegram.feeds] 订阅表（加法
合成），无登录、无评论层；详情读列表自带正文（extra 透传），进详情零请求，
摘要外没有的部分 Ctrl+O 跳浏览器。频道帖的正文是行内 HTML，走 html_to_md
共用管线；图片/视频换 [图片]/[视频] 记号。翻页走 ?before= 游标（单频道流）；
「全部」聚合流一次并发拉全、内存切片上屏（SliceFeedLoader）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from feedscope.core.config import Subscription
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import FeedLoader, SourceError, SubscribedSource
from feedscope.sources.telegram.fetch import TelegramFeedLoader, fetch_merged
from feedscope.sources.telegram.parse import SOURCE_ID, display_time


@register
class TelegramSource(SubscribedSource):
    source_id = SOURCE_ID
    label = "Telegram"
    supports_detail = True
    subscribable = True  # feeds 来自用户订阅：F2 里 → 进订阅管理屏
    supports_comments = False  # 频道无评论层（讨论组在另一维度，预览页拿不到）
    detail_kinds = frozenset({""})  # 全部条目（kind 空）Enter 都进终端详情
    detail_unit_label = "units.body"  # 无楼层，详情屏尾行与 probe 用这个词（同 RSS）

    all_feed: ClassVar[str] = "all"  # 聚合流名（「全部」），保留字：订阅 key 与它同名会遮住聚合流
    sub_noun: ClassVar[str] = "units.channel"
    reserved_feed_keys = (all_feed,)
    _meta_extra_key = "channel"  # 详情元信息首段 = 频道名

    async def _fetch_merged(
        self, feeds: list[Subscription]
    ) -> tuple[list[FeedItem], list[str]]:
        return await fetch_merged(feeds)

    async def _open_one(self, feed: Subscription) -> FeedLoader:
        loader = TelegramFeedLoader(feed.key, feed.label)
        await loader.load_more()  # 首批
        # 0 条大概率是频道不存在/为空/关闭了 Web 预览，不是站点改版——给专属指引
        if not loader.items:
            raise SourceError(t("errors.telegram_no_items", key=feed.key))
        return loader

    def _detail_time(self, extra: dict[str, Any]) -> str:
        return display_time(str(extra.get("published") or ""))

    def _detail_body(self, extra: dict[str, Any]) -> str:
        return str(extra.get("content_md") or "")
