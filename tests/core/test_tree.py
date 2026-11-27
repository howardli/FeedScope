"""core/tree.py 的直接单测：拍平规则与 @ 前缀规则的基线。

此前只经 hn/reddit 的 parse fixture 间接覆盖——「拍成两层」「隔层加 @父作者」
「空壳整支跳过」「count_kept 只数保留」的规则在这里用最小树直接钉死。
节点用最简 dict（accessor 注入），不绑任何站点的真实形态。

规则速记（规则本体在 core/tree）：
- flatten_comments(nodes)：nodes 自身无 @（无父语境）；每个孩子的子树递归，
  首层 direct 决定加不加 @（floor_comments 传 True：紧贴楼上不加），
  更深一层起一律 @ 直接父——被拍平后父不一定相邻，@ 是唯一指向
- floor_comments(nodes)：nodes 是「楼层的直接子回复」，各自做顶层 Comment，
  replies 装更深层的拍平结果
"""

from __future__ import annotations

from feedscope.core.models import Answer, Comment
from feedscope.core.tree import (
    TreeAccessors,
    build_floors,
    count_kept,
    deleted_author,
    deleted_body,
    flatten_comments,
    floor_comments,
)


def node(name: str, body: str | None = "内容", kids: list | None = None) -> dict:
    return {"name": name, "body": body, "kids": kids or []}


def _keep(n: dict) -> bool:
    return bool(n["body"] or n["kids"])


def _kids_of(n: dict) -> list[dict]:
    return n["kids"]


def _author_of(n: dict) -> str:
    return n["name"] if n["name"] else deleted_author()


def _comment_of(n: dict) -> Comment:
    return Comment(author=_author_of(n), votes="", content_md=n["body"] or deleted_body())


_ACC = TreeAccessors(keep=_keep, kids_of=_kids_of, author_of=_author_of, comment_of=_comment_of)


def test_flatten_direct_flag_covers_top_level_only() -> None:
    """direct=True 只救传入层的顶层节点（紧贴楼上不加 @）；递归层一律
    direct=False——隔层必 @ 直接父，这是「拍平后 @ 是唯一指向」的实现。"""
    tree = [node("甲", kids=[node("乙", kids=[node("丙")])])]
    flat = flatten_comments(tree, _ACC, direct=True)
    assert [(c.author, c.content_md) for c in flat] == [
        ("甲", "内容"),  # 顶层节点：direct=True 且无父语境，无 @
        ("乙", "@甲：内容"),  # 递归层 direct=False：@ 直接父
        ("丙", "@乙：内容"),  # 隔层：@ 直接父
    ]


def test_flatten_default_marks_children() -> None:
    """direct 缺省 False：首层孩子也加 @父作者（floor_comments 的 replies
    递归正是靠这个给隔层加 @）。"""
    tree = [node("甲", kids=[node("乙")])]
    flat = flatten_comments(tree, _ACC)
    assert [c.content_md for c in flat] == ["内容", "@甲：内容"]


def test_flatten_skips_empty_shells_whole_branch() -> None:
    """空壳节点（无正文无孩子）整支跳过；有孩子的空壳保留（树还在）。"""
    tree = [node("甲"), node("", body=None), node("乙", body=None, kids=[node("丙")])]
    flat = flatten_comments(tree, _ACC)
    assert [c.author for c in flat] == ["甲", "乙", "丙"]
    assert flat[1].content_md == deleted_body()  # 本尊已删有孩子照常保留，正文占位


def test_flatten_no_at_for_deleted_parent() -> None:
    """父已删（作者占位）不给 @——@ 不到人。"""
    tree = [node("", body=None, kids=[node("丙")])]
    flat = flatten_comments(tree, _ACC)
    assert flat[1].content_md == "内容"  # 父是 [已删除]，不加 @


def test_floor_comments_two_level_shape() -> None:
    """楼层回复树 -> 顶层 Comment 各带 replies（更深的拍平进 replies）。"""
    tree = [node("乙", kids=[node("丁", kids=[node("己")]), node("戊")])]
    out = floor_comments(tree, _ACC)
    assert len(out) == 1
    assert out[0].author == "乙"  # 直接子回复做顶层
    assert [(c.author, c.content_md) for c in out[0].replies] == [
        ("丁", "内容"),  # 紧贴楼上（direct=True）：不加 @
        ("己", "@丁：内容"),  # 隔层：@ 直接父
        ("戊", "内容"),
    ]


def test_count_kept_counts_only_kept_descendants() -> None:
    """子树内保留的评论总数（自身不含）：空壳不算，空壳的孩子算。"""
    root = node("楼", kids=[
        node("甲"),
        node("", body=None),  # 空壳：不计
        node("乙", body=None, kids=[node("丙"), node("", body=None)]),  # 乙 + 丙 计，空壳不计
    ])
    assert count_kept(root, _ACC) == 3


def test_build_floors_skeleton_and_answer_of_injection() -> None:
    """build_floors：keep 过滤 → count_kept 报数 → answer_of 造楼层 → 拍平挂树。"""
    roots = [
        node("甲", kids=[node("乙")]),
        node("", body=None),  # 空壳楼层整支跳过
        node("丙"),  # 无回复楼层：引导词为空
    ]

    def answer_of(n: dict, count: int) -> Answer:
        return Answer(
            author=n["name"], votes="", content_md=n["body"],
            comment_count=f"{count} 条回复" if count else "",
        )

    floors = build_floors(roots, _ACC, answer_of=answer_of)
    assert [f.answer.author for f in floors] == ["甲", "丙"]
    assert floors[0].answer.comment_count == "1 条回复"
    assert floors[1].answer.comment_count == ""  # 0 条不引导 Enter
    assert [c.author for c in floors[0].comments] == ["乙"]
