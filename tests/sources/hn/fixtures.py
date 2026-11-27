"""HN 源测试夹具：Algolia search hits 与 items/:id 整树的最小形状。"""

from __future__ import annotations

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


TREE = {
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
