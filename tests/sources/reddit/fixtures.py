"""Reddit 源测试夹具：shreddit 列表页 / 帖子页 HTML 的最小形状，与通道页签替身的接线（mock_tab）。"""

from __future__ import annotations

import pytest

from feedscope.sources.reddit import fetch as reddit_fetch
from tests.support.tabs import FakeTab

TS = "2026-11-04T12:00:00+00:00"


def post(
    pid: str = "1abcde",
    title: str = "Rust 1.85 released",
    *,
    score: str = "1234",
    comments: str = "8",
    sub: str = "rust",
    ptype: str = "text",
    href: str = "",
    cls: str = "",
) -> str:
    extra = f' class="{cls}"' if cls else ""
    ch = f' content-href="{href}"' if href else ""
    return (
        f'<shreddit-post{extra} id="t3_{pid}" post-title="{title}" score="{score}" '
        f'comment-count="{comments}" subreddit-name="{sub}" permalink="/r/{sub}/comments/{pid}/slug_{pid}/" '
        f'post-type="{ptype}"{ch} author="op" created-timestamp="{TS}"></shreddit-post>'
    )


def comment(
    author: str,
    body: str,
    *,
    depth: int = 0,
    score: str = "1",
    cid: str = "",
) -> str:
    cid = cid or f"c_{author}_{depth}"
    inner = f"<p>{body}</p>" if body else ""
    return (
        f'<shreddit-comment author="{author}" depth="{depth}" score="{score}" '
        f'thingId="t1_{cid}" created="{TS}" permalink="/r/rust/comments/1abcde/comment/{cid}/">'
        f'<div class="md" slot="comment">{inner}</div>'
    )


LISTING_HTML = (
    "<html><body>"
    + post()
    + post("1fghij", "Second post", score="99", comments="0", ptype="link", href="https://example.com/a")
    + post("1ad000", "Ad post", cls="promotedlink")
    + '<shreddit-post post-title="残条无 id"></shreddit-post>'  # 无 t3 id：跳过
    + '</body><faceplate-partial slot="load-after" src="/svc/shreddit/feeds/popular-feed?after=dDNfMQ%3D%3D&amp;distance=4"></faceplate-partial></html>'
)


LISTING_END_HTML = "<html><body>" + post("1end00", "Last") + "</body></html>"


THREAD_HTML = (
    "<html><head><style>shreddit-comment[x]</style></head><body>"
    + post(ptype="text")
    + '<div slot="text-body"><div class="md"><p>Self text here</p></div></div>'
    + comment("mod", "Floor one", score="10", cid="m1")
    + comment("alice", "Floor two", score="20", cid="a1")
    + comment("bob", "Reply to alice", depth=1, score="5", cid="b1")
    + comment("dave", "Deeper e", depth=2, score="2", cid="d1")
    + comment("erin", "Deepest", depth=3, score="1", cid="e1")
    + comment("[deleted]", "", depth=0, cid="x1")
    + "</body></html>"
)


def mock_tab(monkeypatch: pytest.MonkeyPatch, handler) -> list:
    """假通道页签接管 Reddit 取数（_tab/_version 复位）；返回请求记录的活列表。"""
    tab = FakeTab(on_fetch=handler)
    monkeypatch.setattr(reddit_fetch, "_tab", tab)
    monkeypatch.setattr(reddit_fetch, "_version", None)
    return tab.fetches
