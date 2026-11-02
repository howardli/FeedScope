"""RSS 源编排层测试：配置合成 feeds、单流 / 聚合流取数、条件请求、详情。

取数全部走 MockTransport（补丁 core/http 的模块池，同 test_http.py）；
fetch 的条件缓存是模块级状态，本文件用 autouse fixture 逐例清空。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_rss_parse import ATOM_XML, RSS_XML  # 平级测试模块（pytest prepend 模式同目录可导）

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core import http
from feedscope.core.registry import get_source
from feedscope.core.source import SourceError
from feedscope.sources.rss import fetch

# 两条活订阅 + 一条死链（聚合失败隔离用）
_CFG = """\
[rss.feeds]
one = { label = "源一", url = "https://one.test/feed.xml" }
two = { url = "https://two.test/feed.xml" }
dead = { label = "死链", url = "https://dead.test/feed.xml" }
"""


@pytest.fixture(autouse=True)
def _clean_fetch_cache() -> None:
    fetch._CACHE.clear()


def _use_config(monkeypatch: pytest.MonkeyPatch, tmp_path, text: str) -> None:
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _route_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """one/two 回 XML，其余（dead）404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "one.test":
            return httpx.Response(200, text=RSS_XML, request=request)
        if request.url.host == "two.test":
            return httpx.Response(200, text=ATOM_XML, request=request)
        return httpx.Response(404, text="gone", request=request)

    _mock(monkeypatch, handler)


# ---- feeds 合成（配置驱动）----


def test_feeds_built_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    src = get_source("rss")
    assert src.feeds == ["all", "one", "two", "dead"]  # 聚合流「全部」打头
    assert src.feed_labels == {"all": "全部", "one": "源一", "two": "two", "dead": "死链"}  # label 缺省回 key


def test_hide_aggregate_or_subscription(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG + '[rss]\nhide = ["all"]\n')
    assert get_source("rss").feeds == ["one", "two", "dead"]  # 隐藏聚合流，订阅流保留
    _use_config(monkeypatch, tmp_path, _CFG + '[rss]\nhide = ["one"]\n')
    assert get_source("rss").feeds == ["all", "two", "dead"]  # 隐藏单个订阅


def test_no_subscriptions_no_feeds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, "")
    assert get_source("rss").feeds == []  # 零订阅 = 无可选信息流（F2/首启不列出）


def test_unknown_feed_name_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("rss").open_feed("nope"))


# ---- 单订阅流 / 聚合流（MockTransport）----


def test_open_single_feed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    _route_mock(monkeypatch)
    loader = asyncio.run(get_source("rss").open_feed("one"))
    try:
        # 无 guid/link 条目按 fallback_url 保留（Ctrl+O 落订阅首页）
        assert [it.id for it in loader.items] == [
            "one/post-1",
            "one/https://example.com/2",
            "one/https://one.test/feed.xml",
        ]
        assert loader.items[0].meta == "张三"  # 单订阅视图：订阅名不进元信息列
        assert loader.done  # RSS 无翻页语义
    finally:
        asyncio.run(loader.close())


def test_open_all_merges_and_isolates_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """聚合流：时间倒序合并 + 跨订阅 URL 去重 + 死订阅隔离（时间线照出）。"""
    _use_config(monkeypatch, tmp_path, _CFG)
    _route_mock(monkeypatch)
    loader = asyncio.run(get_source("rss").open_feed("all"))
    try:
        ids = [it.id for it in loader.items]
        # 最新在前：RSS 第一篇(11-02) > Atom(11-01) > RSS 第二篇(10-27)；死链条目缺席但不炸
        assert ids == ["one/post-1", "two/tag:example.com,2026:1", "one/https://example.com/2"]
        assert loader.items[0].meta == "源一 · 张三"  # 聚合视图：订阅名打头
    finally:
        asyncio.run(loader.close())


def test_open_all_all_dead_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(
        monkeypatch,
        tmp_path,
        '[rss.feeds]\ndead = { url = "https://dead.test/feed.xml" }\n',
    )
    _route_mock(monkeypatch)
    with pytest.raises(SourceError, match="订阅全部拉取失败"):
        asyncio.run(get_source("rss").open_feed("all"))


def test_open_all_dedups_same_url_across_feeds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同一 URL 被两个订阅覆盖（转载/聚合）只留一份：书写顺序靠前的赢。"""
    _use_config(
        monkeypatch,
        tmp_path,
        '[rss.feeds]\na = { url = "https://one.test/feed.xml" }\nb = { url = "https://one.test/feed.xml" }\n',
    )
    _route_mock(monkeypatch)
    loader = asyncio.run(get_source("rss").open_feed("all"))
    try:
        urls = [it.url for it in loader.items]
        assert len(urls) == len(set(urls))
        assert all(it.id.startswith("a/") for it in loader.items)  # 靠前的订阅版本胜出
    finally:
        asyncio.run(loader.close())


# ---- 条件请求：ETag 命中回放缓存体 ----


def test_conditional_get_reuses_cached_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"inm": request.headers.get("If-None-Match", ""), "url": str(request.url)})
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304, request=request)
        return httpx.Response(200, text=RSS_XML, headers={"ETag": '"v1"'}, request=request)

    _mock(monkeypatch, handler)
    feed = fetch.RssFeed(key="k", label="l", url="https://one.test/feed.xml")

    body1 = asyncio.run(fetch.fetch_body(feed))
    body2 = asyncio.run(fetch.fetch_body(feed))  # 第二次带 If-None-Match，304 回放
    assert body2 == body1 and "第一篇" in body2.decode("utf-8")
    assert len(calls) == 2 and calls[1]["inm"] == '"v1"'


# ---- 详情：feed 自带正文，不重新请求 ----


def test_open_detail_renders_content(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, _CFG)
    _route_mock(monkeypatch)
    src = get_source("rss")
    loader = asyncio.run(src.open_feed("one"))
    try:
        item = loader.items[0]
    finally:
        asyncio.run(loader.close())

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))  # 若详情重新请求会撞进来
        return httpx.Response(500, request=request)

    _mock(monkeypatch, handler)
    detail = asyncio.run(src.open_detail(item))
    try:
        doc = detail.doc
        assert doc.title == "第一篇 & 含标签"
        assert doc.meta == "源一 · 张三"
        assert doc.time == "2026-11-02 08:00"  # epoch -> 北京时间
        assert "全文 1" in doc.description_md
        assert "[图片]" in doc.description_md and "https://example.com/i.png" in doc.description_md
        assert not doc.answers and detail.done  # 无楼层、一次性到头
    finally:
        asyncio.run(detail.close())
    assert calls == []  # 详情零请求：正文全靠列表抓取带回的 extra
