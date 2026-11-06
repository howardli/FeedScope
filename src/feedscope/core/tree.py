"""讨论树拍平共用件：深层嵌套 -> Comment 模型的两层（顶层 + replies）。

Comment 只有两层而讨论站常嵌四五层（HN / Reddit）：楼层的直接子回复做
顶层 Comment、更深的后代拍平进它的 replies。「拍成两层」与 @「父作者」
前缀的规则在这里唯一实现，两站的观感由同一份代码保证：

- 紧贴上一条的那层不加 @（回复对象视觉相邻，@ 冗余）；隔层起加（被拍平
  后父不一定相邻，@ 是唯一指向）
- 父已删不给前缀（@ 不到人）；本尊已删有内容照常保留（作者栏是删除占位）
- 空壳节点（无正文也无孩子，典型是被删且无回复）整支跳过

节点形态各站不同（Algolia 的 dict / HTML 重建的 _CNode），差异经四个
accessor 注入：keep / kids_of / author_of / comment_of（本体 Comment，
@ 前缀由本模块按上述规则统一拼装，源内不再自拼）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from feedscope.core.models import Answer, Comment, Floor

_N = TypeVar("_N")

# 删除态占位词（作者栏 / 正文），HN 与 Reddit 同形，UI 不特判
DELETED_AUTHOR = "[已删除]"
DELETED_BODY = "（已删除）"


def _at(parent_author: str, *, direct: bool) -> str:
    """这层回复的 @ 前缀：direct（紧贴楼上）/ 父缺席 / 父已删 → 不加。"""
    if direct or not parent_author or parent_author == DELETED_AUTHOR:
        return ""
    return f"@{parent_author}"


def flatten_comments(
    nodes: Sequence[_N],
    *,
    keep: Callable[[_N], bool],
    kids_of: Callable[[_N], Sequence[_N]],
    author_of: Callable[[_N], str],
    comment_of: Callable[[_N], Comment],
    parent_author: str = "",
    direct: bool = False,
) -> list[Comment]:
    """nodes 的全部后代拍平成一串 Comment（@ 前缀标出回复对象，规则见模块 docstring）。"""
    flat: list[Comment] = []
    for node in nodes:
        if not keep(node):
            continue
        who = author_of(node)
        c = comment_of(node)
        if at := _at(parent_author, direct=direct):
            c.content_md = f"{at}：{c.content_md}"
        flat.append(c)
        flat.extend(
            flatten_comments(
                kids_of(node),
                keep=keep, kids_of=kids_of, author_of=author_of, comment_of=comment_of,
                parent_author=who,
            )
        )
    return flat


def floor_comments(
    nodes: Sequence[_N],
    *,
    keep: Callable[[_N], bool],
    kids_of: Callable[[_N], Sequence[_N]],
    author_of: Callable[[_N], str],
    comment_of: Callable[[_N], Comment],
) -> list[Comment]:
    """楼层（顶层评论）的直接子回复 -> 顶层 Comment；各自更深的后代拍平进 replies。

    直接子回复不加 @：楼层作者就在屏首（与知乎「回答下的评论」同观感）；
    replies 里第一层（紧贴楼上那条）也不加，@ 从隔层起才有指向价值。
    """
    out: list[Comment] = []
    for node in nodes:
        if not keep(node):
            continue
        c = comment_of(node)
        c.replies = flatten_comments(
            kids_of(node),
            keep=keep, kids_of=kids_of, author_of=author_of, comment_of=comment_of,
            parent_author=author_of(node),
            direct=True,
        )
        out.append(c)
    return out


def count_kept(
    node: _N, *, keep: Callable[[_N], bool], kids_of: Callable[[_N], Sequence[_N]]
) -> int:
    """node 子树内保留下来的评论总数（楼层头「N 条回复」与 meta 评论数用）。"""
    return sum(1 + count_kept(c, keep=keep, kids_of=kids_of) for c in kids_of(node) if keep(c))


def build_floors(
    nodes: Sequence[_N],
    *,
    keep: Callable[[_N], bool],
    kids_of: Callable[[_N], Sequence[_N]],
    author_of: Callable[[_N], str],
    comment_of: Callable[[_N], Comment],
    answer_of: Callable[[_N, int], Answer],
) -> list[Floor]:
    """顶层节点序列 -> Floor 列表（HN / Reddit 的「详情一次拉全树」共用）。

    骨架：keep 过滤 → count_kept 数子树 → answer_of(node, n) 造楼层本体
    （字段来源与 comment_count 引导词是站差异，n 为该层回复数）→
    floor_comments 拍平该层的回复树。与 flatten/floor_comments 同一注入
    四件套（keep/kids_of/author_of/comment_of），多一个 answer_of。
    """
    floors: list[Floor] = []
    for node in nodes:
        if not keep(node):
            continue
        n = count_kept(node, keep=keep, kids_of=kids_of)
        floors.append(
            Floor(
                answer=answer_of(node, n),
                comments=floor_comments(
                    kids_of(node),
                    keep=keep, kids_of=kids_of, author_of=author_of, comment_of=comment_of,
                ),
            )
        )
    return floors
