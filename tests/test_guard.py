"""首屏 0 条守卫：取数成功但解析空 = 报「可能已改版」而非静默空列表。"""

from __future__ import annotations

import asyncio

import pytest

import feedscope.sources  # noqa: F401  确保真实源已注册（get_source 才能拿到 zhihu）
from feedscope.core.models import FeedItem
from feedscope.core.registry import get_source
from feedscope.core.source import SourceError, require_feed_items


def test_require_empty_raises_with_source_and_feed() -> None:
    with pytest.raises(SourceError) as ei:
        require_feed_items([], "X", "following")
    msg = str(ei.value)
    assert "X" in msg and "following" in msg and "改版" in msg and "feedscope probe" in msg


def test_require_empty_without_feed_name() -> None:
    with pytest.raises(SourceError, match="^知乎 解析到 0 条"):
        require_feed_items([], "知乎")


def test_require_passthrough() -> None:
    items = [FeedItem(source="zhihu", id="question/1", title="标题", url="https://zhihu.com/q/1")]
    assert require_feed_items(items, "知乎", "hot") is items


def test_zhihu_open_feed_on_redesigned_page_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """集成：接口取回来但解析不出任何条目（改版响应）-> open_feed 报 SourceError。"""

    async def fake_api_json(url: str, referer: str) -> dict:
        return {"data": [], "paging": {"is_end": True, "next": ""}}  # 0 条

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._api_json", fake_api_json)

    with pytest.raises(SourceError, match="改版"):
        asyncio.run(get_source("zhihu").open_feed("recommend"))
