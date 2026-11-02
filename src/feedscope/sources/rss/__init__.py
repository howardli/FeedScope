"""RSS 源（rss）：通用订阅聚合——「全部」合并时间线 + 每订阅一个信息流。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：httpx 条件请求（ETag/Last-Modified）+ 聚合流的并发编排
- parse.py  解析层：feedparser -> FeedItem / 详情 markdown，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派

与 zhihu/x 的本质差异：feeds 不是代码写死的内置流，而是用户在 sources.toml
[rss.feeds] 里的订阅表（数据驱动的加法合成）；无登录、无评论层；详情读
feed 自带正文（content 优先、summary 兜底），摘要型源正文短，Ctrl+O 跳浏
览器补全。
"""

from __future__ import annotations

import logging

from feedscope.core import config
from feedscope.core.media import html_to_md
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
from feedscope.core.timefmt import fmt_unix
from feedscope.sources.rss.fetch import fetch_body, fetch_merged
from feedscope.sources.rss.parse import SOURCE_ID, parse_feed

logger = logging.getLogger(__name__)


class RssDetailLoader(DetailLoader):
    """feed 自带全文的详情加载器：无楼层可翻、无资源可释，一次性到头。"""

    def __init__(self, doc: DetailDoc) -> None:
        self.doc = doc
        self.done = True

    async def load_more(self) -> list[Answer]:
        return []

    async def close(self) -> None:
        pass


@register
class RssSource(Source):
    source_id = SOURCE_ID
    label = "RSS"
    supports_detail = True
    subscribable = True  # feeds 来自用户订阅：F2 里 Enter 进订阅管理屏
    detail_kinds = frozenset({""})  # 全部条目（kind 空）Enter 都进终端详情
    detail_unit_label = "正文"  # 无楼层，详情屏尾行与 probe 用这个词

    ALL_FEED = config.RSS_ALL_FEED  # 聚合流名（「全部」），feeds 首位

    def __init__(self) -> None:
        """feeds 合成：配置订阅表（加法）而非类默认流剔除 hide（减法）。

        不调 super().__init__()——它的合成规则对类默认流做减法，这里 feeds
        完全来自用户数据：[rss.feeds] 的订阅 key 各成一个流，key 非空时再
        前置聚合流「全部」（hide 可分别隐藏「全部」或单个订阅）。零订阅 =
        没有任何可选信息流（F2/首启引导不列出，配置订阅后再启用）。
        """
        feeds_cfg = config.rss_feeds()
        hidden = set(config.hidden_feeds(self.source_id))
        self._by_key = {f.key: f for f in feeds_cfg if f.key not in hidden}
        keys = list(self._by_key)
        self.feeds = ([self.ALL_FEED] if keys and self.ALL_FEED not in hidden else []) + keys
        self.feed_labels = (
            {self.ALL_FEED: "全部", **{k: f.label for k, f in self._by_key.items()}}
            if self.feeds
            else {}
        )
        if not keys:
            logger.info("RSS 暂无订阅：在 sources.toml 的 [rss.feeds] 配置后，F2 勾选启用")

    async def open_feed(self, name: str) -> FeedLoader:
        if name == self.ALL_FEED:
            items, errors = await fetch_merged(list(self._by_key.values()))
            if errors and items:  # 部分失败降级：时间线照出，失败原因进日志
                logger.warning(
                    "RSS「全部」%d/%d 个订阅拉取失败：%s", len(errors), len(self._by_key), "；".join(errors[:3])
                )
            if not items and errors:
                raise SourceError(
                    "RSS「全部」订阅全部拉取失败：" + "；".join(errors[:3]) + f"（共 {len(errors)} 个）"
                )
            items = require_feed_items(items, self.label, "全部")  # 全空且无错 = 可能改版
            return StaticFeedLoader(items)
        feed = self._by_key.get(name)
        if feed is None:
            raise ValueError(f"未知信息流：{name}")
        items = parse_feed(
            await fetch_body(feed),
            feed_key=feed.key,
            feed_label=feed.label,
            all_view=False,
            fallback_url=feed.url,
        )
        items = require_feed_items(items, self.label, feed.label)  # 0 条 = 大概率源坏了
        return StaticFeedLoader(items)

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        """正文已在列表抓取时带回来（extra），进详情不重新请求。"""
        extra = item.extra
        meta = " · ".join(
            x for x in (str(extra.get("feed_label") or ""), str(extra.get("author") or "")) if x
        )
        doc = DetailDoc(
            title=item.title,
            meta=meta,
            time=fmt_unix(extra.get("published") or None),
            description_md=html_to_md(str(extra.get("content_html") or "")),
        )
        return RssDetailLoader(doc)
