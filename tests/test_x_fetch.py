"""X 取数层测试：FakeTab 通道页签替身（GraphQL fetch / chunk 收集按脚本
分派）+ 种子起步与失效收割自愈（chunk 下载 grep 新 queryId）+
401/403/限流语义 + TweetDetail 懒深度截获 + 判登门，不碰浏览器网络。
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse

import pytest
from conftest import FakeTab
from test_parsers import _conv_data, _entry, _item_content, _tweet_result

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.registry import get_source
from feedscope.core.source import LoginRequired, SourceError
from feedscope.sources import x as x_mod
from feedscope.sources.x import fetch as x_fetch
from feedscope.sources.x.fetch import (
    XFeedLoader,
    fetch_conversation,
    fetch_entries,
    _Op,
    _parse_graphql_url,
)


def _tl_payload(tweets: list[str], cursor: str | None) -> dict:
    """HomeTimeline 响应 fixture（URT：推文条目 + cursor-bottom 游标）。"""
    entries = [
        _entry(entry_id=f"tweet-{t}", item_content=_item_content(_tweet_result(t))) for t in tweets
    ]
    if cursor:
        entries.append(_entry(entry_id="cursor-bottom", cursor_value=cursor))
    return {
        "data": {
            "home": {
                "home_timeline_urt": {
                    "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                }
            }
        }
    }


def _detail_payload(rest_id: str, replies: list[str]) -> dict:
    """TweetDetail 响应 fixture（焦点推 + 回复模块 + 游标）。"""
    entries = [
        _entry(
            entry_id=f"tweet-{rest_id}",
            item_content=_item_content(_tweet_result(rest_id, new_user=True)),
        )
    ]
    for r in replies:
        entries.append(
            _entry(
                entry_id=f"conversationthread-{r}",
                items=[{"item": {"itemContent": _item_content(_tweet_result(r, new_user=True))}}],
            )
        )
    return _conv_data(entries, cursor="more" if replies else None)


def _gql_url(op: str, qid: str, variables: dict | None = None, features: dict | None = None) -> str:
    """构造截获形态的 GraphQL URL（variables/features 全在查询参数里）。"""
    params = {"variables": json.dumps(variables or {"count": 20})}
    if features:
        params["features"] = json.dumps(features)
    return f"https://x.com/i/api/graphql/{qid}/{op}?{urllib.parse.urlencode(params)}"


def _mock(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    *,
    script_urls: list[str] | None = None,
) -> FakeTab:
    """conftest 共享 FakeTab 接管取数（chunk 收集走 markers）；_harvest/_HARVEST_WAIT 复位。"""
    tab = FakeTab(
        on_fetch=lambda req: handler(req.url),
        markers=[("script[src]", lambda arg: list(script_urls or []))],
    )
    monkeypatch.setattr(x_fetch, "_tab", tab)
    monkeypatch.setattr(x_fetch, "_harvest", {})
    monkeypatch.setattr(x_fetch, "_HARVEST_WAIT", 0)
    return tab


def _stub_chunks(monkeypatch: pytest.MonkeyPatch, texts: dict[str, str]) -> None:
    async def fake_dl(url: str) -> str | None:
        return texts.get(url)

    monkeypatch.setattr(x_fetch, "_download_chunk", fake_dl)


def _stub_drop(monkeypatch: pytest.MonkeyPatch) -> list:
    cleared: list = []

    async def fake_drop(url: str, names: tuple[str, ...]) -> None:
        cleared.append((url, names))

    monkeypatch.setattr(x_fetch, "drop_profile_cookies", fake_drop)
    return cleared


# ---- 种子起步（happy path 零收割成本）与收割自愈（失效触发） ----


def test_timeline_happy_path_uses_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """起步用种子快照（零收割、零启动延迟）；翻页 cursor 按次拼进 variables。"""

    def handler(url: str):
        return None, _tl_payload(["1", "2"], "cur-1")

    tab = _mock(monkeypatch, handler)
    entries, cursor = asyncio.run(fetch_entries("foryou", None))
    assert len(entries) == 3 and cursor == "cur-1"  # 2 推 + 1 游标条目
    assert x_fetch._SEEDS["HomeTimeline"].qid in tab.fetches[0].url  # 种子 qid
    assert tab.resets == 0  # happy path 不收割
    asyncio.run(fetch_entries("foryou", "cur-1"))
    assert "cur-1" in urllib.parse.unquote(tab.fetches[1].url)  # 翻页 cursor 进 variables


def test_parse_graphql_url_strips_per_call_vars() -> None:
    """收割的形状不烤死每次调用的参数（cursor/focalTweetId）：调用方按次拼接。"""
    url = _gql_url(
        "TweetDetail",
        "q1",
        variables={"focalTweetId": "20", "cursor": "abc", "rankingMode": "Relevance"},
        features={"responsive_web_grok_show_grok_translated_post": True},
    )
    op_name, op = _parse_graphql_url(url)
    assert op_name == "TweetDetail" and op.qid == "q1"
    assert op.variables == {"rankingMode": "Relevance"}
    assert op.features == {"responsive_web_grok_show_grok_translated_post": True}


# ---- 形状腐烂自愈（errors/400/404/422 → 重开页签重割 → 形状确实换过才重试） ----


def test_shape_rot_self_heals_via_chunk_grep(monkeypatch: pytest.MonkeyPatch) -> None:
    """queryId 轮换导致的 errors：重开页签重割（chunk 优先按名下载 grep 新 qid）后原参重试。"""
    fresh = "freshQID"
    chunk = "https://abs.twimg.com/responsive-web/client-web/shared~bundle.Compose~bundle.HomeTimeline~a1b2c3.js"
    tab = _mock(monkeypatch, lambda url: (None, _tl_payload(["1"], None)), script_urls=[chunk])
    _stub_chunks(monkeypatch, {chunk: f'queryId:"{fresh}",operationName:"HomeTimeline"'})

    def handler(url: str):
        if fresh not in url:  # 种子（旧部署 qid）报 errors
            return None, {"errors": [{"message": "Intentionally invalid queryId"}]}
        return None, _tl_payload(["1"], None)

    tab._on_fetch = lambda req: handler(req.url)
    entries, _cursor = asyncio.run(fetch_entries("foryou", None))
    assert entries
    assert x_fetch._SEEDS["HomeTimeline"].qid in tab.fetches[0].url and fresh in tab.fetches[1].url
    assert tab.resets == 1 and tab.opened == 1  # 重割重开了页签


def test_shape_rot_heals_via_plain_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """端点名不总在 chunk 名里（如 Following 的 qid 藏在普通 chunk）：全量兜底也割得到。"""
    grep_qid = "grepQID9"
    tab = _mock(
        monkeypatch,
        lambda url: (None, _tl_payload(["1"], None)),
        script_urls=["https://abs.twimg.com/responsive-web/client-web/bundle.js"],
    )
    _stub_chunks(
        monkeypatch,
        {
            "https://abs.twimg.com/responsive-web/client-web/bundle.js": f'queryId:"{grep_qid}",operationName:"HomeLatestTimeline" … queryId:"z",operationName:"Other"'
        },
    )

    def handler(url: str):
        if grep_qid not in url:
            return None, {"errors": [{"message": "stale"}]}  # 种子腐烂
        return None, _tl_payload(["1"], None)

    tab._on_fetch = lambda req: handler(req.url)
    entries, _cursor = asyncio.run(fetch_entries("following", None))
    assert entries and grep_qid in tab.fetches[1].url  # 普通 chunk 里 grep 出的新 qid 救场


def test_unhealed_shape_rot_gives_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    """重割也没换形状：不空转重试，给人话指引（脚本诊断/反馈维护者）。"""
    tab = _mock(monkeypatch, lambda url: (None, {"errors": [{"message": "bad"}]}))
    with pytest.raises(SourceError, match="自动收割未修复"):
        asyncio.run(fetch_entries("foryou", None))
    assert len(tab.fetches) == 1 and tab.resets == 1  # 形状没换：只重割不重试


# ---- 401/403/限流语义（与 httpx 时代一致） ----



def test_graphql_401_dead_session_self_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 = auth_token 被吊销（cookie 只是还在本地）：清失效 cookie + LoginRequired。"""
    cleared = _stub_drop(monkeypatch)
    _mock(monkeypatch, lambda url: (401, {"errors": [{"code": 32}]}))
    with pytest.raises(LoginRequired, match="登录已失效"):
        asyncio.run(fetch_entries("foryou", None))
    assert cleared == [("https://x.com", ("auth_token", "ct0"))]


def test_graphql_403_means_risk_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 = 风控/账号受限（重登救不了）：风控文案，不清 cookie 不引导重登。"""
    cleared = _stub_drop(monkeypatch)
    tab = _mock(monkeypatch, lambda url: (403, {"errors": [{"code": 64}]}))
    with pytest.raises(SourceError, match="风控"):
        asyncio.run(fetch_entries("foryou", None))
    assert cleared == [] and tab.resets == 0


def test_graphql_429_is_not_shape_rot(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流/抖动（429 等）不触发重割：形状没坏，等就是了。"""
    tab = _mock(
        monkeypatch,
        lambda url: (429, {"errors": [{"code": 88}]}),
    )
    with pytest.raises(SourceError, match="HTTP 429"):
        asyncio.run(fetch_entries("foryou", None))
    assert len(tab.fetches) == 1 and tab.resets == 0


# ---- TweetDetail：种子起步，失效懒深度截获 ----


def test_detail_uses_seed_then_deep_capture_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    """详情先用种子形状；失效时懒深度截获（稳定推文页截真实请求）拿到新形状重试。"""
    seed = x_fetch._SEEDS["TweetDetail"]
    captured = _Op(
        qid="capQID",
        variables={"withVoice": True},
        features={"responsive_web_grok_show_grok_translated_post": True},
    )
    tab = _mock(monkeypatch, lambda url: (None, _detail_payload("20", ["101", "102"])))

    def handler(url: str):
        if seed.qid in url:
            return None, {"errors": [{"message": "stale"}]}
        return None, _detail_payload("20", ["101", "102"])

    tab._on_fetch = lambda req: handler(req.url)

    async def fake_capture() -> _Op:
        return captured

    monkeypatch.setattr(x_fetch, "_deep_capture_detail", fake_capture)
    _entries, cursor = asyncio.run(fetch_conversation("20", None))
    assert cursor == "more"
    assert (
        seed.qid in tab.fetches[0].url and "capQID" in tab.fetches[1].url
    )  # 种子失败、截获形状成功


def test_deep_capture_parses_and_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    """深度截获的 URL 解析：qid/variables/features 还原，focalTweetId/cursor 剔除。"""
    url = _gql_url(
        "TweetDetail",
        "capQID",
        variables={"focalTweetId": "20", "rankingMode": "Relevance"},
        features={"f": True},
    )
    # _parse_graphql_url 已直测（test_parse_graphql_url_strips_per_call_vars），这里只钉住端点归属
    op_name, op = _parse_graphql_url(url)
    assert op_name == "TweetDetail" and op.qid == "capQID" and "focalTweetId" not in op.variables


# ---- 编排：判登门与 Loader 接线 ----


def test_require_login_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """判登门：auth_token/ct0 成对在才算登录（页签 cookie 由浏览器自带）。"""

    async def jar(url: str) -> dict[str, str]:
        return {"auth_token": "a", "ct0": "c"}

    monkeypatch.setattr("feedscope.core.browser.profile_cookies", jar)
    asyncio.run(x_mod.require_login("测试"))  # 成对在：直放

    async def empty(url: str) -> dict[str, str]:
        return {"auth_token": "a"}  # 只有 auth_token 没有 ct0：不算

    monkeypatch.setattr("feedscope.core.browser.profile_cookies", empty)
    with pytest.raises(LoginRequired, match="需要登录"):
        asyncio.run(x_mod.require_login("测试"))


def test_open_feed_wires_gate_and_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """open_feed 接线：过门后首批经通道页签取数（种子起步）。"""

    async def jar(url: str) -> dict[str, str]:
        return {"auth_token": "a", "ct0": "c"}

    monkeypatch.setattr("feedscope.core.browser.profile_cookies", jar)
    _mock(monkeypatch, lambda url: (None, _tl_payload(["1", "2"], None)))
    loader = asyncio.run(get_source("x").open_feed("foryou"))
    asyncio.run(loader.close())
    assert [it.id for it in loader.items] == ["status/1", "status/2"]


def test_feed_loader_cursor_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loader 语义：游标推进 + 按 id 去重 + 无 cursor 判死（编排无关，通道替身驱动）。"""
    calls: list[str | None] = []

    def handler(url: str):
        calls.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["variables"][0])
        return None, (
            _tl_payload(["1", "2"], "cur-1") if len(calls) == 1 else _tl_payload(["2", "3"], None)
        )

    _mock(monkeypatch, handler)
    loader = XFeedLoader("foryou")
    assert [it.id for it in asyncio.run(loader.load_more())] == ["status/1", "status/2"]
    assert [it.id for it in asyncio.run(loader.load_more())] == ["status/3"]  # 重复的 2 去重
    assert loader.done and asyncio.run(loader.load_more()) == []
