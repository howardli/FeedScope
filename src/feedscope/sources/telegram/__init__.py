"""Telegram 频道源（telegram）：纯订阅聚合——「全部」合并时间线 + 每频道一流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：t.me/s 预览页（免登录 SSR HTML）+ 聚合流的并发编排
- parse.py  解析层：预览 HTML -> FeedItem / 正文 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（订阅型骨架走
  core.SubscribedSource，本文件只剩 Telegram 的差异）+ 订阅管理三钩子

与 RSS 完全同形（代码级近亲）：feeds 来自 [telegram.feeds] 订阅表（加法
合成），无登录、无评论层；详情读列表自带正文（extra 透传），进详情零请求，
摘要外没有的部分 Ctrl+O 跳浏览器。频道帖的正文是行内 HTML，走 html_to_md
共用管线；图片/视频换 [图片]/[视频] 记号。翻页走 ?before= 游标（单频道流）；
「全部」聚合流一次并发拉全、内存切片上屏（SliceFeedLoader）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from feedscope.core.config import Subscription
from feedscope.core.errors import SourceError, SubscriptionError
from feedscope.core.i18n import t
from feedscope.core.loaders import FeedLoader
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import SubscribedSource
from feedscope.core.timefmt import fmt_iso
from feedscope.sources.telegram import fetch
from feedscope.sources.telegram.fetch import TelegramFeedLoader, fetch_merged
from feedscope.sources.telegram.parse import (
    SOURCE_ID,
    parse_channel_html,
    parse_channel_title,
    parse_telegram_input,
)


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
    # 订阅管理：添加框收频道名或链接（文案键见 subs.*）
    sub_placeholder = "subs.telegram_placeholder"
    sub_hint = "subs.hint_basic"
    sub_empty = "subs.telegram_empty"
    sub_added = "subs.added_channel"

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
        return fmt_iso(extra.get("published") or None)

    def _detail_body(self, extra: dict[str, Any]) -> str:
        return str(extra.get("content_md") or "")

    # ---- 订阅管理钩子（通用编排在 services/subscriptions；判定顺序与文案不变）----

    def subscription_input(
        self, text: str, *, key: str | None, existing: list[Subscription]
    ) -> Subscription:
        # 四形态归一成裸频道名（parse_telegram_input：durov / @durov / t.me/durov / 帖子直链）
        name = parse_telegram_input(text)
        if not name:
            raise SubscriptionError(t("subs.channel_unrecognized"))
        return Subscription(key=name, label="", url=f"https://t.me/{name}/")

    def check_subscription(self, draft: Subscription, existing: list[Subscription]) -> None:
        if draft.key in self.reserved_feed_keys:
            # 防御分支：当前保留字 all 只有 3 字符，Telegram 用户名最短 5 字符，
            # 实际到不了这里（先在输入归一拦下）——留给保留字集日后扩展长词
            raise SubscriptionError(t("subs.channel_reserved", name=draft.key))
        if any(f.key == draft.key for f in existing):
            raise SubscriptionError(t("subs.channel_dup", name=draft.key))

    async def probe_subscription(self, draft: Subscription) -> Subscription:
        # SourceError 顺势上抛（HTTP 层的中文消息够用，订阅管理屏按意外失败展示）
        html = await fetch.fetch_channel_html(draft.key)
        if not parse_channel_html(html, channel=draft.key, label=draft.key):
            raise SubscriptionError(
                t("subs.channel_no_items", name=draft.key)
            )  # 不存在 / 为空 / 站长关了 Web 预览：给专属指引而不是误报改版
        return Subscription(key=draft.key, label=parse_channel_title(html), url=draft.url)
