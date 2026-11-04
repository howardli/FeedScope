"""Reddit 源编排层测试：JWT 判登门、列表翻页链、详情楼层/评论内存切片、
403 版本自愈、subreddit 订阅增删（全 MockTransport + 假 profile cookie）。

- 传输是 httpx 直调网站 HTML 通道；MockTransport 按 URL 路由页面/片段，
  编排与解析逻辑真实跑
- 判登门 = token_v2 JWT 本地解码（sub="user"/"loid"），_reddit_session 的
  过门逻辑用假 profile_cookies 注入真实跑
- 订阅表驱动的 feeds 合成照 test_rss_source.py 的配置文件模式
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_reddit_parse import LISTING_END_HTML, LISTING_HTML, THREAD_HTML, _post

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core import http
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import LoginRequired, SourceError
from feedscope.sources import reddit as reddit_mod
from feedscope.sources.reddit import fetch as reddit_fetch

# 两条 subreddit 订阅（feeds 合成 / 订阅流路径用）
_CFG = """\
[reddit.feeds]
rust = { label = "Rust", url = "https://www.reddit.com/r/rust/" }
python = { url = "https://www.reddit.com/r/python/" }
"""

_VERSION = "2026-11-04T13:46Z~456d6f1d"
_PARTIAL_PATH = "/svc/shreddit/feeds/popular-feed"


def _use_config(monkeypatch: pytest.MonkeyPatch, tmp_path, text: str):
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))
    return p


def _jwt(claims: dict) -> str:
    """造一枚三段式假 JWT（判登只解码 payload，签名随便）。"""
    import base64
    import json

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    return f"{b64(b'{\"alg\":\"none\"}')}.{b64(json.dumps(claims).encode())}.sig"


def _logged_in_token() -> str:
    return _jwt({"sub": "user", "at": 1, "aid": "t2_56dpnldj"})


class _FakeBrowser:
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0.0.0 Safari/537.36"


def _fake_login(
    monkeypatch: pytest.MonkeyPatch,
    cookies: dict[str, str] | None = None,
) -> None:
    """假 profile cookie + 假浏览器 UA：_reddit_session 的判登门真实跑，不起真浏览器。"""
    jar = dict(
        cookies
        if cookies is not None
        else {"reddit_session": "s", "token_v2": _logged_in_token(), "edgebucket": "e"}
    )

    async def fake_profile_cookies(url: str) -> dict[str, str]:
        return dict(jar)

    monkeypatch.setattr(reddit_mod, "profile_cookies", fake_profile_cookies)
    monkeypatch.setattr(reddit_mod, "get_session", lambda: _FakeBrowser())


def _page(html: str) -> str:
    """页面 HTML 里嵌版本头（收割用）。"""
    return f"<script>var v='{_VERSION}'</script>{html}"


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    monkeypatch.setattr(reddit_fetch, "_version", None)  # 版本缓存不跨用例
    return calls


def _route_listing(request: httpx.Request) -> httpx.Response:
    """页面/片段路由：热门页起步、翻页一片到底。"""
    if request.url.path == "/r/popular/":
        return httpx.Response(200, text=_page(LISTING_HTML), request=request)
    if request.url.path == _PARTIAL_PATH:
        return httpx.Response(200, text=LISTING_END_HTML, request=request)
    if request.url.path == "/":
        return httpx.Response(200, text=_page(LISTING_HTML), request=request)
    if request.url.path == "/r/rust/":
        return httpx.Response(200, text=_page(LISTING_HTML), request=request)
    return httpx.Response(404, text="gone", request=request)


def _item(id36: str = "1abcde") -> FeedItem:
    return FeedItem(
        source="reddit", id=id36, title="Rust 1.85 released",
        url=f"https://www.reddit.com/r/rust/comments/{id36}/slug_{id36}/",
    )


# ---- JWT 判登门 ----


def test_open_feed_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch, cookies={})
    _mock(monkeypatch, _route_listing)
    with pytest.raises(LoginRequired, match="需要登录"):
        asyncio.run(get_source("reddit").open_feed("popular"))


def test_gate_logged_in_token_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """token_v2 解码出登录态：判登门零请求直放。"""
    _fake_login(monkeypatch, cookies={"token_v2": _logged_in_token()})
    calls = _mock(monkeypatch, _route_listing)
    loader = asyncio.run(get_source("reddit").open_feed("popular"))
    asyncio.run(loader.close())
    assert calls[0].url.path == "/r/popular/"  # 第一个请求就是数据页


def test_gate_anonymous_token_prompts_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """token_v2 在但 sub=loid（打开过登录页的匿名访客就有）：判未登录，零请求。"""
    _fake_login(monkeypatch, cookies={"token_v2": _jwt({"sub": "loid"}), "reddit_session": "s"})
    calls = _mock(monkeypatch, _route_listing)
    with pytest.raises(LoginRequired, match="需要登录"):
        asyncio.run(get_source("reddit").open_feed("popular"))
    assert calls == []


def test_login_verified_decodes_token_in_login_context() -> None:
    """登录成功的浏览器级判据：登录窗口上下文里读 token_v2 本地解码，零网络。"""
    from feedscope.sources.reddit import _login_verified

    class _Ctx:
        def __init__(self, jar: dict[str, str]) -> None:
            self._jar = jar

        async def cookies(self, _url):
            return [{"name": k, "value": v} for k, v in self._jar.items()]

    assert asyncio.run(_login_verified(_Ctx({"token_v2": _logged_in_token()}))) is True
    assert asyncio.run(_login_verified(_Ctx({"token_v2": _jwt({"sub": "loid"})}))) is False
    assert asyncio.run(_login_verified(_Ctx({"token_v2": "bad"}))) is False
    assert asyncio.run(_login_verified(_Ctx({}))) is False


# ---- 信息流：页面起步 + load-after 链翻页 ----


def test_open_feed_headers_and_topup(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSR 首页只嵌 2 帖：不足一批时自动跟 load-after 链补齐（首屏即一屏）。"""
    calls = _mock(monkeypatch, _route_listing)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("popular"))
    try:
        assert [it.id for it in loader.items] == ["1abcde", "1fghij", "1end00"]  # 页面 2 + 片段 1
        assert loader.done  # 夹具的链在片段后到底
        assert asyncio.run(loader.load_more()) == []  # 到底后翻页为空
    finally:
        asyncio.run(loader.close())
    page = calls[0]
    assert page.url.path == "/r/popular/"
    assert page.headers["user-agent"] == _FakeBrowser.ua  # 真 UA 与登录会话同形
    assert "token_v2" in page.headers["cookie"] or "reddit_session" in page.headers["cookie"]
    assert page.headers["accept"].startswith("text/html")


def test_feed_loader_incremental_pages_when_batch_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """批次已够（_MIN_BATCH 调低模拟片段大批量）：一次 load_more 一页，链逐次跟。"""
    monkeypatch.setattr(reddit_fetch, "_MIN_BATCH", 1)  # 夹具每页 1-2 帖即达标
    calls = _mock(monkeypatch, _route_listing)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("popular"))
    try:
        assert [it.id for it in loader.items] == ["1abcde", "1fghij"]  # 首批 = 页面原样
        assert not loader.done
        new = asyncio.run(loader.load_more())
        assert [it.id for it in new] == ["1end00"]  # 翻页 = 跟链一次
        assert loader.done
    finally:
        asyncio.run(loader.close())
    partial = calls[2]  # [页面起步, 收割版本的页面, 首个片段]
    assert partial.url.path == _PARTIAL_PATH and "after=dDNfMQ" in str(partial.url)
    assert partial.headers["x-reddit-client-version"] == _VERSION  # 版本头是收割来的
    assert partial.headers["accept"].startswith("text/vnd.reddit.partial")


def test_open_feed_unknown_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("reddit").open_feed("nope"))


def test_open_feed_empty_children_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(200, text=_page("<html>空页面</html>"), request=r))
    with pytest.raises(SourceError, match="0 条"):
        asyncio.run(get_source("reddit").open_feed("popular"))


# ---- 403 版本自愈 ----


def test_partial_403_refreshes_version_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """版本头过期导致的 403：重割版本（页面已换新）后原址重试成功。"""
    old_ver = "2026-11-01T00:00Z~0a1b2c3"
    partial_calls: list[httpx.Request] = []
    page_calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r/popular/":
            v = old_ver if page_calls[0] < 2 else _VERSION  # 前两次（起步+首割）旧版，403 后换新
            page_calls[0] += 1
            return httpx.Response(200, text=f"<script>var v='{v}'</script>{LISTING_HTML}", request=request)
        if request.url.path == _PARTIAL_PATH:
            partial_calls.append(request)
            if len(partial_calls) == 1:
                return httpx.Response(403, text="blocked", request=request)
            return httpx.Response(200, text=LISTING_END_HTML, request=request)
        return httpx.Response(404, request=request)

    _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("popular"))  # 补齐首屏时就会撞 403 并自愈
    asyncio.run(loader.close())
    assert [it.id for it in loader.items] == ["1abcde", "1fghij", "1end00"]  # 自愈成功出数据
    assert partial_calls[0].headers["x-reddit-client-version"] == old_ver  # 首用旧版本
    assert partial_calls[1].headers["x-reddit-client-version"] == _VERSION  # 重割后新版本


def test_partial_403_unhealed_gives_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """重割也没换版本（登录态失效/风控）：给人话指引，绝不清 cookie 不弹登录。"""
    partial_n = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r/popular/":
            return httpx.Response(200, text=_page(LISTING_HTML), request=request)
        if request.url.path == _PARTIAL_PATH:
            partial_n[0] += 1
            return httpx.Response(403, text="blocked", request=request)
        return httpx.Response(404, request=request)

    _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    with pytest.raises(SourceError, match="可能已过期或被风控"):
        asyncio.run(get_source("reddit").open_feed("popular"))  # 补齐首屏时撞 403 且无法自愈
    assert partial_n[0] == 1  # 版本没换：不重试（重试只认「版本确实轮换过」）


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


def test_open_feed_subscription_pages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    calls = _mock(monkeypatch, _route_listing)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("rust"))
    asyncio.run(loader.close())
    assert calls[0].url.path == "/r/rust/"  # 订阅 key 即 subreddit 名


# ---- 详情：一次请求全树，楼层/评论翻页零请求 ----


def _thread_route(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/r/rust/comments/"):
        return httpx.Response(200, text=_page(THREAD_HTML), request=request)
    if request.url.path == "/r/popular/":
        return httpx.Response(200, text=_page(LISTING_HTML), request=request)
    return httpx.Response(404, request=request)


def test_open_detail_floors_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock(monkeypatch, _thread_route)
    _fake_login(monkeypatch)
    detail = asyncio.run(get_source("reddit").open_detail(_item()))
    try:
        doc = detail.doc
        assert doc.title == "Rust 1.85 released"
        assert doc.meta == "1234 点 · 8 评 · r/rust · u/op"
        assert [a.author for a in doc.answers] == ["mod", "alice"]
        assert detail.done  # 2 楼 < FLOOR_PAGE，首批即全量

        cl = asyncio.run(detail.open_comments(1))  # alice 楼层的回复树
        try:
            assert [c.author for c in cl.comments] == ["bob", "dave", "erin"]
            assert cl.comments[1].content_md == "@bob：Deeper e"
            assert cl.done
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert calls[0].url.path.startswith("/r/rust/comments/1abcde/")
    assert len(calls) == 1  # 详情 + 评论层合计一次请求


def test_open_detail_out_of_range_comments() -> None:
    from feedscope.sources.reddit import RedditDetailLoader
    from feedscope.sources.reddit.parse import parse_thread_html

    loader = RedditDetailLoader(parse_thread_html(THREAD_HTML))
    with pytest.raises(SourceError, match="不在已加载范围内"):
        asyncio.run(loader.open_comments(99))


def _wide_thread(n_floors: int = 17) -> str:
    """宽树：17 楼（跨 FLOOR_PAGE=15），每楼 1 条回复。"""
    parts = [_post(ptype="text")]
    parts.append('<div slot="text-body"><div class="md"><p>body</p></div></div>')
    for i in range(n_floors):
        parts.append(
            f'<shreddit-comment author="u{i}" depth="0" score="1" thingId="t1_f{i}" '
            f'created="2026-11-04T12:00:00+00:00" permalink="/p/f{i}/">'
            f'<div class="md" slot="comment"><p>楼层 {i}</p></div>'
        )
        parts.append(
            f'<shreddit-comment author="r{i}" depth="1" score="1" thingId="t1_g{i}" '
            f'created="2026-11-04T12:00:00+00:00" permalink="/p/g{i}/">'
            f'<div class="md" slot="comment"><p>回复 {i}</p></div>'
        )
    return "<html><body>" + "".join(parts) + "</body></html>"


def test_detail_and_comments_local_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    """楼层（15/页）与评论（20/页）的翻页都是内存切片：一次请求后零网络。"""
    calls = _mock(
        monkeypatch,
        lambda r: httpx.Response(200, text=_page(_wide_thread()), request=r)
        if r.url.path.startswith("/r/rust/comments/")
        else httpx.Response(404, request=r),
    )
    _fake_login(monkeypatch)
    detail = asyncio.run(get_source("reddit").open_detail(_item("1wide")))
    try:
        assert len(detail.doc.answers) == 15 and not detail.done
        new = asyncio.run(detail.load_more())
        assert [a.author for a in new] == [f"u{i}" for i in range(15, 17)]  # u0..u14 在首批
        assert detail.done and len(detail.doc.answers) == 17

        cl = asyncio.run(detail.open_comments(0))
        try:
            assert len(cl.comments) == 1 and cl.done  # 每楼 1 条回复，一页全给
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert len(calls) == 1  # 楼层翻页 + 评论翻页都没再发请求


# ---- subreddit 订阅（subscriptions 编排）----


def _about_route(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/r/rust/":
        return httpx.Response(200, text=_page('x<div subreddit-name="rust"></div>' + LISTING_HTML), request=request)
    return httpx.Response(200, text=_page("<html>壳页</html>"), request=request)


def test_add_subreddit_validates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import tomllib

    from feedscope.core import config
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    p = _use_config(monkeypatch, tmp_path, "enabled = []\n")
    _fake_login(monkeypatch)
    _mock(monkeypatch, _about_route)
    feed = asyncio.run(add_subreddit("https://www.reddit.com/r/rust/"))
    assert (feed.key, feed.label) == ("rust", "rust")
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
    _mock(monkeypatch, _about_route)  # 壳页：subreddit 校验不通过
    with pytest.raises(SubscriptionError, match="加不上"):
        asyncio.run(add_subreddit("nosuchsub"))


def test_add_subreddit_needs_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    _use_config(monkeypatch, tmp_path, "")
    _fake_login(monkeypatch, cookies={})
    _mock(monkeypatch, _about_route)
    with pytest.raises(SubscriptionError, match="需要登录"):
        asyncio.run(add_subreddit("rust"))


def test_remove_and_rename_subreddit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.core import config
    from feedscope.subscriptions import remove_subreddit, rename_subreddit

    _use_config(monkeypatch, tmp_path, "enabled = []\n" + _CFG)
    _fake_login(monkeypatch)
    _mock(monkeypatch, _about_route)

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
    """空首页是新账号的正常形态（首页 = 站内关注聚合），给指引而不是改版报错。"""
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: httpx.Response(200, text=_page("<html>空</html>"), request=r))
    with pytest.raises(SourceError, match="关注版块"):
        asyncio.run(get_source("reddit").open_feed("home"))
