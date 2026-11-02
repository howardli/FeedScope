"""HN 解析层测试：search hits -> FeedItem；items/:id 树 -> 楼层/评论两层拍平。

fixture 是 Algolia 线上响应形状的最小裁剪（字段名、空值形态照抄实测），
树 fixture 覆盖：深层嵌套拍平 + @ 父作者、被删评论（无回复跳过 / 有回复
保留楼层）、无回复楼层不引导 Enter。
"""

from __future__ import annotations

from feedscope.sources.hn.parse import parse_search, parse_story

# search 命中：链接帖 / Ask 自帖（无 url）/ Show（无作者）/ 野数据（非数字 id）
HITS = [
    {
        "objectID": "50312408",
        "title": "Postgres 19 is late",
        "url": "http://rhaas.blogspot.com/2026/10/postgres-19.html",
        "author": "soheilpro",
        "points": 769,
        "num_comments": 456,
        "created_at": "2026-10-31T01:38:56Z",
        "_tags": ["story", "author_soheilpro", "front_page"],
    },
    {
        "objectID": "50326821",
        "title": "Ask HN: favorite terminal tools?",
        "url": None,
        "author": "roansh",
        "points": 5,
        "num_comments": 2,
        "created_at": "2026-11-01T15:58:28Z",
        "_tags": ["story", "ask_hn"],
    },
    {
        "objectID": "50330000",
        "title": "Show HN: demo",
        "url": "https://demo.test",
        "author": None,
        "points": 1,
        "num_comments": 0,
        "_tags": ["story", "show_hn"],
    },
    {"objectID": "not-a-number", "url": "https://x.test", "author": "a", "points": 1},
    {"title": "缺 objectID 整条不可寻址"},
]


def test_parse_search_kinds_and_meta() -> None:
    items = parse_search(HITS)
    assert [it.id for it in items] == ["50312408", "50326821", "50330000"]  # 野数据丢条不丢流

    a = items[0]
    assert (a.source, a.kind, a.title) == ("hn", "story", "Postgres 19 is late")
    assert a.url == "http://rhaas.blogspot.com/2026/10/postgres-19.html"
    assert a.meta == "769 点 · 456 评 · soheilpro"

    b = items[1]
    assert b.kind == "ask"
    assert b.url == "https://news.ycombinator.com/item?id=50326821"  # 自帖兜底条目页

    c = items[2]
    assert c.kind == "show"
    assert c.meta == "1 点 · 0 评"  # 作者缺失不硬凑占位


def test_parse_search_empty_hits() -> None:
    assert parse_search([]) == []


# 树：根（自述 + 外链）
#   ├─ alice「顶层评论 A」
#   │   ├─ bob「直接回复 b1」                     <- 楼层 A 的顶层评论 1
#   │   └─ carol「直接回复 c1」                    <- 楼层 A 的顶层评论 2
#   │       └─ dave「深层 d」                      <- 拍平进 carol.replies（紧贴楼上，无 @）
#   │           └─ erin「更深 e」                  <- 同上但隔层，@dave
#   ├─ frank「顶层评论 B（无回复）」
#   ├─ 空壳（无正文无孩子，被删无回复）             <- 整支跳过
#   └─ 被删但有回复                                <- 楼层保留，作者/正文占位
#       └─ gina「回复已删评论」                    <- 楼层的顶层评论（无 @）
_TREE = {
    "id": 50312408,
    "title": "Postgres 19 is late",
    "url": "http://rhaas.blogspot.com/2026/10/postgres-19.html",
    "author": "soheilpro",
    "points": 769,
    "created_at": "2026-10-31T01:38:56Z",
    "text": "<p>Ask 自述</p>",
    "children": [
        {
            "id": 1,
            "author": "alice",
            "created_at": "2026-10-31T02:00:00Z",
            "text": "<p>顶层评论 A</p>",
            "children": [
                {"id": 2, "author": "bob", "created_at": "2026-10-31T02:05:00Z", "text": "<p>直接回复 b1</p>", "children": []},
                {
                    "id": 3,
                    "author": "carol",
                    "created_at": "2026-10-31T02:10:00Z",
                    "text": "<p>直接回复 c1</p>",
                    "children": [
                        {
                            "id": 4,
                            "author": "dave",
                            "created_at": "2026-10-31T02:20:00Z",
                            "text": "<p>深层 d</p>",
                            "children": [
                                {"id": 5, "author": "erin", "text": "<p>更深 e</p>", "children": []},
                            ],
                        },
                    ],
                },
            ],
        },
        {"id": 6, "author": "frank", "text": "<p>顶层评论 B</p>", "children": []},
        {"id": 7, "author": None, "text": None, "children": []},
        {
            "id": 8,
            "author": None,
            "text": None,
            "children": [{"id": 9, "author": "gina", "text": "<p>回复已删评论</p>", "children": []}],
        },
    ],
}


def test_parse_story_story_body() -> None:
    story = parse_story(_TREE)
    assert story.title == "Postgres 19 is late"
    assert story.meta == "769 点 · 8 评 · soheilpro"  # 8 = 树内保留的全部评论数
    assert story.time == "2026-10-31 09:38"  # UTC -> 北京时间
    # 链接帖正文 = 原文一行 + Ask/Show 自述全文
    assert "[原文](http://rhaas.blogspot.com/2026/10/postgres-19.html)" in story.description_md
    assert "Ask 自述" in story.description_md


def test_parse_story_floors() -> None:
    floors = parse_story(_TREE).floors
    assert [f.answer.author for f in floors] == ["alice", "frank", "[已删除]"]  # 空壳楼层被跳过

    a = floors[0].answer
    assert a.content_md == "顶层评论 A"
    assert a.comment_count == "4 条回复"  # bob/carol/dave/erin
    assert a.url == "https://news.ycombinator.com/item?id=1"  # 楼层直达 = 评论 permalink
    assert a.time == "2026-10-31 10:00"

    assert floors[1].answer.comment_count == ""  # 无回复不引导 Enter

    deleted = floors[2].answer
    assert deleted.content_md == "（已删除）"
    assert deleted.comment_count == "1 条回复"  # 删除但有回复，楼层照出


def test_parse_story_thread_flatten() -> None:
    """楼层 A 的回复树：直接子回复做顶层，更深层拍平进 replies 且 @ 直接父。"""
    comments = parse_story(_TREE).floors[0].comments
    assert [c.author for c in comments] == ["bob", "carol"]
    assert comments[0].content_md == "直接回复 b1"
    assert comments[0].replies == []  # bob 无后代

    carol = comments[1]
    assert carol.content_md == "直接回复 c1"
    assert [(r.author, r.content_md) for r in carol.replies] == [
        ("dave", "深层 d"),  # 回复 carol，紧贴楼上：@ 冗余不加
        ("erin", "@dave：更深 e"),  # 回复 dave（不是 carol）——隔层 @ 指向直接父
    ]
    # 被删楼层的直接回复不加 @（楼层作者栏已是占位，@ 不到人）
    assert parse_story(_TREE).floors[2].comments[0].content_md == "回复已删评论"


def test_parse_story_bare_root() -> None:
    """零评论的 story：无楼层、正文照出（首屏守卫之外最常见的正常空态）。"""
    story = parse_story({"id": 1, "title": "T", "author": "u", "points": 2, "children": []})
    assert story.floors == []
    assert story.meta == "2 点 · 0 评 · u"
    assert story.description_md == ""  # 无外链无自述
