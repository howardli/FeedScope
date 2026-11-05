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

from feedscope.core.const import new_h2t, safe_int
from feedscope.core.display import fmt_count
from feedscope.core.models import Answer, Comment, FeedItem, Floor
from feedscope.core.text import strip_zwsp
from feedscope.core.timefmt import fmt_iso
from feedscope.core.tree import DELETED_AUTHOR, DELETED_BODY, count_kept, floor_comments

SOURCE_ID = "hn"

# HN 原生条目页：自帖的兜底 url 与楼层直达链接（评论 permalink 也是 item 页）
_ITEM_URL = "https://news.ycombinator.com/item?id="


_H2T = new_h2t()  # 模块级实例复用（handle 自带状态复位，见 zhihu/parse 同款）


def _md(html: object) -> str:
    """HN 受限 HTML（p/a/i/code/pre）-> markdown；非 str / 空回空串。"""
    if not isinstance(html, str) or not html:
        return ""
    return strip_zwsp(_H2T.handle(html)).strip()


def _kids(node: dict) -> list[dict]:
    kids = node.get("children")
    return [c for c in kids if isinstance(c, dict)] if isinstance(kids, list) else []


def _keep(node: dict) -> bool:
    """空壳节点（无正文也无孩子，典型是被删且无回复的评论）整支跳过。"""
    return bool(_md(node.get("text")) or _kids(node))


def _author(node: dict) -> str:
    return str(node.get("author") or "") or DELETED_AUTHOR


def _time(node: dict) -> str:
    created = node.get("created_at")
    return fmt_iso(created if isinstance(created, str) else None)


def _comment_of(node: dict) -> Comment:
    """Algolia 节点 -> Comment 本体（@ 前缀由 core.tree 统一拼装）。"""
    return Comment(
        author=_author(node),
        votes="",  # Algolia 树节点不带分值，HN 评论也无赞数语义
        content_md=_md(node.get("text")) or DELETED_BODY,
        time=_time(node),
        raw_id=str(node.get("id") or ""),
    )


# 差异注入：HN 的树是 Algolia dict（结构见各 accessor）
_ACC = {"keep": _keep, "kids_of": _kids, "author_of": _author, "comment_of": _comment_of}
_COUNT = {"keep": _keep, "kids_of": _kids}  # count_kept 只要这两个


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
        parts = [f"{fmt_count(safe_int(hit.get('points')))} 点",
                 f"{fmt_count(safe_int(hit.get('num_comments')))} 评"]
        if author := str(hit.get("author") or ""):
            parts.append(author)
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=oid,
                title=str(hit.get("title") or "").strip() or "（无标题）",
                url=str(hit.get("url") or "") or f"{_ITEM_URL}{oid}",  # 自帖无外链，兜底条目页
                kind=kind,
                meta=" · ".join(parts),
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
    parts = [f"{fmt_count(safe_int(root.get('points')))} 点", f"{count_kept(root, **_COUNT)} 评"]
    if author := str(root.get("author") or ""):
        parts.append(author)
    blocks = []
    if url := str(root.get("url") or ""):
        blocks.append(f"[原文]({url})")  # 链接帖正文就是外链一行，Ctrl+O 直达
    if text_md := _md(root.get("text")):
        blocks.append(text_md)  # Ask/Show 的自述全文
    floors = []
    for child in _kids(root):
        if not _keep(child):
            continue
        n = count_kept(child, **_COUNT)
        cid = str(child.get("id") or "")
        floors.append(
            Floor(
                answer=Answer(
                    author=_author(child),
                    votes="",
                    content_md=_md(child.get("text")) or DELETED_BODY,
                    time=_time(child),
                    url=f"{_ITEM_URL}{cid}" if cid else "",
                    comment_count=f"{n} 条回复" if n else "",  # 0 条不引导 Enter
                ),
                comments=floor_comments(_kids(child), **_ACC),
            )
        )
    return StoryDetail(
        title=str(root.get("title") or "").strip(),
        meta=" · ".join(parts),
        time=_time(root),
        description_md="\n\n".join(blocks),
        floors=floors,
    )
