"""HN（Hacker News）源（hn）：首页/最新/Ask/Show 四个信息流 + 讨论树详情。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：Algolia 公开索引 API（免 key 免登录、无登录态）
- parse.py  解析层：Algolia JSON -> FeedItem / 楼层与评论树，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派 + 内存分页 Loader

结构映射是知乎式三层：story 本体（正文 = 原文一行 + Ask/Show 自述）->
顶层评论当楼层（unit「评论」）-> 楼层内回复树当评论层（unit「回复」）。
items/:id 一次给全树，楼层与评论翻页都是内存切片——详情打开后评论层
零额外请求。无楼层引导（descendants=0 的楼层头不带「N 条回复」）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    CommentLoader,
    DetailLoader,
    FeedLoader,
    Source,
    SourceError,
    require_feed_items,
)
from feedscope.sources.hn.fetch import HnFeedLoader, fetch_item
from feedscope.sources.hn.parse import SOURCE_ID, StoryDetail, parse_story

# 详情一次拿全树后，楼层/评论每页给多少（内存切片，不是请求）：首批给
# 一屏上下的量，Ctrl+N 增量补，几百楼的线程不至于一次糊满屏
FLOOR_PAGE = 15
COMMENT_PAGE = 20


class HnDetailLoader(DetailLoader):
    """HN 详情加载器：树已全在内存——楼层翻页与评论层都是切片，零额外请求。"""

    def __init__(self, story: StoryDetail, fallback_title: str = "") -> None:
        self._floors = story.floors
        first = self._floors[:FLOOR_PAGE]
        self._given = len(first)
        self.doc = DetailDoc(
            title=story.title or fallback_title,
            meta=story.meta,
            time=story.time,
            description_md=story.description_md,
            answers=[f.answer for f in first],
        )

    @property
    def done(self) -> bool:
        return self._given >= len(self._floors)

    async def load_more(self) -> list[Answer]:
        if self.done:
            return []
        batch = self._floors[self._given : self._given + FLOOR_PAGE]
        self._given += len(batch)
        answers = [f.answer for f in batch]
        self.doc.answers.extend(answers)  # 「doc = 已加载全部」不变量
        return answers

    async def open_comments(self, index: int) -> HnCommentsLoader:
        if not 0 <= index < len(self.doc.answers):
            raise SourceError("HN 该评论不在已加载范围内，先按 Ctrl+N 加载更多楼层")
        return HnCommentsLoader(self._floors[index].comments)

    async def close(self) -> None:
        pass  # 树在内存，无资源


class HnCommentsLoader(CommentLoader):
    """楼层回复树加载器：整串已在内存，翻页是本地切片（Ctrl+N 零请求）。"""

    def __init__(self, comments: list[Comment]) -> None:
        self.comments = comments[:COMMENT_PAGE]
        self._rest = comments[COMMENT_PAGE:]

    @property
    def done(self) -> bool:
        return not self._rest

    async def load_more(self) -> list[Comment]:
        if self.done:
            return []
        batch, self._rest = self._rest[:COMMENT_PAGE], self._rest[COMMENT_PAGE:]
        self.comments.extend(batch)  # 「comments = 已加载全部」不变量
        return batch

    async def close(self) -> None:
        pass


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
        label = self.feed_labels.get(name)
        if label is None:
            raise ValueError(f"未知信息流：{name}")
        loader = HnFeedLoader(name, label)
        await loader.load_more()  # 首批
        require_feed_items(loader.items, self.label, label)  # 0 条 = 接口可能已变
        return loader

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        root = await fetch_item(item.id)
        if not root.get("id"):
            raise SourceError("HN 详情返回里没有 id，接口可能已变")
        return HnDetailLoader(parse_story(root), fallback_title=item.title)
