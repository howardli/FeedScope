"""Reddit 解析层测试：shreddit SSR/partial HTML -> 模型，纯函数零网络。

夹具是按 2026-11 抓包形态手工构造的最小 HTML（开标签属性 + slot 容器 +
depth 嵌套），断言语义与旧 JSON 版一致：三层映射、@ 回复前缀、删除占位、
广告跳过、翻页 src 链、订阅校验。
"""

from __future__ import annotations

from feedscope.core.timefmt import fmt_iso
from feedscope.sources.reddit.parse import (
    parse_next_html,
    parse_posts_html,
    parse_subreddit_input,
    parse_subreddit_page,
    parse_thread_html,
)
from tests.sources.reddit.fixtures import (
    LISTING_END_HTML,
    LISTING_HTML,
    THREAD_HTML,
    TS,
    comment,
    post,
)

# 楼层树：mod(0) / alice(0) -> bob(1) -> dave(2) -> erin(3) / [已删除](0, 无正文)


# ---- 列表与翻页链 ----


def test_parse_posts_html_items_and_ads() -> None:
    items = parse_posts_html(LISTING_HTML)
    assert [(i.id, i.kind) for i in items] == [("1abcde", "self"), ("1fghij", "link")]
    a = items[0]
    assert a.title == "Rust 1.85 released"
    assert a.url == "https://www.reddit.com/r/rust/comments/1abcde/slug_1abcde/"
    assert a.meta == "1234 点 · 8 评 · r/rust"
    assert a.extra == {"subreddit": "rust"}
    assert items[1].meta == "99 点 · 0 评 · r/rust"


def test_parse_next_html_follows_load_after_src() -> None:
    nxt = parse_next_html(LISTING_HTML)
    assert nxt == "https://www.reddit.com/svc/shreddit/feeds/popular-feed?after=dDNfMQ%3D%3D&distance=4"
    assert parse_next_html(LISTING_END_HTML) is None  # 无 load-after = 到底


def test_parse_posts_html_score_万级() -> None:
    html = post(score="12345", comments="3586")
    (item,) = parse_posts_html(html)
    assert item.meta == "1.2 万 点 · 3586 评 · r/rust"


# ---- 帖子与评论树 ----


def test_parse_thread_html_post_and_floors() -> None:
    t = parse_thread_html(THREAD_HTML, fallback_title="fb")
    assert t.title == "Rust 1.85 released"
    assert t.meta == "1234 点 · 8 评 · r/rust · u/op"
    assert t.time == fmt_iso(TS)
    assert t.description_md.strip() == "Self text here"
    # mod / alice 两楼（[deleted] 空壳整支跳过）
    assert [f.answer.author for f in t.floors] == ["mod", "alice"]
    assert t.floors[0].answer.votes == "赞 10"
    assert t.floors[0].answer.comment_count == ""  # 无回复不引导 Enter
    assert t.floors[1].answer.comment_count == "3 条回复 · 另有 3 条未展开"  # bob+dave+erin + 截断提示


def test_parse_thread_html_flatten_and_at_prefixes() -> None:
    t = parse_thread_html(THREAD_HTML)
    alice = t.floors[1]
    # 拍平成两层（同 HN，规则在 core.tree）：bob 直接回复 alice 做顶层；
    # bob 的回复串里 dave 紧贴楼上不加 @、erin 隔层 @dave
    assert [c.author for c in alice.comments] == ["bob"]
    assert alice.comments[0].content_md == "Reply to alice"
    assert alice.comments[0].raw_id == "b1"
    assert [(c.author, c.content_md) for c in alice.comments[0].replies] == [
        ("dave", "Deeper e"),
        ("erin", "@dave：Deepest"),
    ]


def test_parse_thread_html_truncation_hint() -> None:
    # comment-count=8 而只渲染了 5 条（2 楼 + 3 回复）：末楼给「另有 3 条未展开」
    t = parse_thread_html(THREAD_HTML)
    assert "另有 3 条未展开" in t.floors[-1].answer.comment_count


def test_parse_thread_html_deleted_and_media() -> None:
    # 删除楼有回复时保留树形；媒体帖出记号
    html = (
        post(pid="1media", ptype="image", href="https://i.redd.it/x.png")
        + comment("[deleted]", "", cid="del")
        + comment("someone", "still here", depth=1, cid="kid")
    )
    t = parse_thread_html(html)
    assert "[图片](https://i.redd.it/x.png)" in t.description_md
    assert [f.answer.author for f in t.floors] == ["[已删除]"]
    assert t.floors[0].answer.content_md == "（已删除）"
    assert t.floors[0].comments[0].author == "someone"  # 删除楼的回复保留


def test_parse_thread_html_empty_and_link_post() -> None:
    html = post(pid="1link", ptype="link", href="https://example.com/news")
    t = parse_thread_html(html, fallback_title="fb")
    assert t.description_md == "[原文](https://example.com/news)"
    assert t.floors == []  # 无评论的帖子：空楼层，不炸
    t2 = parse_thread_html("<html>野页面</html>", fallback_title="fb")
    assert t2.title == "fb" and t2.floors == []  # 野数据防御


# ---- 订阅校验与输入归一 ----


def test_parse_subreddit_page() -> None:
    html = "<html>" + "x" * 450_000 + ' subreddit-name="rust" ' + post() + "</html>"
    assert parse_subreddit_page(html) == "rust"  # 属性在 400KB+ 深处也能找到
    assert parse_subreddit_page('<html><div subreddit-prefixed-name="r/rust"></div></html>') == "rust"
    assert parse_subreddit_page("<html>壳页无标记</html>") is None


def test_parse_subreddit_input() -> None:
    assert parse_subreddit_input("rust") == "rust"
    assert parse_subreddit_input("r/Rust") == "rust"
    assert parse_subreddit_input("/r/rust/") == "rust"
    assert parse_subreddit_input("https://www.reddit.com/r/rust/") == "rust"
    assert parse_subreddit_input("不是有效输入!") == ""
    assert parse_subreddit_input("") == ""
