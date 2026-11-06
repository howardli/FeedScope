"""HN（Hacker News）源（hn）：首页/最新/Ask/Show 四个信息流 + 讨论树详情。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：Algolia 公开索引 API（免 key 免登录、无登录态）
- parse.py  解析层：Algolia JSON -> FeedItem / 楼层与评论树，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（楼层/评论翻页走
  core 的内存切片基类）

结构映射是知乎式三层：story 本体（正文 = 原文一行 + Ask/Show 自述）->
顶层评论当楼层（unit「评论」）-> 楼层内回复树当评论层（unit「回复」）。
items/:id 一次给全树，楼层与评论翻页都是内存切片——详情打开后评论层
零额外请求。无楼层引导（descendants=0 的楼层头不带「N 条回复」）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    FeedLoader,
    SliceDetailLoader,
    Source,
    SourceError,
)
from feedscope.sources.hn.fetch import HnFeedLoader, fetch_item
from feedscope.sources.hn.parse import SOURCE_ID, parse_story


class HnDetailLoader(SliceDetailLoader):
    """HN 详情加载器：树已全在内存——楼层翻页与评论层都是切片，零额外请求。

    全部结构在 SliceDetailLoader.from_detail（楼层/评论树成对进切片），
    本类只声明报错文案里的源标签（楼层页宽用基类默认）。"""

    source_label = "HN"


@register
class HnSource(Source):
    source_id = SOURCE_ID
    label = "HN"
    supports_detail = True
    supports_comments = True  # 详情屏 Enter 进楼层的回复树（树已全在内存）
    feeds: ClassVar[list[str]] = ["front", "new", "ask", "show"]
    # ask/show = Ask HN（向社区提问）/ Show HN（展示自己的项目），HN 官方两大
    # 经典分类；标签与其余源同款两字中文（列表里帖子标题仍带 Ask/Show HN 前缀）
    feed_labels: ClassVar[dict[str, str]] = {
        "front": "首页", "new": "最新", "ask": "提问", "show": "展示",
    }
    detail_kinds = frozenset({"story", "ask", "show"})  # 全部条目 Enter 都进终端详情
    detail_unit_label = "评论"  # 楼层单位词：顶层评论串（知乎「回答」、X「回复」）
    comment_unit_label = "回复"  # 楼层内的讨论（知乎叫「评论」）

    async def open_feed(self, name: str) -> FeedLoader:
        label = self.label_of(name)
        return await self.first_batch(HnFeedLoader(name, label), label)

    async def open_detail(self, item: FeedItem) -> SliceDetailLoader:
        root = await fetch_item(item.id)
        if not root.get("id"):
            raise SourceError("HN 详情返回里没有 id，接口可能已变")
        return HnDetailLoader.from_detail(parse_story(root), fallback_title=item.title)
