"""core/http 的替身：用 httpx.MockTransport 顶替模块级客户端，不碰网络。"""

from __future__ import annotations

import httpx
import pytest

from feedscope.core import http


def mock_http(monkeypatch: pytest.MonkeyPatch, handler) -> httpx.AsyncClient:
    """用 MockTransport 顶替 core/http 的模块级客户端：handler(request) -> Response。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http, "_client", client)
    return client


def recording_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """mock_http 的记录版：包一层把每个请求收进活列表并返回（断言请求序用）。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    mock_http(monkeypatch, wrapped)
    return calls
