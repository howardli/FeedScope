"""Telegram 源编排层测试：feeds 合成（订阅表驱动）、单频道 before 翻页、
「全部」聚合的合并/失败隔离、详情零请求、频道订阅增删（全 MockTransport）。

t.me 是纯 HTML 端点：Mock 回预览页 HTML（形态见 test_telegram_parse 的
fixture）；before 翻页与聚合合并是编排层的两块核心。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import recording_http, use_config
from test_telegram_parse import HTML

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.registry import get_source
from feedscope.core.source import SourceError
from feedscope.subscriptions import SubscriptionError, add_channel

# 两条频道订阅（feeds 合成 / 聚合流用）
_CFG = """\
[telegram.feeds]
alpha = { label = "频道甲", url = "https://t.me/alpha/" }
beta = { url = "https://t.me/beta/" }
"""






def _page(*posts: str, channel: str = "alpha", title: str = "Alpha Channel") -> str:
    """拼一页预览 HTML：每条消息用 data-post=频道/编号 的最小容器。"""
    body = "".join(
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text js-widget_message" data-post="{channel}/{p}">'
        f'<div class="tgme_widget_message_text js-message_text" dir="auto">消息 {p}</div>'
        f'<span class="tgme_widget_message_views">{p}K</span>'
        f'<time datetime="2026-07-{int(p):02d}T10:00:00+00:00"></time>'
        "</div></div>"
        for p in posts
    )
    return f'<html><head><meta property="og:title" content="{title}"/></head><body>{body}</body></html>'


def _tg_handler(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> list[httpx.Request]:
    """按 URL 的 before 参数分页回放：首页给 pages["first"]，before 命中键给旧页。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "t.me"
        before = request.url.params.get("before")
        key = f"before-{before}" if before else "first"
        if (page := pages.get(key)) is None:
            return httpx.Response(200, text=_page(), request=request)  # 未知页回空页（到头）
        return httpx.Response(200, text=page, request=request)

    return recording_http(monkeypatch, handler)


# ---- feeds 合成（订阅表驱动，与 RSS 同形）----


def test_feeds_composition_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    src = get_source("telegram")
    assert src.feeds == ["all", "alpha", "beta"]  # 聚合流在前，订阅按书写序
    assert src.feed_labels == {"all": "units.all", "alpha": "频道甲", "beta": "beta"}
    assert src.feed_label("all") == "全部" and src.feed_label("alpha") == "频道甲"
    assert src.subscribable and src.supports_detail and not src.supports_comments
    assert src.detail_unit == "正文"
    assert not src.login_url  # 免登录


def test_feeds_composition_hide_and_empty(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, '[telegram]\nhide = ["all", "beta"]\n' + _CFG)
    src = get_source("telegram")
    assert src.feeds == ["alpha"]  # 聚合流与单个订阅都可隐藏

    use_config(monkeypatch, tmp_path, "")
    assert get_source("telegram").feeds == []  # 零订阅 = 没有任何流（同 RSS）


def test_open_feed_unknown_name_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    with pytest.raises(ValueError, match="未知信息流"):
        asyncio.run(get_source("telegram").open_feed("nope"))


# ---- 单频道流：before 游标翻页 ----


def test_open_feed_pages_with_before_cursor(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    calls = _tg_handler(
        monkeypatch,
        {"first": _page("13", "14", "15"), "before-13": _page("11", "12")},
    )
    loader = asyncio.run(get_source("telegram").open_feed("alpha"))
    try:
        # 每页倒排、全局最新在前（与「全部」流及其他源的时间线方向一致）
        assert [it.id for it in loader.items] == ["alpha/15", "alpha/14", "alpha/13"]
        assert not loader.done
        assert asyncio.run(loader.load_more()) != []  # before=13 -> 11、12
        assert [it.id for it in loader.items] == [
            "alpha/15", "alpha/14", "alpha/13", "alpha/12", "alpha/11",
        ]
        assert asyncio.run(loader.load_more()) == []  # before=11 回空页：到头
        assert loader.done
    finally:
        asyncio.run(loader.close())
    assert [str(c.url.params.get("before") or "") for c in calls] == ["", "13", "11"]


def test_open_feed_empty_channel_gets_guidance(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """0 条大概率是频道不存在/为空/关了 Web 预览——给专属指引不报改版。"""
    use_config(monkeypatch, tmp_path, _CFG)
    recording_http(monkeypatch, lambda r: httpx.Response(200, text="<html></html>", request=r))
    with pytest.raises(SourceError, match="Web 预览"):
        asyncio.run(get_source("telegram").open_feed("alpha"))


# ---- 「全部」聚合流 ----


def test_open_feed_all_merges_by_time(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """聚合：多频道并发拉首页，按发布时间倒序合并，元信息列前缀频道名。"""
    use_config(monkeypatch, tmp_path, _CFG)

    def handler(request: httpx.Request) -> httpx.Response:
        # alpha 最新 15 号、beta 最新 14 号：合并后 15 在最前
        if request.url.path == "/s/alpha":
            return httpx.Response(200, text=_page("15"), request=request)
        return httpx.Response(200, text=_page("14", channel="beta"), request=request)

    recording_http(monkeypatch, handler)
    loader = asyncio.run(get_source("telegram").open_feed("all"))
    asyncio.run(loader.close())
    assert [it.id for it in loader.items] == ["alpha/15", "beta/14"]
    assert loader.items[0].meta.startswith("频道甲 · ")  # 聚合视图前缀频道名
    assert loader.done  # 聚合流是一次性快照（同 RSS）


def test_open_feed_all_partial_failure_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/s/beta":
            return httpx.Response(500, text="boom", request=request)
        return httpx.Response(200, text=_page("15"), request=request)

    recording_http(monkeypatch, handler)
    loader = asyncio.run(get_source("telegram").open_feed("all"))  # beta 死不拖垮 alpha
    asyncio.run(loader.close())
    assert [it.id for it in loader.items] == ["alpha/15"]

    recording_http(monkeypatch, lambda r: httpx.Response(500, text="boom", request=r))
    with pytest.raises(SourceError, match="全部拉取失败"):
        asyncio.run(get_source("telegram").open_feed("all"))


# ---- 详情：列表自带正文，零请求 ----


def test_open_detail_uses_list_extra(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, _CFG)
    calls = _tg_handler(monkeypatch, {"first": HTML.replace("durov/", "alpha/")})
    src = get_source("telegram")
    loader = asyncio.run(src.open_feed("alpha"))
    item = next(it for it in loader.items if it.id.endswith("/531"))  # 取指定那条（时间线已倒排）
    asyncio.run(loader.close())
    before = len(calls)
    detail = asyncio.run(src.open_detail(item))
    asyncio.run(detail.close())
    assert len(calls) == before  # 详情零请求
    doc = detail.doc
    assert doc.meta == "频道甲 · Pavel Durov"  # 频道名 + 署名
    assert doc.time == "2026-07-17 04:45"
    assert "Rich Text Editor" in doc.description_md
    assert detail.done


# ---- 频道订阅（subscriptions 编排）----


def test_add_channel_validates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import tomllib

    from feedscope.core import config

    p = use_config(monkeypatch, tmp_path, "enabled = []\n" + _CFG)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/s/gamma"
        return httpx.Response(200, text=_page("21", "22", title="Gamma News"), request=request)

    recording_http(monkeypatch, handler)
    feed = asyncio.run(add_channel("https://t.me/gamma"))
    assert (feed.key, feed.label) == ("gamma", "Gamma News")  # og:title 当默认显示名
    assert tomllib.loads(p.read_text(encoding="utf-8"))["enabled"] == ["telegram"]  # 加订阅自动启用

    for bad, match in (("alpha", "已经订阅"), ("不合法输入", "没认出")):
        with pytest.raises(SubscriptionError, match=match):
            asyncio.run(add_channel(bad))
    # all 保留字：TG 用户名最短 5 字符，合法输入到不了保留字检查——它防的是
    # 手编配置（源合成层 feeds_for 过滤已兜底），这里验证不误伤正常添加
    assert "all" not in [f.key for f in config.feeds_for("telegram")]
    assert [f.key for f in config.feeds_for("telegram")] == ["alpha", "beta", "gamma"]


def test_add_channel_rejects_unreadable_channel(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, "")
    recording_http(monkeypatch, lambda r: httpx.Response(200, text="<html>预览关闭页</html>", request=r))
    with pytest.raises(SubscriptionError, match="Web 预览"):
        asyncio.run(add_channel("ghost"))


def test_remove_and_rename_channel(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from feedscope.core import config
    from feedscope.subscriptions import remove_channel, rename_channel

    use_config(monkeypatch, tmp_path, "enabled = []\n" + _CFG)
    assert rename_channel("alpha", "  甲频道  ").label == "甲频道"  # 顺手 strip
    assert rename_channel("alpha", "   ") is None  # 空名不动配置
    assert config.feeds_for("telegram")[0].label == "甲频道"

    assert remove_channel("beta").key == "beta"
    assert [f.key for f in config.feeds_for("telegram")] == ["alpha"]
    assert remove_channel("beta") is None  # 再删不动配置
    # 删到空摘 enabled（没订阅就没有任何流，与 rss 同语义、与 reddit 相反）
    remove_channel("alpha")
    assert config.feeds_for("telegram") == [] and config.enabled_ids() == []


def test_update_feeds_roundtrip_preserved_on_save(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """F2/首启保存（save_enabled 全量重渲染）不丢 telegram 订阅表。"""
    from feedscope.core import config
    from feedscope.core.config import save_enabled

    use_config(monkeypatch, tmp_path, _CFG)
    save_enabled(["zhihu", "telegram"])
    assert [f.key for f in config.feeds_for("telegram")] == ["alpha", "beta"]
    assert [f.key for f in config.feeds_for("reddit")] == []  # 各源互不干扰
