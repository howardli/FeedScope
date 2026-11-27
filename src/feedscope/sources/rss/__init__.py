"""RSS 源（rss）：通用订阅聚合——「全部」合并时间线 + 每订阅一个信息流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：httpx 条件请求（ETag/Last-Modified）+ 聚合流的并发编排
- parse.py  解析层：feedparser -> FeedItem / 详情 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（订阅型骨架走
  core.SubscribedSource，本文件只剩 RSS 的差异）+ 订阅管理三钩子

与 zhihu/x 的本质差异：feeds 不是代码写死的内置流，而是用户在 sources.toml
[rss.feeds] 里的订阅表（数据驱动的加法合成）；无登录、无评论层；详情读
feed 自带正文（content 优先、summary 兜底），摘要型源正文短，Ctrl+O 跳浏
览器补全。
"""

from __future__ import annotations

from typing import Any, ClassVar

from feedscope.core import config
from feedscope.core.config import Subscription
from feedscope.core.errors import SubscriptionError
from feedscope.core.i18n import t
from feedscope.core.loaders import FeedLoader
from feedscope.core.media import html_to_md, mark
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import SubscribedSource
from feedscope.core.timefmt import fmt_unix
from feedscope.sources.rss import fetch
from feedscope.sources.rss.parse import (
    SOURCE_ID,
    parse_enclosure_marks,
    parse_feed,
    parse_feed_discovery,
    parse_feed_info,
)


@register
class RssSource(SubscribedSource):
    source_id = SOURCE_ID
    label = "RSS"
    supports_detail = True
    subscribable = True  # feeds 来自用户订阅：F2 里 → 进订阅管理屏
    detail_kinds = frozenset({""})  # 全部条目（kind 空）Enter 都进终端详情
    detail_unit_label = "units.body"  # 无楼层，详情屏尾行与 probe 用这个词

    all_feed: ClassVar[str] = "all"  # 聚合流名（「全部」），feeds 首位
    sub_noun: ClassVar[str] = "units.subscription"
    reserved_feed_keys = (all_feed,)
    _meta_extra_key = "feed_label"  # 详情元信息首段 = 订阅显示名
    # 订阅管理：添加框收 feed 地址 / 站点首页 / OPML 文件路径（文案键见 subs.*）
    sub_placeholder = "subs.rss_placeholder"
    sub_hint = "subs.rss_hint"
    sub_empty = "subs.rss_empty"
    sub_added = "subs.added_feed"
    sub_imports_opml = True

    async def _fetch_merged(
        self, feeds: list[Subscription]
    ) -> tuple[list[FeedItem], list[str]]:
        return await fetch.fetch_merged(feeds)

    async def _open_one(self, feed: Subscription) -> FeedLoader:
        items = parse_feed(
            await fetch.fetch_body(feed),
            feed_key=feed.key,
            feed_label=feed.label,
            all_view=False,
            fallback_url=feed.url,
        )
        # 0 条 = 大概率源坏了（改版/地址失效），切片出口统一报；单个
        # feed 也可能几百条，切片上屏防一次性挂载冻结
        return self.sliced_feed(items, feed.label)

    def _detail_time(self, extra: dict[str, Any]) -> str:
        return fmt_unix(extra.get("published") or None)

    def _detail_body(self, extra: dict[str, Any]) -> str:
        # 正文里的相对地址以条目自己的页面为基准绝对化；enclosure（播客/附件）
        # 在正文之后追加记号链接（记号按当前语言解析，缓存里只存地址与 mime）
        md = html_to_md(str(extra.get("content_html") or ""), base=str(extra.get("base") or ""))
        marks = [mark(t(key), url) for key, url in parse_enclosure_marks(extra.get("enclosures"))]
        return "\n\n".join([md, *marks]) if (md or marks) else ""

    # ---- 订阅管理钩子（通用编排在 services/subscriptions；判定顺序与文案不变）----

    def subscription_input(
        self, text: str, *, key: str | None, existing: list[Subscription]
    ) -> Subscription:
        # 地址原样收（CLI 参数 / 输入框已 strip）：key 缺省按域名派生，避开
        # 保留字与已有 key（https://all.com/… 派生 all-2 而不是 all——撞上聚合
        # 流名的订阅会被读取侧静默跳过，加了等于没加）
        if key is None:
            key = config.derive_key(text, (*self.reserved_feed_keys, *(f.key for f in existing)))
        return Subscription(key=key, label="", url=text)

    def check_subscription(self, draft: Subscription, existing: list[Subscription]) -> None:
        if any(f.url == draft.url for f in existing):
            raise SubscriptionError(
                t("subs.dup_url", key=next(f.key for f in existing if f.url == draft.url))
            )
        if draft.key in self.reserved_feed_keys:
            raise SubscriptionError(t("subs.reserved_key", key=draft.key))
        if any(f.key == draft.key for f in existing):
            raise SubscriptionError(t("subs.key_taken", key=repr(draft.key)))

    async def probe_subscription(self, draft: Subscription) -> Subscription:
        body = await fetch.fetch_body_or_none(draft.url)
        if body is None:
            return draft  # 拉不到就按用户给的地址加（显示名回退 key）
        title, entries = parse_feed_info(body)
        if not title and not entries:
            # 拉回来不像 feed：当首页试自动发现（粘博客首页也能订上）
            alt = parse_feed_discovery(body.decode("utf-8", "ignore"), draft.url)
            if alt and alt != draft.url:
                # 改订发现的地址：新地址可能已订阅，查重对它再走一遍（key 沿用）
                moved = Subscription(key=draft.key, label="", url=alt)
                self.check_subscription(moved, config.feeds_for(self.source_id))
                return await self.probe_subscription(moved)
        return Subscription(key=draft.key, label=title, url=draft.url)
