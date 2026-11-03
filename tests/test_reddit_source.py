"""Reddit 源编排层测试：登录判据、信息流分页/订阅表合成、详情楼层与评论层
的内存切片、subreddit 订阅增删（全 MockTransport + 假浏览器会话）。

- 浏览器依赖（profile_cookies / get_session）用假件注入：_reddit_session
  的判登逻辑（两枚 cookie 任一在）真实跑，网络全 Mock
- MockTransport 补丁 core/http 的模块池（同 test_hn_source.py 模式）
- 订阅表驱动的 feeds 合成照 test_rss_source.py 的配置文件模式
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_reddit_parse import LISTING, THREAD, _c, _listing, _thread

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core import http
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import LoginRequired, SourceError
from feedscope.sources import reddit as reddit_mod

# 两条 subreddit 订阅（feeds 合成 / 订阅流路径用）
_CFG = """\
[reddit.feeds]
rust = { label = "Rust", url = "https://www.reddit.com/r/rust/" }
python = { url = "https://www.reddit.com/r/python/" }
"""


def _use_config(monkeypatch: pytest.MonkeyPatch, tmp_path, text: str):
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))
    return p


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    return calls


class _FakeBrowser:
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0.0.0 Safari/537.36"


def _fake_login(
    monkeypatch: pytest.MonkeyPatch,
    cookies: dict[str, str] | None = None,
) -> None:
    """假浏览器会话：profile cookie 与 UA 不起真浏览器，_reddit_session 照常判登。"""
    jar = dict(cookies if cookies is not None else {"reddit_session": "s", "token_v2": "t", "edgebucket": "e"})

    async def fake_profile_cookies(url: str) -> dict[str, str]:
        return dict(jar)

    monkeypatch.setattr(reddit_mod, "profile_cookies", fake_profile_cookies)
    monkeypatch.setattr(reddit_mod, "get_session", lambda: _FakeBrowser())


def _item(id36: str = "1abcde") -> FeedItem:
    return FeedItem(source="reddit", id=id36, title="Rust 1.85 released", url="https://www.reddit.com/r/rust/comments/1abcde/x/")


def _listing_ok(children=None, after: str | None = None) -> dict:
    return _listing(children if children is not None else LISTING, after)


# ---- 登录判据 ----


def test_open_feed_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch, cookies={})
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_listing_ok(), request=r))
    with pytest.raises(LoginRequired, match="需要登录"):
        asyncio.run(get_source("reddit").open_feed("home"))


def test_session_accepts_either_login_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """账号迁移期两枚判登 cookie 互斥出现：任一在即放行（比 X 的成对检查宽）。"""
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_listing_ok(), request=r))
    for cookies in ({"reddit_session": "s"}, {"token_v2": "t"}):
        _fake_login(monkeypatch, cookies)
        loader = asyncio.run(get_source("reddit").open_feed("home"))
        asyncio.run(loader.close())


# ---- 信息流 ----


def test_open_feed_home_headers_and_items(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "www.reddit.com"
        assert request.url.path == "/best.json"
        assert request.url.params["raw_json"] == "1"
        assert "reddit_session=s" in request.headers["cookie"]  # 全量 jar 进 Cookie 头
        assert request.headers["user-agent"] == _FakeBrowser.ua  # 真 UA 与登录会话同形
        return httpx.Response(200, json=_listing_ok(after=None), request=request)

    _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("home"))
    try:
        assert [it.id for it in loader.items] == ["1abcde", "1fghij"]
        assert loader.done  # after=None：一页到底
    finally:
        asyncio.run(loader.close())
    assert len(calls) == 1


def test_open_feed_unknown_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("reddit").open_feed("nope"))


def test_open_feed_empty_children_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_listing([]), request=r))
    with pytest.raises(SourceError, match="0 条"):
        asyncio.run(get_source("reddit").open_feed("popular"))


def test_feed_loader_pages_dedups_and_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    """after 游标翻页；重排挤进挤出的重复 id 去重；after=None 判到底。"""

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json=_listing_ok(after="t3_1abcde"), request=request)
        # 第二页：全重复 + 到头（Reddit 重排常见形态）
        return httpx.Response(200, json=_listing(LISTING[:1], after=None), request=request)

    calls = _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("home"))
    try:
        assert not loader.done and len(loader.items) == 2
        assert asyncio.run(loader.load_more()) == []  # 重复条目全部被去重
        assert "after=t3_1abcde" in str(calls[1].url)  # 游标回传下一页
        assert loader.done and len(loader.items) == 2
    finally:
        asyncio.run(loader.close())
    assert len(calls) == 2


# ---- feeds 混形合成（内置流 + 订阅表）----


def test_feeds_composition_builtins_plus_subs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    src = get_source("reddit")
    assert src.feeds == ["home", "popular", "rust", "python"]  # 内置在前，订阅按书写序
    assert src.feed_labels == {"home": "首页", "popular": "热门", "rust": "Rust", "python": "python"}
    assert src.subscribable


def test_feeds_composition_hide_and_collision(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """hide 剔内置流；手编配置里与内置流撞名的订阅被跳过（添加入口已拦，双保险）。"""
    _use_config(
        monkeypatch,
        tmp_path,
        '[reddit]\nhide = ["popular"]\n[reddit.feeds]\nhome = { url = "https://www.reddit.com/r/home/" }\nrust = { url = "https://www.reddit.com/r/rust/" }\n',
    )
    src = get_source("reddit")
    assert src.feeds == ["home", "rust"]
    assert src.feed_labels == {"home": "首页", "rust": "rust"}


def test_open_feed_subscription_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=_listing_ok(), request=request)

    _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("rust"))
    asyncio.run(loader.close())
    assert seen == ["/r/rust/hot.json"]  # 订阅 key 即 subreddit 名


# ---- 详情：一次请求全树，楼层/评论翻页零请求 ----


def test_open_detail_floors_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock(monkeypatch, lambda r: httpx.Response(200, json=THREAD, request=r))
    _fake_login(monkeypatch)
    detail = asyncio.run(get_source("reddit").open_detail(_item()))
    try:
        doc = detail.doc
        assert doc.title == "Rust 1.85 released"
        assert doc.meta == "1234 点 · 8 评 · r/rust · u/op"
        assert [a.author for a in doc.answers] == ["mod", "alice", "[已删除]"]
        assert detail.done  # 3 楼 < FLOOR_PAGE，首批即全量

        cl = asyncio.run(detail.open_comments(1))  # alice 楼层的回复树
        try:
            assert [c.author for c in cl.comments] == ["bob"]
            assert cl.comments[0].replies[1].content_md == "@dave：deeper e"
            assert cl.done
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert "/comments/1abcde.json" in str(calls[0].url)
    assert len(calls) == 1  # 详情 + 评论层合计一次请求


def test_open_detail_out_of_range_comments() -> None:
    from feedscope.sources.reddit import RedditDetailLoader
    from feedscope.sources.reddit.parse import parse_thread

    loader = RedditDetailLoader(parse_thread(THREAD))
    with pytest.raises(SourceError, match="不在已加载范围内"):
        asyncio.run(loader.open_comments(99))


def _wide_thread(n_floors: int = 17, replies_per: int = 25) -> list:
    """宽树：17 楼（跨 FLOOR_PAGE=15），第一楼带 25 条直接回复（跨 COMMENT_PAGE=20）。"""
    top = [_c("seed", "第一楼", replies=[_c(f"r{i}", f"回复 {i}") for i in range(replies_per)])]
    top += [_c(f"u{i}", f"楼层 {i}") for i in range(n_floors - 1)]
    return _thread({"id": "1w", "title": "wide", "subreddit": "rust", "is_self": False, "url": "https://x.test"}, top)


def test_detail_and_comments_local_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    """楼层（15/页）与评论（20/页）的翻页都是内存切片：一次请求后零网络。"""
    calls = _mock(monkeypatch, lambda r: httpx.Response(200, json=_wide_thread(), request=r))
    _fake_login(monkeypatch)
    detail = asyncio.run(get_source("reddit").open_detail(_item("1w")))
    try:
        assert len(detail.doc.answers) == 15 and not detail.done
        new = asyncio.run(detail.load_more())
        assert [a.author for a in new] == ["u14", "u15"]  # seed + u0..u13 在首批
        assert detail.done and len(detail.doc.answers) == 17

        cl = asyncio.run(detail.open_comments(0))
        try:
            assert len(cl.comments) == 20 and not cl.done
            more = asyncio.run(cl.load_more())
            assert [c.author for c in more] == [f"r{i}" for i in range(20, 25)]
            assert cl.done and len(cl.comments) == 25
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert len(calls) == 1  # 楼层翻页 + 评论翻页都没再发请求


# ---- subreddit 订阅（subscriptions 编排）----


def _about_json() -> dict:
    return {"kind": "t5", "data": {"display_name": "rust", "title": "rust"}}


def test_add_subreddit_validates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import tomllib

    from feedscope.core import config
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    p = _use_config(monkeypatch, tmp_path, "enabled = []\n")
    _fake_login(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/r/rust/about.json"
        return httpx.Response(200, json=_about_json(), request=request)

    _mock(monkeypatch, handler)
    feed = asyncio.run(add_subreddit("https://www.reddit.com/r/rust/"))
    assert (feed.key, feed.label) == ("rust", "rust")  # display_name 当默认显示名
    assert tomllib.loads(p.read_text(encoding="utf-8"))["enabled"] == ["reddit"]  # 加订阅自动启用

    # 重复 / 撞内置流 / 认不出：SubscriptionError 且不动配置
    for bad, match in (
        ("rust", "已经订阅"),
        ("home", "重名"),
        ("不是有效输入!", "没认出"),
    ):
        with pytest.raises(SubscriptionError, match=match):
            asyncio.run(add_subreddit(bad))
    assert [f.key for f in config.feeds_for("reddit")] == ["rust"]


def test_add_subreddit_about_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    _use_config(monkeypatch, tmp_path, "")
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(404, text="gone", request=r))
    with pytest.raises(SubscriptionError, match="加不上"):
        asyncio.run(add_subreddit("nosuchsub"))


def test_add_subreddit_needs_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    _use_config(monkeypatch, tmp_path, "")
    _fake_login(monkeypatch, cookies={})
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_about_json(), request=r))
    with pytest.raises(SubscriptionError, match="需要登录"):
        asyncio.run(add_subreddit("rust"))


def test_remove_and_rename_subreddit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.core import config
    from feedscope.subscriptions import remove_subreddit, rename_subreddit

    _use_config(monkeypatch, tmp_path, "enabled = []\n" + _CFG)
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_about_json(), request=r))

    assert rename_subreddit("rust", "  锈  ").label == "锈"  # 顺手 strip
    assert rename_subreddit("rust", "   ") is None  # 空名不动配置
    assert rename_subreddit("nope", "x") is None
    assert config.feeds_for("reddit")[0].label == "锈"

    removed = remove_subreddit("rust")
    assert (removed.key, removed.label) == ("rust", "锈")
    assert [f.key for f in config.feeds_for("reddit")] == ["python"]
    assert remove_subreddit("rust") is None  # 再删不动配置
    assert remove_subreddit("python").key == "python"
    # reddit 删到空也不摘 enabled（还有首页/热门内置流）：rss 的「删空摘除」语义不搬过来
    assert config.feeds_for("reddit") == [] and config.enabled_ids() == ["reddit"]


def test_update_feeds_roundtrip_preserved_on_save(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """F2/首启保存（save_enabled 全量重渲染）不丢 reddit 订阅表。"""
    from feedscope.core import config
    from feedscope.core.config import save_enabled

    _use_config(monkeypatch, tmp_path, _CFG)
    save_enabled(["zhihu", "reddit"])
    assert [f.key for f in config.feeds_for("reddit")] == ["rust", "python"]  # 订阅表原样保留
    assert [f.key for f in config.rss_feeds()] == []  # 各源互不干扰


def test_open_feed_home_empty_gets_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    """空首页是新账号的正常形态（best = 站内关注聚合），给指引而不是改版报错。"""
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_listing([]), request=r))
    with pytest.raises(SourceError, match="关注版块"):
        asyncio.run(get_source("reddit").open_feed("home"))
