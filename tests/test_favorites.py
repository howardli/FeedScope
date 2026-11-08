"""收藏/稍后读的主线动线（stub 源，全离线）：Ctrl+F 标记 / F3 列表 / 详情内取消 / 下架源兜底 / CLI 导出。"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Label, ListView

from feedscope.core.models import FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.main import MainScreen

from conftest import last_notification, settle
from test_tui_smoke import StubSource, StubSourceTwo


@pytest.fixture()
def stub_registry():
    """只留 stub 源（同 test_read_state 的内联替换，见其注释）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _rows(app: FeedScopeApp, view_id: str = "#feed") -> list[str]:
    """指定列表逐行的圆点内容（主屏 #feed / 收藏屏 #fav-list）。

    app.query_one 落在默认屏（MainScreen）上：收藏屏的部件要经 app.screen
    查；settle 轮询期当前屏可能短暂不是收藏屏，查不到按空表重试。
    """
    from textual.css.query import NoMatches

    try:
        base = app.screen if view_id == "#fav-list" else app
        view = base.query_one(view_id, ListView)
    except NoMatches:
        return []
    return [str(row.query_one(".item-mark", Label).content) for row in view.children]


def test_star_from_main_browse_and_unstar(stub_registry) -> None:
    """主屏 Ctrl+F 收藏 → F3 见条目（源名前缀）→ Enter 读后圆点清（两屏同步）→ F3 内取消。"""
    asyncio.run(_run_star_flow())


async def _run_star_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+f")  # 收藏当前条目（第 0 条，topic 类型）
        assert last_notification(app) == "已收藏（F3 查看）"
        assert app.state.is_fav("stub", "i0")

        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        assert await settle(pilot, lambda: len(app.screen.items) == 1)
        view = app.screen.query_one("#fav-list", ListView)
        tag = view.children[0].query_one(".item-tag", Label).content
        assert str(tag) == "存根", "跨源列表里标出处（独立源名列）"
        score = view.children[0].query_one(".item-score", Label).content
        assert "存根" not in str(score), "元信息列回到各源原生形态（无源名前缀）"
        assert app.sub_title == "收藏 1 条"

        # Enter 进详情 = 打开即已读：退回后收藏行圆点清、主屏同一行也同步清
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        assert await settle(pilot, lambda: _rows(app, "#fav-list") == [""])
        assert await settle(pilot, lambda: _rows(app) == ["", "●", "●"]), "主屏同一行同步"

        # F3 内 Ctrl+F 取消：行消失、空态与计数到位
        await pilot.press("ctrl+f")
        assert last_notification(app) == "已取消收藏"
        assert await settle(pilot, lambda: app.screen.items == [])
        assert app.screen.query_one("#fav-empty", Label).display
        assert app.sub_title == "收藏 0 条"

        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert not app.state.is_fav("stub", "i0")


def test_unstar_from_detail_syncs_list(stub_registry) -> None:
    """详情屏里 Ctrl+F 取消收藏：退回 F3 后列表同步消失（ScreenResume 重建）。"""
    asyncio.run(_run_unstar_from_detail())


async def _run_unstar_from_detail() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+f")
        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("ctrl+f")  # 阅读中取消（PagedReadingScreen 的键位）
        assert last_notification(app) == "已取消收藏"
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        assert await settle(pilot, lambda: app.screen.items == [])


def test_unregistered_source_falls_back_to_browser(stub_registry, monkeypatch) -> None:
    """注册表里已不存在的源的收藏：行照常显示，Enter 兜底开浏览器。"""
    asyncio.run(_run_unregistered(monkeypatch))


async def _run_unregistered(monkeypatch) -> None:
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        # 直接造一条「下架源」的收藏（不经过主屏）
        app.state.toggle_fav(
            FeedItem(source="gone", id="x1", title="下架源的条目", url="https://gone.example/x1")
        )
        await pilot.press("f3")
        assert await settle(pilot, lambda: len(app.screen.items) == 1)
        await pilot.press("enter")
        assert opened == ["https://gone.example/x1"], "无源可解析时 Enter 兜底开浏览器"


def test_favorites_rows_use_shared_list_css(stub_registry) -> None:
    """收藏屏行样式与主屏同源：DEFAULT_CSS 不跨屏——没吃到共享行样式时行会被
    控件默认拉伸填满整屏（行高=列表高）、圆点列失去固定宽。同时钉住跨源
    列对齐：源名列成列、元信息列成列（跨源混排不参差）。"""

    async def flow() -> None:
        _REGISTRY[StubSourceTwo.source_id] = StubSourceTwo  # 跨源用例：第二个源（fixture 收尾整体还原）
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            app.state.toggle_fav(
                FeedItem(source="stub", id="s1", title="短", url="https://e.com/s1", meta="热")
            )
            app.state.toggle_fav(
                FeedItem(source="stub2", id="s2", title="更长的短标题", url="https://e.com/s2")
            )
            await pilot.press("f3")
            assert await settle(pilot, lambda: len(app.screen.items) == 2)
            rows = app.screen.query_one("#fav-list", ListView).children
            assert rows[0].region.height == 1, "短标题行高 1：被拉伸 = 共享行样式没生效"
            mark = rows[0].query_one(".item-mark", Label)
            assert mark.region.width == 2 and rows[0].styles.padding.right == 1
            # 跨源两行：源名列与元信息列的 x 坐标逐行一致（成列）
            tags = [r.query_one(".item-tag", Label) for r in rows]
            scores = [r.query_one(".item-score", Label) for r in rows]
            assert tags[0].region.x == tags[1].region.x, "源名列成列"
            assert scores[0].region.x == scores[1].region.x, "元信息列成列"
            assert {str(t.content) for t in tags} == {"存根", "存根二"}, "源名列显示词汇表名"

    asyncio.run(flow())


def test_footer_labels_are_translated(stub_registry) -> None:
    """zh 页脚两键是文案而非键名（retranslate 的既有纪律）。"""

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            assert app.main._bindings.key_to_bindings["ctrl+f"][0].description == "收藏/取消"
            assert app.main._bindings.key_to_bindings["f3"][0].description == "收藏列表"
            await pilot.press("f3")
            assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
            assert app.screen._bindings.key_to_bindings["ctrl+f"][0].description == "收藏/取消"

    asyncio.run(flow())


# ---- CLI：feedscope favorites [export [path]] ----


def test_cli_favorites_list_and_export(capsys, tmp_path) -> None:
    from feedscope import cli

    assert cli.main(["favorites"]) == 0
    assert "还没有收藏" in capsys.readouterr().out

    _star_two()
    assert cli.main(["favorites"]) == 0
    out = capsys.readouterr().out
    assert "乙标题" in out and "甲标题" in out and "收藏 2 条" in out

    assert cli.main(["favorites", "export"]) == 0
    md = capsys.readouterr().out
    assert "[甲标题](https://example.com/a) — stub" in md

    target = tmp_path / "fav.md"
    assert cli.main(["favorites", "export", str(target)]) == 0
    assert "已导出 2 条收藏" in capsys.readouterr().out
    assert "[乙标题]" in target.read_text(encoding="utf-8")


def _star_two() -> None:
    """造两条收藏（CLI 用例的数据准备，直接逐条 toggle）。"""
    from feedscope.core.store import ReadState

    st = ReadState()
    st.toggle_fav(FeedItem(source="stub", id="a", title="甲标题", url="https://example.com/a"))
    st.toggle_fav(FeedItem(source="stub", id="b", title="乙标题", url="https://example.com/b"))
