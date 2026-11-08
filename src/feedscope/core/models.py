"""核心数据模型：FeedItem 是「源 -> UI」的唯一契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeedItem:
    """列表级条目：所有源都产出它，UI 只消费它。

    extra 的键约定（列表传给详情抓取的上下文，属源内私有约定，UI 不读取）：
    - x：conversation_id（转发详情跳原推会话）
    - v2ex：topic（列表端点正文即全量，详情只再拉回复）
    - reddit：subreddit（详情 meta 拼装用）
    - rss：feed_key / feed_label / author / published / content_html（详情读回正文）
    - telegram：channel / author / published / content_md（详情读回正文）
    - zhihu：不使用
    """

    source: str                        # 源 id，如 "zhihu"
    id: str                            # 源内稳定 id（url token、hn id 等）
    title: str
    url: str
    kind: str = ""                     # 内容类型；语义由源定义（Source.kind_labels / detail_kinds）
    meta: str = ""                     # 列表元信息列语义文本（热度/作者/节点名…），UI 统一截断对齐
    tag: str = ""                      # 源名列：跨流混排（聚合流/收藏屏）标出处（订阅名/频道名）；
                                       # 单源流为空。显示数据由解析层产出——UI 不读 extra 的约定不变
    extra: dict[str, Any] = field(default_factory=dict)  # 传给 detail 抓取的补充字段

    def to_dict(self) -> dict[str, Any]:
        """列表缓存的序列化形态（core.feed_cache 落盘用）：字段平铺，extra 原样。

        extra 的值约定全是 JSON 类型（各源 parse 产自 JSON/HTML 解析结果），
        不做逐值清洗——坏类型让缓存文件的 load 侧整体丢弃，不值得逐键防御。
        """
        return {
            "source": self.source,
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "kind": self.kind,
            "meta": self.meta,
            "tag": self.tag,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedItem:
        """to_dict 的逆（core.feed_cache 读回用）；缺字段 = 缺省值，未知键忽略。

        旧版缓存少字段/手编多字段都不炸：缓存是派生数据，宽容读回优于版本迁移。
        """
        return cls(
            source=str(data.get("source", "")),
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            kind=str(data.get("kind", "")),
            meta=str(data.get("meta", "")),
            tag=str(data.get("tag", "")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class Comment:
    """一条评论（楼层内的讨论；知乎 / HN / Reddit 共用）。

    author / votes / content_md / time 是展示字段；replies 是拍平后的子回复
    （@父作者规则见 core.tree）；raw_id 是源内评论 id（翻页去重）。
    """

    author: str
    votes: str                         # 展示文本，如 "赞 3"；无赞为空
    content_md: str
    time: str = ""
    replies: list[Comment] = field(default_factory=list)
    raw_id: str = ""


@dataclass
class Answer:
    """一条楼层内容（知乎回答 / X 回复 / HN·Reddit 评论 / V2EX 回复）。

    author 为展示名：知乎是昵称；X 是署名「名字 @handle」（名字与账号同显，
    见 sources/x/parse.py 的 _who）。评论是更深一层，不随楼层首批抓取
    （按需经 DetailLoader.open_comments 进评论层），这里只带 comment_count
    引导（如 "158 条评论" / "4 条回复"，缺省空）。
    """

    author: str
    votes: str                         # 展示文本，如 "赞同 148"
    content_md: str
    time: str = ""                     # 发布时间展示文本，如 "2026-10-19 14:32"（源尽力而为）
    url: str = ""                      # 楼层直达链接；空 = 用父条目 url
    comment_count: str = ""            # 展示文本，如 "158 条评论"（缺省空）


@dataclass
class Floor:
    """一个楼层（顶层评论/回答）：本体 + 它的回复树（已是 Comment 列表）。

    「详情一次拉全树」的源（HN / Reddit）的 parse 产物单元，楼层切片
    Loader（core.source.SliceDetailLoader 的调用方）按 answer 建楼层、
    按下标切 comments 进评论层。
    """

    answer: Answer
    comments: list[Comment] = field(default_factory=list)


@dataclass
class DetailDoc:
    """详情级文档：open_detail() 的首批内容，更多靠 loader.load_more() 增量拿。"""

    title: str
    meta: str = ""                     # 一行元信息（如 "30 回答 · 12 万浏览"）
    time: str = ""                     # 正文发布时间展示文本（源尽力而为，缺省空）
    description_md: str = ""           # 问题描述（markdown）
    answers: list[Answer] = field(default_factory=list)
