"""聚合流（RSS/Telegram「全部」）源名列的主屏钉子：真注册表 + mock HTTP。

跨流混排时出处（订阅名/频道名）必须走独立源名列，而不是拼进元信息列
（收藏屏同款问题同款药方）；单源流不占列。数据层断言在
test_rss_parse / test_telegram_parse，这里钉主屏渲染拿到了 tag。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from textual.widgets import Label, ListView

from feedscope.core import http
from feedscope.ui.app import FeedScopeApp

from conftest import settle
from test_rss_parse import RSS_XML

_FEEDS = """
[rss.feeds]
a = { label = "甲报", url = "https://a.test/feed.xml" }
b = { label = "另一个很长的订阅名", url = "https://b.test/feed.xml" }
"""


@pytest.fixture()
def rss_all_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """两个订阅 + 默认聚合流启动；B 订阅回改写过链接的样例——同一份 XML
    会被「全部」流的跨订阅 URL 去重当成转载吞掉，必须内容不同。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = RSS_XML
        if request.url.host == "b.test":
            body = RSS_XML.replace("https://example.com/", "https://b.example.com/")
        return httpx.Response(200, text=body, request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = tmp_path / "sources.toml"
    cfg.write_text(f'language = "zh-CN"\nenabled = ["rss"]\n{_FEEDS}', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))


def test_all_stream_renders_tag_column(rss_all_env) -> None:
    """「全部」流：源名列渲染订阅名且成列；元信息列无前缀；单流不占列。"""

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(100, 36)) as pilot:
            # 默认停聚合流「全部」：等首批上屏（两个订阅 × 样例条目）
            assert await settle(pilot, lambda: len(app.main.items) >= 2)
            view = app.query_one("#feed", ListView)
            rows = list(view.children)
            tags = [r.query_one(".item-tag", Label) for r in rows]
            assert rows, "聚合流有内容"
            xs = {t.region.x for t in tags}
            assert len(xs) == 1, "源名列成列（x 唯一）"
            assert {str(t.content) for t in tags} == {"甲报", "另一个很长的订阅名"}
            for r in rows:
                assert "甲报" not in str(r.query_one(".item-score", Label).content)
                assert "另一个很长的订阅名" not in str(
                    r.query_one(".item-score", Label).content
                ), "元信息列无订阅名前缀"

            # 切到单订阅流（边栏第 2 项）：整屏同源，源名列消失（不占列）
            await pilot.press("left")  # 进边栏
            await pilot.press("down")  # 下移到第一个单订阅流
            await pilot.press("enter")  # 回列表并刷新
            assert await settle(pilot, lambda: len(app.main.items) >= 1)
            view = app.query_one("#feed", ListView)
            from textual.css.query import NoMatches

            for r in view.children:
                with pytest.raises(NoMatches):
                    r.query_one(".item-tag", Label)  # 单流行没有源名列部件

    asyncio.run(flow())
