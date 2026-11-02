"""V2EX 源编排层测试：信息流守卫、详情正文零请求与楼层内存切片（全 MockTransport）。

MockTransport 补丁 core/http 的模块池（同 test_hn_source.py 模式）；宽回复
fixture 专门驱动楼层本地翻页（REPLY_PAGE 边界）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from test_v2ex_parse import REPLIES, TOPICS

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core import http
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import SourceError
from feedscope.sources.v2ex.parse import parse_topics


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    return calls


def _item(topic: dict = TOPICS[0]) -> FeedItem:
    """带列表上下文的条目（extra.topic 是详情正文的唯一来源）。"""
    return parse_topics([topic])[0]


# ---- 信息流 ----


def test_open_feed_hot(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.v2ex.com"
        assert request.url.path == "/api/topics/hot.json"
        return httpx.Response(200, json=TOPICS, request=request)

    _mock(monkeypatch, handler)
    loader = asyncio.run(get_source("v2ex").open_feed("hot"))
    try:
        assert [it.id for it in loader.items] == ["1253836", "1253979"]
        assert loader.done  # v1 一页全量，无翻页
        assert loader.items[0].meta == "职场话题 · 163 回复 · losephsky"
    finally:
        asyncio.run(loader.close())


def test_open_feed_unknown_name_rejected() -> None:
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("v2ex").open_feed("nope"))


def test_open_feed_empty_topics_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, lambda r: httpx.Response(200, json=[], request=r))
    with pytest.raises(SourceError, match="0 条"):
        asyncio.run(get_source("v2ex").open_feed("hot"))


def test_open_feed_non_list_shape_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """返回结构变了（如换成 dict 包裹）= 接口已改版，显式报错而非静默空列表。"""
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"data": []}, request=r))
    with pytest.raises(SourceError, match="返回结构异常"):
        asyncio.run(get_source("v2ex").open_feed("hot"))


# ---- 详情：正文随列表透传，打开只拉回复一个请求 ----


def test_open_detail_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/replies/show.json"
        assert request.url.params["topic_id"] == "1253836"
        return httpx.Response(200, json=REPLIES, request=request)

    calls = _mock(monkeypatch, handler)
    detail = asyncio.run(get_source("v2ex").open_detail(_item()))
    try:
        doc = detail.doc
        assert doc.title == "前公司老板找我回去救场，情况比较特殊，想听听各位的建议"
        assert doc.meta == "职场话题 · 163 回复 · losephsky"
        assert doc.description_md.startswith("# 求助")  # 正文来自 extra，零请求
        assert [a.author for a in doc.answers] == ["Croow", "zuosiruan"]
        assert detail.done  # 2 楼 < REPLY_PAGE，首批即全量
    finally:
        asyncio.run(detail.close())
    assert len(calls) == 1  # 只有回复一个请求


def test_open_detail_missing_context_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra 没带 topic（跨进程恢复的旧条目）：报错引导回列表，不发请求。"""
    calls = _mock(monkeypatch, lambda r: httpx.Response(200, json=[], request=r))
    bare = FeedItem(source="v2ex", id="1253836", title="T", url="https://www.v2ex.com/t/1253836")
    with pytest.raises(SourceError, match="列表上下文"):
        asyncio.run(get_source("v2ex").open_detail(bare))
    assert calls == []


def _wide_replies(n: int = 17) -> list[dict]:
    """跨 REPLY_PAGE=15 边界的回复串（形态对齐实测回复对象的最小裁剪）。"""
    return [
        {
            "id": 18000000 + i,
            "content": f"回复 {i}",
            "content_rendered": f"<p>回复 {i}</p>",
            "created": 1793580000 + i,
            "member": {"id": i, "username": f"u{i}"},
            "member_id": i,
            "topic_id": 1253836,
        }
        for i in range(n)
    ]


def test_detail_local_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    """楼层翻页是内存切片：一次请求后零网络。"""
    calls = _mock(monkeypatch, lambda r: httpx.Response(200, json=_wide_replies(), request=r))
    detail = asyncio.run(get_source("v2ex").open_detail(_item()))
    try:
        assert len(detail.doc.answers) == 15 and not detail.done
        new = asyncio.run(detail.load_more())
        assert [a.author for a in new] == ["u15", "u16"]
        assert detail.done and len(detail.doc.answers) == 17
    finally:
        asyncio.run(detail.close())
    assert len(calls) == 1  # 翻页没再发请求
