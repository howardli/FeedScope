"""Reddit 解析层测试：listing -> FeedItem；comments 树 -> 楼层/评论两层拍平。

fixture 是 Reddit .json 端点线上响应形状的最小裁剪（t3/t1/more 包装、
replies 无回复时是空串、preview 地址的 &amp; 转义态都照抄实测），树
fixture 覆盖：深层嵌套拍平 + @ 父作者、置顶评论前缀、被删评论（无回复
跳过 / 有回复保留楼层）、more 截断计数的挂靠、无回复楼层不引导 Enter。
"""

from __future__ import annotations

from feedscope.core.timefmt import fmt_unix
from feedscope.sources.reddit.parse import (
    parse_listing,
    parse_subreddit_input,
    parse_thread,
)

# ---- fixture 帮手（照 Reddit listing/t1 原始形状）----


def _t3(d: dict) -> dict:
    return {"kind": "t3", "data": d}


def _t1(d: dict) -> dict:
    return {"kind": "t1", "data": d}


def _more(count: int) -> dict:
    return {"kind": "more", "data": {"count": count, "children": ["t1_xx", "t1_yy"]}}


def _replies(*children: dict) -> dict:
    return {"data": {"children": list(children)}}


def _listing(children: list[dict], after: str | None = None) -> dict:
    return {"kind": "Listing", "data": {"children": children, "after": after}}


def _thread(post: dict, top: list[dict]) -> list:
    return [_listing([_t3(post)]), _listing(top)]


def _c(
    author: str,
    body: str,
    *,
    score: int = 0,
    cid: str = "",
    stickied: bool = False,
    replies: list[dict] | None = None,
) -> dict:
    d = {
        "id": cid or f"c_{author}",
        "author": author,
        "body": body,
        "score": score,
        "created_utc": 1730000000.0,
        "permalink": f"/r/rust/comments/1ab/rust/{cid or author}/",
    }
    if stickied:
        d["stickied"] = True
    if replies is not None:
        d["replies"] = _replies(*replies)
    return _t1(d)


# 列表：链接帖 / 自述帖 / 野数据（无 id、非 t3）
LISTING = [
    _t3(
        {
            "id": "1abcde",
            "title": "Rust 1.85 released",
            "permalink": "/r/rust/comments/1abcde/rust_185_released/",
            "url": "https://blog.rust-lang.org/2026/11/01/Rust-1.85.html",
            "subreddit": "rust",
            "score": 1234,
            "num_comments": 456,
            "author": "op",
            "is_self": False,
        }
    ),
    _t3(
        {
            "id": "1fghij",
            "title": "Ask: best terminal setup?",
            "permalink": "/r/rust/comments/1fghij/ask/",
            "subreddit": "rust",
            "score": 5,
            "num_comments": 2,
            "is_self": True,
        }
    ),
    _t3({"title": "缺 id 整条不可寻址"}),
    {"kind": "t1", "data": {"id": "1nope"}},  # 非 t3 混进列表
    {"kind": "t3"},  # 缺 data
]


def test_parse_listing_items_and_meta() -> None:
    items = parse_listing(LISTING)
    assert [it.id for it in items] == ["1abcde", "1fghij"]  # 野数据丢条不丢流

    a = items[0]
    assert (a.source, a.kind, a.title) == ("reddit", "link", "Rust 1.85 released")
    assert a.url == "https://www.reddit.com/r/rust/comments/1abcde/rust_185_released/"
    assert a.meta == "1234 点 · 456 评 · r/rust"
    assert a.extra == {"subreddit": "rust"}

    b = items[1]
    assert b.kind == "self"
    assert b.meta == "5 点 · 2 评 · r/rust"


def test_parse_listing_empty() -> None:
    assert parse_listing([]) == []


# 树：帖子（自述帖）
#   ├─ mod「版规」(stickied，无回复)               <- 楼层，[置顶] 前缀
#   ├─ alice「顶层评论 A」
#   │   └─ bob「直接回复 b」                       <- 楼层 A 的顶层评论
#   │       └─ dave「深层 d」                      <- 拍平进 bob.replies（紧贴楼上，无 @）
#   │           └─ erin「更深 e」                  <- 同上但隔层，@dave
#   ├─ 空壳（[removed] 且无回复）                   <- 整支跳过
#   ├─ 被删但有回复（author [deleted]）             <- 楼层保留，作者/正文占位
#   │   └─ gina「回复已删评论」                    <- 楼层的顶层评论（无 @）
#   └─ more(count=37)                              <- 顶层截断折进末楼层 comment_count
POST = {
    "id": "1abcde",
    "title": "Rust 1.85 released",
    "permalink": "/r/rust/comments/1abcde/rust_185_released/",
    "subreddit": "rust",
    "author": "op",
    "score": 1234,
    "num_comments": 8,
    "created_utc": 1730000000.0,
    "is_self": True,
    "selftext": "**Highlights**\n\n- `let chains` stabilized",
}
THREAD = _thread(
    POST,
    [
        _c("mod", "Read the rules before posting.", score=50, cid="c_mod", stickied=True),
        _c("alice", "Comment A", score=42, replies=[_c("bob", "direct reply b", score=3, replies=[_c("dave", "deep d", replies=[_c("erin", "deeper e")])])]),
        _t1({"id": "c_gone", "author": "[deleted]", "body": "[removed]"}),
        _c("[deleted]", "[removed]", replies=[_c("gina", "reply to deleted")]),
        _more(37),
    ],
)


def test_parse_thread_post_and_floors() -> None:
    t = parse_thread(THREAD, fallback_title="兜底")
    assert t.title == "Rust 1.85 released"
    assert t.meta == "1234 点 · 8 评 · r/rust · u/op"
    assert t.time == fmt_unix(1730000000.0)
    assert t.description_md == "**Highlights**\n\n- `let chains` stabilized"  # markdown 直传

    assert [f.answer.author for f in t.floors] == ["mod", "alice", "[已删除]"]
    mod = t.floors[0].answer
    assert mod.content_md == "[置顶] Read the rules before posting."
    assert mod.votes == "赞 50"
    assert mod.url == "https://www.reddit.com/r/rust/comments/1ab/rust/c_mod/"
    assert mod.comment_count == ""  # 无回复不引导 Enter

    alice = t.floors[1].answer
    assert alice.comment_count == "3 条回复"  # bob + dave + erin

    deleted = t.floors[2].answer
    assert (deleted.author, deleted.content_md) == ("[已删除]", "（已删除）")
    assert deleted.comment_count == "1 条回复 · 另有 37 条未展开"  # 顶层 more 折进末楼层


def test_parse_thread_flattens_reply_tree() -> None:
    t = parse_thread(THREAD)
    comments = t.floors[1].comments
    assert [c.author for c in comments] == ["bob"]  # alice 的直接子回复做顶层
    assert comments[0].votes == "赞 3"
    assert [c.author for c in comments[0].replies] == ["dave", "erin"]
    assert comments[0].replies[0].content_md == "deep d"  # 紧贴楼上无 @
    assert comments[0].replies[1].content_md == "@dave：deeper e"  # 隔层 @ 直接父

    # 父已删不给 @（@ 不到人）；回复照常保留
    deleted_floor = t.floors[2].comments
    assert [c.content_md for c in deleted_floor] == ["reply to deleted"]


def test_parse_thread_deep_more_attaches_to_neighbor() -> None:
    """分支内 more 的 count 挂到同层前一条评论（「另有 N 条」提示语义）。"""
    tree = _thread(
        POST,
        [_c("alice", "A", replies=[_c("bob", "b", replies=[_c("dave", "d"), _more(5)])])],
    )
    t = parse_thread(tree)
    bob = t.floors[0].comments[0]
    assert [c.author for c in bob.replies] == ["dave"]
    assert bob.replies[0].more_replies == 5


def test_parse_thread_wild_data() -> None:
    assert parse_thread(None).floors == []  # 形不符不炸，楼层为空
    assert parse_thread([{}, {}]).description_md == ""
    t = parse_thread(_thread({}, []), fallback_title="兜底标题")
    assert t.title == "兜底标题"  # 帖子字段缺失回退列表标题


# ---- 帖子正文的媒体形态 ----


def _body_of(post: dict) -> str:
    return parse_thread(_thread(post, [])).description_md


def test_post_body_link_and_media() -> None:
    link = {"is_self": False, "url": "https://blog.test/post.html"}
    assert _body_of(link) == "[原文](https://blog.test/post.html)"

    # preview 地址是 &amp; 转义态：还原成真 & 再进记号链接
    image = {
        "is_self": False,
        "is_reddit_media_host": True,
        "url": "https://i.redd.it/abc.jpg",
        "preview": {
            "images": [
                {"source": {"url": "https://preview.redd.it/abc.png?width=960&amp;format=pjpg"}},
            ]
        },
    }
    assert _body_of(image) == "[图片](https://preview.redd.it/abc.png?width=960&format=pjpg)"

    video = {
        "is_self": False,
        "is_video": True,
        "secure_media": {"reddit_video": {"fallback_url": "https://v.redd.it/x/DASH_720.mp4"}},
        "preview": {"images": [{"source": {"url": "https://preview.redd.it/poster.png"}}]},
    }
    assert _body_of(video) == "[视频](https://v.redd.it/x/DASH_720.mp4)"

    gallery = {
        "is_self": False,
        "is_reddit_media_host": True,
        "preview": {"images": [{"source": {"url": f"https://preview.redd.it/{i}.png"}} for i in range(6)]},
    }
    assert _body_of(gallery).count("[图片]") == 4  # 相册上限 4 张防刷屏


def test_post_body_removed_selftext() -> None:
    assert _body_of({"is_self": True, "selftext": "[removed]"}) == "（已删除）"
    assert _body_of({"is_self": True, "selftext": ""}) == ""


# ---- 订阅输入归一 ----


def test_parse_subreddit_input_forms() -> None:
    ok = {
        "rust": "rust",
        "Rust": "rust",  # 归一小写
        "r/rust": "rust",
        "/r/rust/": "rust",
        "https://www.reddit.com/r/rust/": "rust",
        "http://old.reddit.com/r/rust": "rust",
        "reddit.com/r/rust/comments/1ab/x/": "rust",  # 帖子页地址也认
    }
    for text, want in ok.items():
        assert parse_subreddit_input(text) == want, text

    for bad in ("", "  ", "rust lang", "https://x.com/foo", "解释性文字", "a" * 22):
        assert parse_subreddit_input(bad) == "", bad
