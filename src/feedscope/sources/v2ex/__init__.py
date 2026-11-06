"""V2EX 源（v2ex）：热门/最新两个信息流 + 主题详情（正文与回复楼层）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：官方 v1 公开 API（免 token 免登录、无登录态）
- parse.py  解析层：v1 JSON -> FeedItem / 主题详情，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派（楼层翻页走 core 的
  内存切片基类）

结构映射是 X 式两层：topic 本体 -> 回复平铺当楼层（unit「回复」）；V2EX
回复没有嵌套结构，无评论层（supports_comments=False，Enter 键位预告按此
退场）。回复一次全量已在内存，楼层翻页是切片——详情打开后零额外请求；
正文不走网络（列表透传，见 parse）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    SliceDetailLoader,
    Source,
    SourceError,
    StaticFeedLoader,
    require_feed_items,
)
from feedscope.sources.v2ex.fetch import fetch_replies, fetch_topics
from feedscope.sources.v2ex.parse import SOURCE_ID, TopicDetail, parse_detail, parse_topics

# 回复全量已在内存后的每页楼层数（内存切片，不是请求）：首批给一屏上下
# 的量，Ctrl+N 增量补，几百楼的帖子不至于一次糊满屏（与 HN 楼层同宽）
REPLY_PAGE = 15


class V2exDetailLoader(SliceDetailLoader):
    """V2EX 详情加载器：回复已全在内存——楼层翻页是切片，零额外请求。"""

    source_label = "V2EX"
    floor_page = REPLY_PAGE

    def __init__(self, topic: TopicDetail, fallback_title: str = "") -> None:
        super().__init__(
            topic.replies,
            title=topic.title or fallback_title,
            meta=topic.meta,
            time=topic.time,
            description_md=topic.description_md,
        )


@register
class V2exSource(Source):
    source_id = SOURCE_ID
    label = "V2EX"
    supports_detail = True
    supports_comments = False  # 回复平铺无嵌套（楼中楼只是 @ 文字），详情到楼层为止
    feeds: ClassVar[list[str]] = ["hot", "new"]
    feed_labels: ClassVar[dict[str, str]] = {"hot": "热门", "new": "最新"}
    detail_kinds = frozenset({"topic"})  # 全部条目 Enter 都进终端详情
    detail_unit_label = "回复"  # 楼层单位词：帖内回复（知乎「回答」、HN「评论」）

    async def open_feed(self, name: str) -> StaticFeedLoader:
        label = self.label_of(name)
        items = parse_topics(await fetch_topics(name, label))
        require_feed_items(items, self.label, label)  # 0 条 = 接口可能已变
        return StaticFeedLoader(items)  # v1 一页全量（hot 10 / latest 40），无翻页

    async def open_detail(self, item: FeedItem) -> SliceDetailLoader:
        # 正文随列表透传（列表端点 content 即全量）；缺上下文的旧条目（如
        # 跨进程恢复的列表）回列表重进即可，不值得为此多打一个端点
        topic = item.extra.get("topic")
        if not isinstance(topic, dict) or not topic:
            raise SourceError("V2EX 详情缺少列表上下文，回到列表重进该主题")
        replies = await fetch_replies(item.id)
        return V2exDetailLoader(parse_detail(topic, replies), fallback_title=item.title)
