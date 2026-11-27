"""样本落盘：url_name 稳定化、save 扩展名嗅探、http 钩子（MockTransport，不碰网络）。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from feedscope.core import http, samples


def test_url_name_stable_across_param_order_and_rand(monkeypatch: pytest.MonkeyPatch) -> None:
    a = samples.url_name("X", "https://x.com/api/tweet?fid=334&rand=123&__output=11")
    b = samples.url_name("X", "https://x.com/api/tweet?__output=11&rand=456&fid=334")
    assert a == b  # rand 剔除 + 参数排序：同一端点重跑同名（覆盖刷新）
    assert a.startswith("x--api-tweet")
    assert "fid=334" in a and "rand" not in a


def test_url_name_full_url_strips_host() -> None:
    assert samples.url_name("X", "https://x.com/latest.json") == "x--latest.json"


def test_save_sniffs_suffix(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(tmp_path))
    samples.save("x--a", '{"topic_list": []}')
    samples.save("x--b", "<html></html>")
    samples.save("x--c", "plain text")
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["x--a.json", "x--b.html", "x--c.txt"]


def test_save_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.delenv("FEEDSCOPE_SAMPLE_DIR", raising=False)
    samples.save("x--a", "text")  # 不抛、不写
    assert not any(tmp_path.iterdir())


def test_http_get_writes_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(tmp_path))
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True}, request=request)
        )
    )
    monkeypatch.setattr(http, "_client", client)

    asyncio.run(http.get("https://x.test/api?rand=1", source="X"))

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "x--api.json"  # rand 参数被剔除，名字稳定


def test_url_name_strips_non_ascii_label() -> None:
    """源标签只留 ASCII（跨平台文件名安全）：中文标签回退 src。"""
    assert samples.url_name("测试源", "https://x.test/api") == "src--api"
