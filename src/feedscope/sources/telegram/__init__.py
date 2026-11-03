"""Telegram 频道源（telegram）：纯订阅聚合——「全部」合并时间线 + 每频道一流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：t.me/s 预览页（免登录 SSR HTML）+ 聚合流的并发编排
- parse.py  解析层：预览 HTML -> FeedItem / 正文 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派

与 RSS 完全同形（代码级近亲）：feeds 来自 [telegram.feeds] 订阅表（加法
合成），无登录、无评论层；详情读列表自带正文（extra 透传），进详情零请求，
摘要外没有的部分 Ctrl+O 跳浏览器。频道帖的正文是行内 HTML，走 html_to_md
共用管线；图片/视频换 [图片]/[视频] 记号。翻页走 ?before= 游标（单频道流）；
「全部」聚合流一次并发拉全、StaticFeedLoader 一次到头。
"""

from __future__ import annotations

import logging

from feedscope.core import config
from feedscope.core.models import Answer, DetailDoc, FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    Source,
    SourceError,
    StaticFeedLoader,
    require_feed_items,
)
from feedscope.sources.telegram.fetch import TelegramFeedLoader, fetch_merged
from feedscope.sources.telegram.parse import SOURCE_ID, display_time

logger = logging.getLogger(__name__)


class TelegramDetailLoader(DetailLoader):
    """列表自带正文的详情加载器：无楼层可翻、无资源可释，一次性到头（同 RSS）。"""

    def __init__(self, doc: DetailDoc) -> None:
        self.doc = doc
        self.done = True

    async def load_more(self) -> list[Answer]:
        return []

    async def close(self) -> None:
        pass


@register
class TelegramSource(Source):
    source_id = SOURCE_ID
    label = "Telegram"
    supports_detail = True
    subscribable = True  # feeds 来自用户订阅：F2 里 → 进订阅管理屏
    supports_comments = False  # 频道无评论层（讨论组在另一维度，预览页拿不到）
    detail_kinds = frozenset({""})  # 全部条目（kind 空）Enter 都进终端详情
    detail_unit_label = "正文"  # 无楼层，详情屏尾行与 probe 用这个词（同 RSS）

    ALL_FEED = "all"  # 聚合流名（「全部」），保留字：订阅 key 与它同名会遮住聚合流

    def __init__(self) -> None:
        """feeds 合成（加法，与 RSS 同形）：[telegram.feeds] 的 key 各成一个流，
        key 非空时前置聚合流「全部」（hide 可分别隐藏「全部」或单个频道）。
        零订阅 = 没有任何可选信息流（F2/首启引导不列出，配置订阅后再启用）。
        """
        feeds_cfg = config.feeds_for(self.source_id)
        hidden = set(config.hidden_feeds(self.source_id))
        self._by_key = {f.key: f for f in feeds_cfg if f.key not in hidden and f.key != self.ALL_FEED}
        keys = list(self._by_key)
        self.feeds = ([self.ALL_FEED] if keys and self.ALL_FEED not in hidden else []) + keys
        self.feed_labels = (
            {self.ALL_FEED: "全部", **{k: f.label for k, f in self._by_key.items()}}
            if self.feeds
            else {}
        )
        if not keys:
            logger.info("Telegram 暂无订阅：在 sources.toml 的 [telegram.feeds] 配置后，F2 勾选启用")

    async def open_feed(self, name: str) -> FeedLoader:
        if name == self.ALL_FEED:
            if not self._by_key:
                # 订阅刚被删光的过渡窗口：旧实例还挂着「全部」流，别让空表
                # 走到 0 条守卫报「站点可能已改版」——那是误导，指引才是实话
                raise SourceError("Telegram 还没有订阅——F2 里选中 Telegram 按 → 添加频道")
            items, errors = await fetch_merged(list(self._by_key.values()))
            if errors and items:  # 部分失败降级：时间线照出，失败原因进日志
                logger.warning(
                    "Telegram「全部」%d/%d 个频道拉取失败：%s",
                    len(errors), len(self._by_key), "；".join(errors[:3]),
                )
            if not items and errors:
                raise SourceError(
                    "Telegram「全部」订阅全部拉取失败：" + "；".join(errors[:3]) + f"（共 {len(errors)} 个）"
                )
            items = require_feed_items(items, self.label, "全部")  # 全空且无错 = 可能改版
            return StaticFeedLoader(items)
        feed = self._by_key.get(name)
        if feed is None:
            raise ValueError(f"未知信息流：{name}")
        loader = TelegramFeedLoader(feed.key, feed.label)
        await loader.load_more()  # 首批
        # 0 条大概率是频道不存在/为空/关闭了 Web 预览，不是站点改版——给专属指引
        if not loader.items:
            loader.done = True
            raise SourceError(
                f"Telegram @{feed.key} 一条也拉不到：频道可能不存在、为空，"
                "或站长关闭了 Web 预览（t.me/s/ 页面需要站长开启）"
            )
        return loader

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        """正文已在列表抓取时带回来（extra），进详情不重新请求（同 RSS）。"""
        extra = item.extra
        meta = " · ".join(
            x for x in (str(extra.get("channel") or ""), str(extra.get("author") or "")) if x
        )
        doc = DetailDoc(
            title=item.title,
            meta=meta,
            time=display_time(str(extra.get("published") or "")),
            description_md=str(extra.get("content_md") or ""),
        )
        return TelegramDetailLoader(doc)
