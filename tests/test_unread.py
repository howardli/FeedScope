"""未读管理三件套的主屏动线（stub 源，全离线）：边栏角标 / F4 只看未读 / Ctrl+Shift+R 清仓。"""

from __future__ import annotations

import asyncio
import re

import pytest
from textual.widgets import Label, ListView

from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.dialogs import ConfirmScreen
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.main import FeedNavItem, MainScreen

from conftest import last_notification, settle
from test_tui_smoke import StubSource


@pytest.fixture()
def stub_registry():
    """只留 stub 源（同 test_read_state 的内联替换，见其注释）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _badge(app: FeedScopeApp) -> str:
    """边栏第一个流项的完整文本（角标是 [dim]N[/dim] markup）。"""
    return str(app.main.query_one("#feed-nav-0", FeedNavItem).content)


def _badge_num(app: FeedScopeApp) -> int:
    """从角标 markup 里抽未读数（无角标 = 0）。"""
    m = re.search(r"\[dim\](\d+)\[/dim\]", _badge(app))
    return int(m.group(1)) if m else 0


def _rows(app: FeedScopeApp) -> list[str]:
    """主列表逐行圆点。"""
    view = app.main.query_one("#feed", ListView)
    return [str(r.query_one(".item-mark", Label).content) for r in view.children]


def test_sidebar_badge_decrements_live(stub_registry) -> None:
    """抓取后边栏带未读角标，Enter 读一条实时减一。"""
    asyncio.run(_run_badge())


async def _run_badge() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        assert await settle(pilot, lambda: _badge_num(app) == 3), _badge(app)
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert await settle(pilot, lambda: _badge_num(app) == 2), "读完一条角标实时减一"


def test_unread_only_filter_and_recovery(stub_registry) -> None:
    """F4 只剩未读行；读过的行直接消失；关掉恢复全量。"""
    asyncio.run(_run_filter())


async def _run_filter() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+r")  # 第 0 条标已读（行内切换，不进详情）
        assert await settle(pilot, lambda: _rows(app)[0] == "")

        await pilot.press("f4")
        assert last_notification(app) == "只看未读：开"
        assert await settle(pilot, lambda: _rows(app) == ["●", "●"]), "只剩两条未读"
        assert app.sub_title.startswith("2 条")

        # 过滤态里 Enter 读完一条：行直接消失（newsboat 语义），角标同减。
        # 过滤后首行是 i1（video 类型，Enter 跳浏览器）：下移到 i2（topic）进详情
        await pilot.press("down")
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert await settle(pilot, lambda: _rows(app) == ["●"]), "读过的行从过滤视图消失"
        assert await settle(pilot, lambda: _badge_num(app) == 1)

        await pilot.press("f4")
        assert last_notification(app) == "只看未读：关"
        assert await settle(pilot, lambda: len(_rows(app)) == 3), "关掉恢复全量"


def test_mark_all_read_with_confirm(stub_registry) -> None:
    """Ctrl+Shift+R 弹轻确认：确认后全量清空圆点、角标归零、过滤态清空。"""
    asyncio.run(_run_mark_all())


async def _run_mark_all() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+a")
        assert await settle(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.click("#confirm-ok")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert await settle(pilot, lambda: _rows(app) == ["", "", ""]), "全量圆点清空"
        assert await settle(pilot, lambda: _badge_num(app) == 0)
        assert last_notification(app) == "已读 3 条"
        assert app.state.unread_count("stub", "one") == 0

        # 过滤态下的清仓：全空 + 专属空态指路
        await pilot.press("f4")
        assert await settle(pilot, lambda: app.main._shown() == [])
        await settle(pilot, lambda: None)
        hint = app.main.query_one("#empty-hint", Label)
        assert hint.display and "F4" in str(hint.content)


def test_favorites_read_syncs_main_badge(stub_registry) -> None:
    """在收藏屏读一条：主屏同一行的圆点与边栏角标同步（跨屏路由）。"""
    asyncio.run(_run_cross_screen())


async def _run_cross_screen() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        assert await settle(pilot, lambda: _badge_num(app) == 3)
        await pilot.press("ctrl+f")
        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        await pilot.press("enter")  # 在收藏屏里读它
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert await settle(pilot, lambda: _rows(app) == ["", "●", "●"]), "主屏行同步"
        assert await settle(pilot, lambda: _badge_num(app) == 2), "主屏角标同步"


def test_toggle_read_in_filter_removes_row(stub_registry) -> None:
    """过滤态下 Ctrl+R 标已读：行消失、角标递减、状态行计数同步（曾漏接
    _sync_after_read——单行点圆点在过滤态失配，行不动角标也不动）。"""
    asyncio.run(_run_toggle_in_filter())


async def _run_toggle_in_filter() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("f4")
        assert await settle(pilot, lambda: _rows(app) == ["●", "●", "●"])
        assert await settle(pilot, lambda: _badge_num(app) == 3)

        await pilot.press("ctrl+r")  # 当前条目标已读
        assert await settle(pilot, lambda: _rows(app) == ["●", "●"]), "行从过滤视图消失"
        assert await settle(pilot, lambda: _badge_num(app) == 2), "角标递减"
        assert app.sub_title.startswith("2 条"), "状态行计数同步"


def test_load_more_updates_badge(stub_registry) -> None:
    """Ctrl+N 增量批：状态行与边栏角标都跟上（漏刷新边栏 = 停在旧值）。"""
    asyncio.run(_run_load_more_badge())


async def _run_load_more_badge() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        assert await settle(pilot, lambda: _badge_num(app) == 3)
        await pilot.press("ctrl+n")
        assert await settle(pilot, lambda: len(app.main.items) == 5)
        # 全新状态：首批 3 新 + 增量 2 新 = 本次会话累计 5 新（计数是累计的）
        assert app.sub_title == "5 条 · 5 新"
        assert await settle(pilot, lambda: _badge_num(app) == 5), "角标随增量批更新"


def test_footer_labels_are_translated(stub_registry) -> None:
    """zh 页脚两键是文案而非键名（retranslate 的既有纪律）。"""

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            assert app.main._bindings.key_to_bindings["f4"][0].description == "只看未读"
            assert (
                app.main._bindings.key_to_bindings["ctrl+a"][0].description == "全部已读"
            )
            visible = {
                key for key, bs in app.main._bindings.key_to_bindings.items() if bs[0].show
            }
            assert visible == {
                "f5", "f2", "f4", "ctrl+n", "ctrl+o", "ctrl+r", "ctrl+f",
            }, "页脚预算：只亮日常循环 7 键"
            assert not app.main._bindings.key_to_bindings["ctrl+a"][0].show  # 低频键照常命中

            await pilot.press("enter")
            assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
            assert app.screen._bindings.key_to_bindings["right"][0].description == "下一篇"
            assert app.screen._bindings.key_to_bindings["left"][0].description == "上一篇"

    asyncio.run(flow())
