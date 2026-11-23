"""正文链接的键盘跟进：core.text.links_in + 阅读屏 Ctrl+G 的链接选择器。

[图片]/[视频]/@提及/原文 这些链接在 Markdown 控件里只能鼠标点——纯键盘用户
跟进不了。Ctrl+G 把屏上的链接按出现顺序列出来，↑↓ 选、Enter 在浏览器打开。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from conftest import last_notification, settle
from textual.widgets import ListView

from feedscope.core.models import DetailDoc, FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    Source,
    StaticDetailLoader,
    StaticFeedLoader,
)
from feedscope.core.text import links_in
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.links import LinkPickerScreen


def test_links_in_lists_inline_links_in_order_without_duplicates() -> None:
    md = (
        '正文 [原文](https://e.com/a?x=1&y=2 "标题") 与 ![图片](https://cdn/a.png)\n'
        "同一地址再来一次 [又是原文](https://e.com/a?x=1&y=2)\n"
        "自动链接 <https://e.com/b> 与锚点 [脚注](#fn) 与空地址 [空]()\n"
        "括号地址 [维基](<https://e.com/w/Foo_(bar)>)\n"
    )
    assert links_in(md) == [
        ("原文", "https://e.com/a?x=1&y=2"),
        ("图片", "https://cdn/a.png"),
        ("https://e.com/b", "https://e.com/b"),
        ("维基", "https://e.com/w/Foo_(bar)"),
    ]
    assert links_in("没有任何链接 [不是链接] (也不是)") == []
    assert links_in("") == []


class LinkedSource(Source):
    """详情正文带几个链接：@提及 / 图片 / 重复的图片地址 / 锚点。"""

    source_id = "linked"
    label = "带链接源"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    body = (
        "帖子正文，提到 [@someone](https://e.com/someone)。\n\n"
        "[图片](https://cdn.example/a.jpg)\n\n"
        "[图片](https://cdn.example/a.jpg)\n\n"  # 同一地址只列一次
        "[脚注](#fn)"
    )

    async def open_feed(self, name: str) -> FeedLoader:
        item = FeedItem(source=self.source_id, id="i0", title="一条带图的帖子", url="https://e.com/p/1")
        return StaticFeedLoader([item])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return StaticDetailLoader(DetailDoc(title="一条带图的帖子", description_md=self.body))


@pytest.fixture()
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """只留带链接的 stub 源；阅读屏的 open_link 换成记录器（不真开浏览器）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[LinkedSource.source_id] = LinkedSource
    urls: list[str] = []
    monkeypatch.setattr("feedscope.ui.screens.reading.open_link", lambda screen, url: urls.append(url))
    yield urls
    _REGISTRY.clear()
    _REGISTRY.update(saved)


async def _open_detail(pilot, app: FeedScopeApp) -> None:
    """等首批上屏后 Enter 进详情（loader 就绪即首载完成）。"""
    assert await settle(pilot, lambda: len(app.main.items) == 1)
    await pilot.press("enter")
    assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)


def test_ctrl_g_lists_body_links_and_opens_the_chosen_one(opened: list[str]) -> None:
    asyncio.run(_picker_flow(opened))


async def _picker_flow(opened: list[str]) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await _open_detail(pilot, app)

        await pilot.press("ctrl+g")
        assert await settle(pilot, lambda: isinstance(app.screen, LinkPickerScreen))
        assert await settle(pilot, lambda: app.screen.query_one("#links-list", ListView).has_focus)
        rows = app.screen.query_one("#links-list", ListView).children
        assert len(rows) == 2, "同一地址只列一次，锚点不算"
        await pilot.press("down")
        await pilot.press("enter")  # 选第二条（图片）
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        assert opened == ["https://cdn.example/a.jpg"]

        await pilot.press("ctrl+g")
        assert await settle(pilot, lambda: isinstance(app.screen, LinkPickerScreen))
        await pilot.press("escape")  # 不选
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        assert opened == ["https://cdn.example/a.jpg"], "Esc 不打开任何链接"


def test_ctrl_g_without_links_just_hints(opened: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LinkedSource, "body", "纯文字正文，没有链接")
    asyncio.run(_empty_flow(opened))


async def _empty_flow(opened: list[str]) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await _open_detail(pilot, app)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen), "没有链接不弹选择器"
        assert "没有链接" in last_notification(app) and opened == []
