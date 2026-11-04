"""X 取数层 401/403 自愈测试：MockTransport 全 Mock + 清 cookie 打桩，不起真浏览器。

X 的 401 语义唯一（auth_token 被服务端吊销），无需 Reddit 式「我是谁」探针，
按状态码直接判（设计见 _graphql_json 的 docstring）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core import http
from feedscope.core.source import LoginRequired, SourceError
from feedscope.sources.x import fetch as x_fetch
from feedscope.sources.x.fetch import XSession

_SESSION = XSession({"auth_token": "a", "ct0": "c"}, "UA/1.0")


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _stub_drop(monkeypatch: pytest.MonkeyPatch, cleared: list) -> None:
    async def fake_drop(url: str, names: tuple[str, ...]) -> None:
        cleared.append((url, names))

    monkeypatch.setattr(x_fetch, "drop_profile_cookies", fake_drop)


def test_graphql_401_dead_session_self_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 = auth_token 被吊销（cookie 只是还在本地）：清失效 cookie + LoginRequired。"""
    cleared: list = []
    _stub_drop(monkeypatch, cleared)
    _mock(
        monkeypatch,
        lambda r: httpx.Response(
            401, json={"errors": [{"code": 32, "message": "Could not authenticate you"}]}, request=r
        ),
    )
    with pytest.raises(LoginRequired, match="登录已失效"):
        asyncio.run(x_fetch._fetch_entries(_SESSION, "following", None))
    assert cleared == [("https://x.com", ("auth_token", "ct0"))]


def test_graphql_403_means_risk_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 = 风控/账号受限（重登救不了）：风控文案，不清 cookie 不引导重登。"""
    cleared: list = []
    _stub_drop(monkeypatch, cleared)
    _mock(monkeypatch, lambda r: httpx.Response(403, json={"errors": [{"code": 64}]}, request=r))
    with pytest.raises(SourceError, match="风控"):
        asyncio.run(x_fetch._fetch_entries(_SESSION, "following", None))
    assert cleared == []


def test_graphql_other_status_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """401/403 之外的状态码（如 429 限流）不走自愈，原样包成 SourceError。"""
    cleared: list = []
    _stub_drop(monkeypatch, cleared)
    _mock(monkeypatch, lambda r: httpx.Response(429, json={"errors": [{"code": 88}]}, request=r))
    with pytest.raises(SourceError, match="HTTP 429"):
        asyncio.run(x_fetch._fetch_entries(_SESSION, "following", None))
    assert cleared == []
