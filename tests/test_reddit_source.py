"""Reddit 源编排层测试：JWT 判登门、通道页签取数、列表翻页链、详情楼层/
评论内存切片、403 版本自愈、subreddit 订阅增删（FakeTab 替身 + 假
profile cookie，不碰浏览器不碰网络）。

- 传输是常驻通道页签的页面 fetch（知乎同款）：FakeTab.evaluate 认取数
  JS，按 URL 路由页面/片段，编排与解析逻辑真实跑（页签生命周期归
  test_channel.py 集中测，这里只测取数语义）
- 判登门 = token_v2 JWT 本地解码（sub="user"/"loid"），require_login 的
  过门逻辑用假 profile_cookies 注入真实跑
- 订阅表驱动的 feeds 合成照 test_rss_source.py 的配置文件模式
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeTab, FetchReq, use_config
from test_reddit_parse import LISTING_END_HTML, LISTING_HTML, THREAD_HTML, _post

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import LoginRequired, SourceError
from feedscope.sources import reddit as reddit_mod
from feedscope.sources.reddit import fetch as reddit_fetch
from feedscope.sources.reddit.fetch import fetch_listing

# 两条 subreddit 订阅（feeds 合成 / 订阅流路径用）
_CFG = """\
[reddit.feeds]
rust = { label = "Rust", url = "https://www.reddit.com/r/rust/" }
python = { url = "https://www.reddit.com/r/python/" }
"""

_VERSION = "2026-11-04T13:46Z~456d6f1d"
_PARTIAL_PATH = "/svc/shreddit/feeds/popular-feed"




def _jwt(claims: dict) -> str:
    """造一枚三段式假 JWT（判登只解码 payload，签名随便）。"""
    import base64
    import json

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(b'{"alg":"none"}')
    return f"{header}.{b64(json.dumps(claims).encode())}.sig"


def _logged_in_token() -> str:
    return _jwt({"sub": "user", "at": 1, "aid": "t2_56dpnldj"})


def _fake_login(
    monkeypatch: pytest.MonkeyPatch,
    cookies: dict[str, str] | None = None,
) -> None:
    """假 profile cookie：require_login 的判登门真实跑，不起真浏览器。"""
    jar = dict(
        cookies
        if cookies is not None
        else {"reddit_session": "s", "token_v2": _logged_in_token(), "edgebucket": "e"}
    )

    async def fake_profile_cookies(url: str) -> dict[str, str]:
        return dict(jar)

    monkeypatch.setattr(reddit_mod, "profile_cookies", fake_profile_cookies)


def _page(html: str) -> str:
    """页面 HTML 里嵌版本头（顺手收割用）。"""
    return f"<script>var v='{_VERSION}'</script>{html}"


# ---- 通道页签替身：conftest 的 FakeTab（统一取数原语分派），生命周期归 test_channel.py ----




def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[FetchReq]:
    """假通道页签接管取数（_tab/_version 复位）；返回请求记录的活列表。"""
    tab = FakeTab(on_fetch=handler)
    monkeypatch.setattr(reddit_fetch, "_tab", tab)
    monkeypatch.setattr(reddit_fetch, "_version", None)
    return tab.fetches


def _route_listing(req: FetchReq) -> tuple[int | None, str]:
    """页面/片段路由：热门页起步、翻页一片到底。"""
    if req.path == "/r/popular/":
        return None, _page(LISTING_HTML)
    if req.path == _PARTIAL_PATH:
        return None, LISTING_END_HTML
    if req.path == "/":
        return None, _page(LISTING_HTML)
    if req.path == "/r/rust/":
        return None, _page(LISTING_HTML)
    return 404, "gone"


def _item(id36: str = "1abcde") -> FeedItem:
    return FeedItem(
        source="reddit",
        id=id36,
        title="Rust 1.85 released",
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
    assert calls[0].path == "/r/popular/"  # 第一个请求就是数据页


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


def test_open_feed_channel_fetch_and_topup(monkeypatch: pytest.MonkeyPatch) -> None:
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
    # 页面响应顺手收割版本：首批 partial 不需要专割请求（httpx 时代要专门拉一次页面）
    assert [c.path for c in calls] == ["/r/popular/", _PARTIAL_PATH]
    assert calls[0].headers["Accept"].startswith("text/html")  # 页面不带版本头
    assert "x-reddit-client-version" not in calls[0].headers
    assert calls[1].headers["Accept"].startswith("text/vnd.reddit.partial")
    assert calls[1].headers["x-reddit-client-version"] == _VERSION  # 版本头是页面响应顺手收割来的


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
    assert [c.path for c in calls] == ["/r/popular/", _PARTIAL_PATH]
    assert calls[1].headers["x-reddit-client-version"] == _VERSION  # 片段带收割来的版本头


def test_open_feed_unknown_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("reddit").open_feed("nope"))


def test_open_feed_empty_children_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: (None, _page("<html>空页面</html>")))
    with pytest.raises(SourceError, match="0 条"):
        asyncio.run(get_source("reddit").open_feed("popular"))


# ---- 403 版本自愈 ----


def test_partial_403_refreshes_version_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """版本头过期导致的 403：重割版本（页面已换新）后原址重试成功。"""
    old_ver = "2026-11-01T00:00Z~0a1b2c3"
    pages = [0]  # /r/popular/ 页面被取的次数

    def handler(req: FetchReq) -> tuple[int | None, str]:
        if req.path == "/r/popular/":
            pages[0] += 1
            v = old_ver if pages[0] == 1 else _VERSION  # 起步页旧版，403 后的专割换新
            return None, f"<script>var v='{v}'</script>{LISTING_HTML}"
        if req.path == _PARTIAL_PATH:
            if req.headers.get("x-reddit-client-version") == old_ver:
                return 403, "blocked"
            return None, LISTING_END_HTML
        return 404, "gone"

    calls = _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("popular"))  # 补齐首屏时就会撞 403 并自愈
    asyncio.run(loader.close())
    assert [it.id for it in loader.items] == ["1abcde", "1fghij", "1end00"]  # 自愈成功出数据
    partials = [c for c in calls if c.path == _PARTIAL_PATH]
    assert [c.headers["x-reddit-client-version"] for c in partials] == [
        old_ver,
        _VERSION,
    ]  # 首用旧版本，重割后新版本
    assert [c.path for c in calls] == ["/r/popular/", _PARTIAL_PATH, "/r/popular/", _PARTIAL_PATH]


def test_partial_403_unhealed_gives_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """重割也没换版本（登录态失效/风控）：给人话指引，绝不清 cookie 不弹登录。"""

    def handler(req: FetchReq) -> tuple[int | None, str]:
        if req.path == "/r/popular/":
            return None, _page(LISTING_HTML)  # 版本一直是同一个
        if req.path == _PARTIAL_PATH:
            return 403, "blocked"
        return 404, "gone"

    calls = _mock(monkeypatch, handler)
    _fake_login(monkeypatch)
    with pytest.raises(SourceError, match="可能已过期或被风控"):
        asyncio.run(get_source("reddit").open_feed("popular"))  # 补齐首屏时撞 403 且无法自愈
    assert (
        sum(1 for c in calls if c.path == _PARTIAL_PATH) == 1
    )  # 版本没换：不重试（重试只认「版本确实轮换过」）


def test_partial_first_use_harvests_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存为空就先割再用：专割一次热门页后 partial 才发（空头发必 403）。"""
    calls = _mock(monkeypatch, _route_listing)
    items, _nxt = asyncio.run(
        fetch_listing(f"https://www.reddit.com{_PARTIAL_PATH}?after=dDNfMQ%3D%3D")
    )
    assert [it.id for it in items] == ["1end00"]
    assert [c.path for c in calls] == ["/r/popular/", _PARTIAL_PATH]  # 先割再用
    assert calls[1].headers["x-reddit-client-version"] == _VERSION


# ---- feeds 混形合成（内置流 + 订阅表）----


def test_feeds_composition_builtins_plus_subs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    src = get_source("reddit")
    assert src.feeds == ["home", "popular", "rust", "python"]  # 内置在前，订阅按书写序
    assert src.feed_labels == {
        "home": "首页",
        "popular": "热门",
        "rust": "Rust",
        "python": "python",
    }
    assert src.subscribable


def test_feeds_composition_hide_and_collision(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """hide 剔内置流；手编配置里与内置流撞名的订阅被跳过（添加入口已拦，双保险）。"""
    use_config(
        monkeypatch,
        tmp_path,
        '[reddit]\nhide = ["popular"]\n[reddit.feeds]\nhome = { url = "https://www.reddit.com/r/home/" }\nrust = { url = "https://www.reddit.com/r/rust/" }\n',
    )
    src = get_source("reddit")
    assert src.feeds == ["home", "rust"]
    assert src.feed_labels == {"home": "首页", "rust": "rust"}


def test_open_feed_subscription_pages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    calls = _mock(monkeypatch, _route_listing)
    _fake_login(monkeypatch)
    loader = asyncio.run(get_source("reddit").open_feed("rust"))
    asyncio.run(loader.close())
    assert calls[0].path == "/r/rust/"  # 订阅 key 即 subreddit 名


# ---- 详情：一次请求全树，楼层/评论翻页零请求 ----


def _thread_route(req: FetchReq) -> tuple[int | None, str]:
    if req.path.startswith("/r/rust/comments/"):
        return None, _page(THREAD_HTML)
    if req.path == "/r/popular/":
        return None, _page(LISTING_HTML)
    return 404, "gone"


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
            # 拍平两层（同 HN）：bob 顶层；dave 紧贴楼上无 @、erin 隔层 @dave
            assert [c.author for c in cl.comments] == ["bob"]
            assert [(c.author, c.content_md) for c in cl.comments[0].replies] == [
                ("dave", "Deeper e"),
                ("erin", "@dave：Deepest"),
            ]
            assert cl.done
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert calls[0].path.startswith("/r/rust/comments/1abcde/")
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
        lambda r: (
            (None, _page(_wide_thread()))
            if r.path.startswith("/r/rust/comments/")
            else (404, "gone")
        ),
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


def _subreddit_route(req: FetchReq) -> tuple[int | None, str]:
    if req.path == "/r/rust/":
        return None, _page('x<div subreddit-name="rust"></div>' + LISTING_HTML)
    return None, _page("<html>壳页</html>")


def test_add_subreddit_validates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import tomllib

    from feedscope.core import config
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    p = use_config(monkeypatch, tmp_path, "enabled = []\n")
    _fake_login(monkeypatch)
    _mock(monkeypatch, _subreddit_route)
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


def test_add_subreddit_page_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    use_config(monkeypatch, tmp_path, "")
    _fake_login(monkeypatch)
    _mock(monkeypatch, _subreddit_route)  # 壳页：subreddit 校验不通过
    with pytest.raises(SubscriptionError, match="加不上"):
        asyncio.run(add_subreddit("nosuchsub"))


def test_add_subreddit_needs_login(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.subscriptions import SubscriptionError, add_subreddit

    use_config(monkeypatch, tmp_path, "")
    _fake_login(monkeypatch, cookies={})
    _mock(monkeypatch, _subreddit_route)
    with pytest.raises(SubscriptionError, match="需要登录"):
        asyncio.run(add_subreddit("rust"))


def test_remove_and_rename_subreddit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.core import config
    from feedscope.subscriptions import remove_subreddit, rename_subreddit

    use_config(monkeypatch, tmp_path, "enabled = []\n" + _CFG)

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


def test_update_feeds_roundtrip_preserved_on_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """F2/首启保存（save_enabled 全量重渲染）不丢 reddit 订阅表。"""
    from feedscope.core import config
    from feedscope.core.config import save_enabled

    use_config(monkeypatch, tmp_path, _CFG)
    save_enabled(["zhihu", "reddit"])
    assert [f.key for f in config.feeds_for("reddit")] == ["rust", "python"]  # 订阅表原样保留
    assert [f.key for f in config.rss_feeds()] == []  # 各源互不干扰


def test_open_feed_home_empty_gets_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    """空首页是新账号的正常形态（首页 = 站内关注聚合），给指引而不是改版报错。"""
    _fake_login(monkeypatch)
    _mock(monkeypatch, lambda r: (None, _page("<html>空</html>")))
    with pytest.raises(SourceError, match="关注版块"):
        asyncio.run(get_source("reddit").open_feed("home"))
