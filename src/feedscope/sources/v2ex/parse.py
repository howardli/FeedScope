"""解析层：v1 JSON -> FeedItem / 主题详情，纯函数不碰网络。

结构映射是 X 式两层（词汇重映射，UI 零特判）：topic 本体（正文 =
content_rendered 转 markdown，自动内联图片换 [图片] 记号）-> 回复平铺
当楼层（unit「回复」）。V2EX 回复没有嵌套字段——「楼中楼」只是正文里的
@ 文字引用，还原不出树，故无评论层（supports_comments=False）。

v1 列表端点的 content/content_rendered 是全量正文（实测无截断），列表
条目把整个 topic dict 藏进 extra 透传给详情——打开详情只需再拉回复。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from feedscope.core.media import html_to_md
from feedscope.core.models import Answer, FeedItem
from feedscope.core.timefmt import fmt_unix

SOURCE_ID = "v2ex"

_TOPIC_URL = "https://www.v2ex.com/t/"

# 相对 href（@ 提及 /member/xxx、站内帖 /t/xxx）：TUI 点开要完整 URL，先绝对化；
# // 开头的协议相对地址（CDN）不碰
_REL_HREF = re.compile(r"""href=(["'])/((?!/)[^"']*)\1""")


def _md(html: object) -> str:
    """content_rendered HTML -> markdown：相对链接绝对化后走共用 img 记号管线。"""
    if not isinstance(html, str) or not html:
        return ""
    return html_to_md(_REL_HREF.sub(r"href=\1https://www.v2ex.com/\2\1", html))


def _username(member: object) -> str:
    return str(member.get("username") or "") if isinstance(member, dict) else ""


def _meta(topic: dict) -> str:
    """一行元信息：节点 · 回复数 · 作者（节点即板块，放最前好扫读）。"""
    parts: list[str] = []
    node = topic.get("node")
    if title := str(node.get("title") or "") if isinstance(node, dict) else "":
        parts.append(title)
    if replies := topic.get("replies"):
        try:
            parts.append(f"{int(replies)} 回复")
        except (TypeError, ValueError):
            pass
    if author := _username(topic.get("member")):
        parts.append(author)
    return " · ".join(parts)


def parse_topics(topics: list[dict]) -> list[FeedItem]:
    """列表端点 -> FeedItem 列表（保持返回序，野数据逐条防御）。

    整个 topic dict 进 extra：列表正文就是全量，详情打开只再拉回复一个请求。
    """
    items: list[FeedItem] = []
    for t in topics:
        tid = str(t.get("id") or "")
        if not tid.isdigit():
            continue  # 无 id 无法寻址，跳条不跳流
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=tid,
                title=str(t.get("title") or "").strip() or "（无标题）",
                url=f"{_TOPIC_URL}{tid}",
                kind="topic",
                meta=_meta(t),
                extra={"topic": t},
            )
        )
    return items


@dataclass
class TopicDetail:
    """主题详情的解析结果：本体 + 全部回复楼层（平铺，无评论层）。"""

    title: str
    meta: str
    time: str
    description_md: str
    replies: list[Answer] = field(default_factory=list)


def parse_detail(topic: dict, replies: list[dict]) -> TopicDetail:
    """topic（列表透传）+ 全量回复 -> TopicDetail（楼层 = 回复平铺直出）。"""
    floors: list[Answer] = []
    for r in replies:
        content = _md(r.get("content_rendered"))
        if not content:
            continue  # 空壳回复（被移除/占位），跳条不跳流
        rid = str(r.get("id") or "")
        topic_id = str(r.get("topic_id") or "")
        floors.append(
            Answer(
                author=_username(r.get("member")),
                votes="",  # v1 回复不带赞数
                content_md=content,
                time=fmt_unix(r.get("created")),
                # 楼层直达 = 原帖页的 reply 锚点（V2EX 的楼层 permalink 形态）
                url=f"{_TOPIC_URL}{topic_id}#reply{rid}" if rid else "",
            )
        )
    return TopicDetail(
        title=str(topic.get("title") or "").strip(),
        meta=_meta(topic),
        time=fmt_unix(topic.get("created")),
        description_md=_md(topic.get("content_rendered")),
        replies=floors,
    )
