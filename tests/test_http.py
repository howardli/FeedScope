"""core/http.py 共享取数通道的离线测试（MockTransport，不碰网络）。"""

import asyncio

import httpx
import pytest
from conftest import mock_http

from feedscope.core import http
from feedscope.core.source import SourceError


def test_client_is_process_singleton() -> None:
    assert http.get_client() is http.get_client()  # 连接池复用


def test_get_passes_params_and_returns_body(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"path": request.url.path, "q": request.url.params.get("q")}, request=request
        )

    mock_http(monkeypatch, handler)

    async def go() -> dict:
        r = await http.get("https://x.test/api", source="测试源", params={"q": "1"})
        return r.json()

    assert asyncio.run(go()) == {"path": "/api", "q": "1"}


def test_get_passes_headers(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, text="ok", request=request)

    mock_http(monkeypatch, handler)

    async def go() -> str:
        r = await http.get(
            "https://x.test/api", source="测试源", headers={"Authorization": "Bearer t"}
        )
        return r.text

    assert asyncio.run(go()) == "ok"
    assert seen["auth"] == "Bearer t"


def test_get_raises_source_error_on_http_status(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    mock_http(monkeypatch, handler)

    with pytest.raises(SourceError) as ei:
        asyncio.run(http.get("https://x.test/boom", source="测试源"))
    msg = str(ei.value)
    assert "测试源 请求失败（HTTP 500）" in msg
    assert "https://x.test/boom" in msg  # 消息里带 URL，可定位


def test_get_raises_source_error_on_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("模拟超时", request=request)

    mock_http(monkeypatch, handler)

    with pytest.raises(SourceError) as ei:
        asyncio.run(http.get("https://x.test/slow", source="测试源"))
    assert "测试源 请求超时" in str(ei.value)


def test_close_is_idempotent(monkeypatch) -> None:
    mock_http(monkeypatch, lambda request: httpx.Response(200, request=request))
    asyncio.run(http.close())
    asyncio.run(http.close())  # 再关一次不炸
    assert http._client is None


def test_get_json_validates_shape(monkeypatch) -> None:
    """get_json：形状对直通；非 JSON 与形状不符（改版信号）分开报。"""
    mock_http(monkeypatch, lambda r: httpx.Response(200, json=[{"id": 1}], request=r))
    assert asyncio.run(http.get_json("https://x/api", source="测试", expect=list)) == [{"id": 1}]

    mock_http(monkeypatch, lambda r: httpx.Response(200, text="not json", request=r))
    with pytest.raises(SourceError, match="不是 JSON"):
        asyncio.run(http.get_json("https://x/api", source="测试", expect=list))

    mock_http(monkeypatch, lambda r: httpx.Response(200, json={"data": 1}, request=r))
    with pytest.raises(SourceError, match="结构异常"):
        asyncio.run(http.get_json("https://x/api", source="测试", expect=list))
