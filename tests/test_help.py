"""F1 帮助弹层：动线 + 自动生成不漏键（页脚预算装不下的键的去处）。"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Label

from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.help import HelpScreen
from feedscope.ui.screens.main import MainScreen

from conftest import settle
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


def _help_texts(app: FeedScopeApp) -> str:
    """帮助弹层全部文本（段名 + 行），一次抓齐。"""
    return "\n".join(str(label.content) for label in app.screen.query(Label))


def test_help_lists_hidden_keys_and_closes(stub_registry) -> None:
    """F1 开帮助：全局段 + 列表段齐备，页脚隐藏的键（F2/F3/Ctrl+A/Ctrl+T/Ctrl+Y）
    都在；Esc 关闭后回主屏。"""
    asyncio.run(_run_help_flow())


async def _run_help_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("f1")
        assert await settle(pilot, lambda: isinstance(app.screen, HelpScreen))
        text = _help_texts(app)
        for expect in (
            "全局", "导航", "内容", "已读与未读", "收藏", "配置",
            "源管理", "收藏列表", "全部已读", "主题", "复制链接", "退出",
            "切站", "移动选中", "返回",  # 控件级（tab）与导航键也在
        ):
            assert expect in text, f"帮助页缺：{expect}"
        # 分组有条理：组序固定（导航 → 内容 → 已读 → 收藏 → 配置），
        # 组内键随声明序但组间绝不穿插
        assert text.index("导航") < text.index("内容") < text.index("已读与未读")
        assert text.index("已读与未读") < text.index("收藏") < text.index("配置")
        assert text.index("切站") < text.index("刷新"), "导航组的键在内容组之前"
        assert "Focus Next" not in text, "Textual 继承默认键不进帮助页"
        assert "Focus Previous" not in text
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))


def test_modal_dialogs_are_centered(stub_registry) -> None:
    """弹层居中（ModalScreen 默认不含对齐——ArrowModalScreen 基类补的，
    曾全族顶在左缘；按左右余量相等断言，屏宽无关）。"""
    asyncio.run(_run_centering())


async def _run_centering() -> None:
    from feedscope.ui.screens.theme import ThemeScreen

    app = FeedScopeApp()
    async with app.run_test(size=(120, 38)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("f1")
        assert await settle(pilot, lambda: isinstance(app.screen, HelpScreen))
        d = app.screen.query_one("#help-dialog")
        left = d.region.x
        right = 120 - left - d.region.width
        assert left == right, f"帮助弹层不居中：左 {left} 右 {right}"
        await pilot.press("escape")
        await pilot.press("ctrl+t")
        assert await settle(pilot, lambda: isinstance(app.screen, ThemeScreen))
        d = app.screen.query_one("#theme-dialog")
        left = d.region.x
        right = 120 - left - d.region.width
        assert left == right, f"主题弹层不居中：左 {left} 右 {right}"


def test_help_on_reading_and_favorites(stub_registry) -> None:
    """详情屏与收藏屏的 F1：段名随屏换（阅读 / 收藏列表），Esc 返回原屏。"""
    asyncio.run(_run_help_screens())


async def _run_help_screens() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("f1")
        assert await settle(pilot, lambda: isinstance(app.screen, HelpScreen))
        detail_text = _help_texts(app)
        assert "导航" in detail_text and "收藏" in detail_text, "同一套分组条理"
        assert "换篇" not in detail_text or detail_text.index("导航") < detail_text.index("换篇")
        await pilot.press("escape")  # 关帮助
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))

        await pilot.press("escape")  # 退回主屏
        await pilot.press("ctrl+f")
        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        await pilot.press("f1")
        assert await settle(pilot, lambda: isinstance(app.screen, HelpScreen))
        fav_text = _help_texts(app)
        assert "导航" in fav_text and "收藏" in fav_text, "同一套分组条理"
