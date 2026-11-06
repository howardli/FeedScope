"""HN 源编排层测试：信息流分页/守卫、详情楼层与评论层的内存切片（全 MockTransport）。

MockTransport 补丁 core/http 的模块池（同 test_rss_source.py 模式）；宽树
fixture 专门驱动楼层/评论两级的本地翻页（floor_page/comment_page 边界）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import recording_http
from test_hn_parse import _TREE, HITS

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import SourceError


def _search_json(hits: list[dict], *, page: int = 0, nb_pages: int = 1) -> dict:
    return {"nbHits": len(hits), "page": page, "nbPages": nb_pages, "hitsPerPage": 30, "hits": hits}


def _item(oid: str = "50312408") -> FeedItem:
    return FeedItem(source="hn", id=oid, title="Postgres 19 is late", url="https://example.com")


# ---- 信息流 ----


def test_open_feed_front(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "hn.algolia.com"
        assert request.url.params["tags"] == "front_page"
        assert request.url.params["page"] == "0"
        return httpx.Response(200, json=_search_json(HITS), request=request)

    recording_http(monkeypatch, handler)
    loader = asyncio.run(get_source("hn").open_feed("front"))
    try:
        assert [it.id for it in loader.items] == ["50312408", "50326821", "50330000"]
        assert loader.done  # nbPages=1：首页快照一页装下
    finally:
        asyncio.run(loader.close())


def test_open_feed_unknown_name_rejected() -> None:
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("hn").open_feed("nope"))


def test_open_feed_empty_hits_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_http(monkeypatch, lambda r: httpx.Response(200, json=_search_json([]), request=r))
    with pytest.raises(SourceError, match="0 条"):
        asyncio.run(get_source("hn").open_feed("ask"))


def test_feed_loader_pages_and_dedups(monkeypatch: pytest.MonkeyPatch) -> None:
    """翻页到 nbPages 到头；索引重排挤进挤出的重复条目被 id 去重。"""
    pages = {
        0: _search_json(HITS, page=0, nb_pages=2),
        1: _search_json(HITS[:1], page=1, nb_pages=2),  # 全是已见条目
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["page"])], request=request)

    recording_http(monkeypatch, handler)
    loader = asyncio.run(get_source("hn").open_feed("front"))
    try:
        assert not loader.done and len(loader.items) == 3
        assert asyncio.run(loader.load_more()) == []  # 重复条目全部被去重
        assert loader.done and len(loader.items) == 3
    finally:
        asyncio.run(loader.close())


# ---- 详情：一次请求全树，楼层/评论翻页零请求 ----


def test_open_detail_floors_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = recording_http(
        monkeypatch,
        lambda r: httpx.Response(200, json=_TREE, request=r),
    )
    src = get_source("hn")
    detail = asyncio.run(src.open_detail(_item()))
    try:
        doc = detail.doc
        assert doc.title == "Postgres 19 is late"
        assert doc.meta == "769 点 · 8 评 · soheilpro"
        assert "[原文](http://rhaas.blogspot.com" in doc.description_md
        assert [a.author for a in doc.answers] == ["alice", "frank", "[已删除]"]
        assert detail.done  # 3 楼 < FLOOR_PAGE，首批即全量

        cl = asyncio.run(detail.open_comments(0))  # alice 楼层的回复树
        try:
            assert [c.author for c in cl.comments] == ["bob", "carol"]
            assert cl.comments[1].replies[0].content_md == "深层 d"  # 紧贴楼上无 @
            assert cl.done  # 本地切片已到头
        finally:
            asyncio.run(cl.close())
    finally:
        asyncio.run(detail.close())
    assert len(calls) == 1  # 详情 + 评论层合计一次请求


def test_open_detail_out_of_range_comments() -> None:
    """越界（未加载楼层）给 SourceError 出口，不裸炸（同知乎语义）。"""
    from feedscope.sources.hn import HnDetailLoader
    from feedscope.sources.hn.parse import parse_story

    loader = HnDetailLoader(parse_story(_TREE))
    with pytest.raises(SourceError, match="不在已加载范围内"):
        asyncio.run(loader.open_comments(99))


def _wide_tree(n_floors: int = 17, replies_per: int = 25) -> dict:
    """宽树：n_floors 个顶层楼层（跨 FLOOR_PAGE=15 边界），第一楼带
    replies_per 条直接回复（跨 comment_page=20 边界）。"""
    return {
        "id": 1,
        "title": "wide",
        "url": "https://example.com",
        "author": "op",
        "points": 10,
        "created_at": "2026-11-01T00:00:00Z",
        "text": "",
        "children": [
            {
                "id": 1000,
                "author": "seed",
                "text": "<p>第一楼</p>",
                "children": [
                    {"id": 2000 + i, "author": f"r{i}", "text": f"<p>回复 {i}</p>", "children": []}
                    for i in range(replies_per)
                ],
            }
        ]
        + [
            {"id": 100 + i, "author": f"u{i}", "text": f"<p>楼层 {i}</p>", "children": []}
            for i in range(n_floors - 1)
        ],
    }


def test_detail_and_comments_local_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    """楼层（15/页）与评论（20/页）的翻页都是内存切片：一次请求后零网络。"""
    calls = recording_http(monkeypatch, lambda r: httpx.Response(200, json=_wide_tree(), request=r))
    detail = asyncio.run(get_source("hn").open_detail(_item("1")))
    try:
        assert len(detail.doc.answers) == 15 and not detail.done
        new = asyncio.run(detail.load_more())
        assert [a.author for a in new] == ["u14", "u15"]  # seed+u0..u13 在首批
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
