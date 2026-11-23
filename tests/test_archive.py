"""收藏即存档：阅读屏收藏时把「已读到的详情」一并存下，原帖失效后回退显示。

收藏存全量快照的初衷是「条目从热榜流走、回答被删之后仍可读」，但此前只有
RSS / Telegram 的正文在列表条目里；HN / 知乎 / X / Reddit / V2EX 的收藏只有
标题和链接，原帖一删就成死链。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from conftest import last_notification, settle

from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    LoginRequired,
    SliceDetailLoader,
    Source,
    StaticFeedLoader,
)
from feedscope.core.store import ReadState
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.answer import AnswerScreen
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen

_DOC = DetailDoc(
    title="一篇会消失的文章",
    meta="12 回答 · 3 万浏览",
    time="2026-11-01 09:00",
    description_md="问题描述的正文。",
    answers=[
        Answer(author="甲", votes="赞同 12", content_md="第一个回答的正文。", comment_count="3 条评论"),
        Answer(author="乙", votes="赞同 7", content_md="第二个回答的正文。"),
    ],
)


class VanishingSource(Source):
    """alive=False 时详情打不开（原帖被删 / 下线）；expired 时是会话失效（登录墙）。"""

    source_id = "vanish"
    label = "会消失源"
    supports_detail = True
    supports_comments = True
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    state: ClassVar[str] = "alive"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([FeedItem(source="vanish", id="p1", title="一篇会消失的文章", url="https://e.com/p1")])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        if self.state == "deleted":
            raise RuntimeError("原帖已删除")
        if self.state == "expired":
            raise LoginRequired("登录态已失效")
        return SliceDetailLoader(
            _DOC.answers, title=_DOC.title, meta=_DOC.meta, time=_DOC.time,
            description_md=_DOC.description_md,
            comment_trees=[[Comment("丙", "赞 2", "评论正文")], []],
        )


@pytest.fixture()
def source_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """只留会消失源，状态复位为「原帖还在」。结束恢复注册表与状态。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[VanishingSource.source_id] = VanishingSource
    VanishingSource.state = "alive"
    yield
    VanishingSource.state = "alive"
    _REGISTRY.clear()
    _REGISTRY.update(saved)


# ---- 存储层 ----


def test_doc_round_trips_through_the_favorite_snapshot() -> None:
    item = FeedItem(source="hn", id="1", title="t", url="https://e.com/1")
    st = ReadState()
    st.toggle_fav(item, _DOC)
    assert st.archive_of(item) == _DOC, "标题 / 正文 / 楼层逐字往返"
    assert [it.id for _, it in ReadState().favorites()] == ["1"], "收藏列表照常读到条目"
    st.toggle_fav(item)  # 取消
    assert st.archive_of(item) is None
    st.toggle_fav(item)  # 不带详情再收藏（列表屏的路径）：只有列表级快照
    assert st.archive_of(item) is None and st.is_fav("hn", "1")


def test_favorites_saved_before_archiving_still_load(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧版 favorites.json 没有 doc 字段：列表照常、archive_of 为 None，不迁移不报错。"""
    path = tmp_path / "favorites.json"
    path.write_text(
        '{"version": 1, "items": {"hn": {"1": {"fav": 1.0, "item": '
        '{"source": "hn", "id": "1", "title": "旧收藏", "url": "https://e.com/1"}'
        ', "doc": {"title": 42, "answers": "not-a-list"}}}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDSCOPE_FAVORITES", str(path))
    st = ReadState()
    item = FeedItem(source="hn", id="1", title="旧收藏", url="https://e.com/1")
    assert st.archive_of(item) == DetailDoc(title=""), "坏字段逐个回空，不炸整篇"


# ---- 阅读屏收藏随存详情 ----


def test_starring_from_the_reading_screens_stores_the_loaded_detail(source_state) -> None:
    asyncio.run(_star_flow())


async def _star_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        item = app.main.current_item()
        await pilot.press("enter")  # 问题屏
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        await pilot.press("ctrl+f")
        assert app.state.archive_of(item) == _DOC, "问题屏 Ctrl+F：已加载的正文与楼层存档"
        await pilot.press("ctrl+f")  # 取消
        assert app.state.archive_of(item) is None

        await pilot.press("enter")  # 进第一个回答的评论层
        assert await settle(pilot, lambda: isinstance(app.screen, AnswerScreen))
        await pilot.press("ctrl+f")
        assert app.state.archive_of(item) == _DOC, "评论层 Ctrl+F 收藏的是「这一篇」：同一份详情"


# ---- 原帖失效时回退存档 ----


def test_detail_falls_back_to_the_archive_when_the_post_is_gone(source_state) -> None:
    asyncio.run(_fallback_flow())


async def _fallback_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        await pilot.press("ctrl+f")  # 存档
        await pilot.press("escape")
        assert await settle(pilot, lambda: not isinstance(app.screen, DetailScreen))

        VanishingSource.state = "deleted"
        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen) and len(app.screen.items) == 1)
        await pilot.press("enter")  # 收藏屏 Enter：先试原帖，打不开就显示存档
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        screen = app.screen
        assert isinstance(screen, DetailScreen) and screen.loader.doc == _DOC
        assert screen._archived
        metas = [str(w.visual.plain) for w in screen.query_one("#detail-body").query(".read-meta")]
        assert metas == ["12 回答 · 3 万浏览 · 2026-11-01 09:00", "原文此刻打不开，显示的是收藏时的存档"]
        assert "已删除" not in last_notification(app), "回退存档不算失败，不弹错误"

        await pilot.press("enter")  # 存档里没有按需抓取的评论层
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen) and "评论层" in last_notification(app)


def test_login_wall_keeps_the_login_flow_not_the_archive(source_state) -> None:
    """会话失效走登录引导（重登就能看最新内容），不拿存档顶上。"""
    asyncio.run(_login_flow())


async def _login_flow() -> None:
    VanishingSource.state = "alive"
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        item = app.main.current_item()
        app.state.toggle_fav(item, _DOC)  # 已有存档
        VanishingSource.state = "expired"
        await pilot.press("enter")
        assert await settle(pilot, lambda: type(app.screen).__name__ == "AccountScreen"), "弹登录框"
        detail = next(s for s in app.screen_stack if isinstance(s, DetailScreen))
        assert detail.loader is None and not detail._archived
