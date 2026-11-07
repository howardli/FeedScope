"""解析层：Algolia JSON -> FeedItem / 楼层与评论树，纯函数不碰网络。

HN 的结构映射是知乎式三层（词汇重映射，UI 零特判）：
- 列表 search hit -> FeedItem（objectID 即条目 id，tag 分 kind）
- items/:id 根 -> story 本体（链接帖正文 = 原文一行；Ask/Show 自述全文）
- 顶层评论 -> 楼层（Answer，unit「评论」）；楼层的回复树 -> 评论层
  （Comment，unit「回复」）。Comment 模型只有两层而 HN 常嵌四五层：
  直接子回复做顶层，更深的拍平进 replies；隔层的回复加「@父作者」
  前缀保住「在回复谁」的指向（紧贴楼上的不加，同知乎观感）。整棵树
  一次已在内存，翻页是切片不是请求（见 __init__ 的内存 Loader）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from feedscope.core.const import safe_int
from feedscope.core.display import fmt_count
from feedscope.core.i18n import t
from feedscope.core.media import text_to_md
from feedscope.core.models import Answer, Comment, FeedItem, Floor
from feedscope.core.timefmt import fmt_iso
from feedscope.core.tree import (
    deleted_author,
    deleted_body,
    TreeAccessors,
    build_floors,
    count_kept,
)

SOURCE_ID = "hn"

# HN 原生条目页：自帖的兜底 url 与楼层直达链接（评论 permalink 也是 item 页）
_ITEM_URL = "https://news.ycombinator.com/item?id="


def _md(html: object) -> str:
    """HN 受限 HTML（p/a/i/code）-> markdown；非 str / 空回空串（管线见 core.media）。"""
    return text_to_md(html) if isinstance(html, str) else ""


def _kids(node: dict) -> list[dict]:
    kids = node.get("children")
    return [c for c in kids if isinstance(c, dict)] if isinstance(kids, list) else []


def _keep(node: dict) -> bool:
    """空壳节点（无正文也无孩子，典型是被删且无回复的评论）整支跳过。"""
    return bool(_md(node.get("text")) or _kids(node))


def _author(node: dict) -> str:
    return str(node.get("author") or "") or deleted_author()


def _time(node: dict) -> str:
    created = node.get("created_at")
    return fmt_iso(created if isinstance(created, str) else None)


def _comment_of(node: dict) -> Comment:
    """Algolia 节点 -> Comment 本体（@ 前缀由 core.tree 统一拼装）。"""
    return Comment(
        author=_author(node),
        votes="",  # Algolia 树节点不带分值，HN 评论也无赞数语义
        content_md=_md(node.get("text")) or deleted_body(),
        time=_time(node),
        raw_id=str(node.get("id") or ""),
    )


def _answer_of(node: dict, n: int) -> Answer:
    """楼层本体（core.tree.build_floors 注入）：字段来源是站差异，n 为回复数。"""
    cid = str(node.get("id") or "")
    return Answer(
        author=_author(node),
        votes="",
        content_md=_md(node.get("text")) or deleted_body(),
        time=_time(node),
        url=f"{_ITEM_URL}{cid}" if cid else "",
        comment_count=t("display.replies_n", n=n) if n else "",  # 0 条不引导 Enter
    )


# 差异注入：HN 的树是 Algolia dict（结构见各 accessor；拍平骨架在 core.tree）
_ACC = TreeAccessors(keep=_keep, kids_of=_kids, author_of=_author, comment_of=_comment_of)


def _meta(points: int, comments: int, author: str) -> str:
    """元信息列「N 点 · N 评 · 作者」：列表 hit 与详情 root 共用。"""
    parts = [
        t("words.points", n=fmt_count(points)),
        t("words.comments_short", n=fmt_count(comments)),
    ]
    if author:
        parts.append(author)
    return " · ".join(parts)


def parse_search(hits: list[dict]) -> list[FeedItem]:
    """search hits -> FeedItem 列表（保持返回序，野数据逐条防御）。"""
    items: list[FeedItem] = []
    for hit in hits:
        oid = str(hit.get("objectID") or "")
        if not oid.isdigit():
            continue  # 无 id 无法寻址，跳条不跳流
        tags = hit.get("_tags")
        tags = tags if isinstance(tags, list) else []
        kind = "ask" if "ask_hn" in tags else "show" if "show_hn" in tags else "story"
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=oid,
                title=str(hit.get("title") or "").strip() or t("words.no_title"),
                url=str(hit.get("url") or "") or f"{_ITEM_URL}{oid}",  # 自帖无外链，兜底条目页
                kind=kind,
                meta=_meta(
                    safe_int(hit.get("points")), safe_int(hit.get("num_comments")),
                    str(hit.get("author") or ""),
                ),
            )
        )
    return items


@dataclass
class StoryDetail:
    """items/:id 的解析结果：story 本体 + 全部楼层（含各自的回复树）。"""

    title: str
    meta: str
    time: str
    description_md: str
    floors: list[Floor] = field(default_factory=list)


def parse_story(root: dict) -> StoryDetail:
    """items/:id 根 -> StoryDetail（story 本体 + 顶层评论楼层与各自回复树）。"""
    blocks = []
    if url := str(root.get("url") or ""):
        blocks.append(f"{t('words.original')}({url})")  # 链接帖正文就是外链一行，Ctrl+O 直达
    if text_md := _md(root.get("text")):
        blocks.append(text_md)  # Ask/Show 的自述全文
    # 顶层节点 -> 楼层（keep 过滤 / 计数 / 拍平的骨架在 core.tree.build_floors）
    return StoryDetail(
        title=str(root.get("title") or "").strip(),
        meta=_meta(
            safe_int(root.get("points")), count_kept(root, _ACC),
            str(root.get("author") or ""),
        ),
        time=_time(root),
        description_md="\n\n".join(blocks),
        floors=build_floors(_kids(root), _ACC, answer_of=_answer_of),
    )
