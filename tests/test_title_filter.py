"""/ 标题过滤与 [/] 未读导航（0.4.0，stub 源全离线）。

CJK 词没法经 Pilot 直注按键（press 吃键名不吃文本），输入层的值直接写进
Input.value 再回车——Input 的 enter 绑定按当前值发 Submitted，与真人输入同路。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import last_notification, settle
from test_tui_smoke import StubSource, StubSourceTwo
from textual.widgets import Input, Label, ListView

from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.main import MainScreen


@pytest.fixture()
def stub_registry():
    """只留主 stub 源（fixture 本地定义：跨模块导入会被 ruff 判未用删除，
    见 test_favorites 的同款注释）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


async def _filter_by(pilot, q: str) -> None:
    """按 / 弹输入层，填词回车（走 Input 的 enter 绑定，值即 Submitted 的值）。"""
    await pilot.press("/")
    await settle(pilot, lambda: pilot.app.screen.query("#input-line"))
    pilot.app.screen.query_one("#input-line", Input).value = q
    await pilot.press("enter")


@pytest.fixture()
def two_source_registry():
    """主源 + 三条不同标题的对照源（] 环扫要跨行验证）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    _REGISTRY[StubSourceTwo.source_id] = StubSourceTwo
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def test_title_filter_set_clear_and_esc(stub_registry) -> None:
    """设词过滤行集缩小、空提交清除、Esc 不动现有过滤。"""
    asyncio.run(_run_set_clear_esc())


async def _run_set_clear_esc() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await _filter_by(pilot, "第1条")
        assert await settle(pilot, lambda: len(app.main._shown()) == 1)
        assert app.main._shown()[0].id == "i1"
        assert app.sub_title.startswith("1 条")  # 「· N 新」随批附加，不在此断言
        assert last_notification(app) == "标题过滤：第1条"

        await _filter_by(pilot, "")  # 空提交 = 清除
        assert await settle(pilot, lambda: len(app.main._shown()) == 3)
        assert last_notification(app) == "标题过滤：已清除"

        await _filter_by(pilot, "第0条")
        assert await settle(pilot, lambda: len(app.main._shown()) == 1)
        await pilot.press("/")  # 再开输入层直接 Esc：过滤保持
        await settle(pilot, lambda: pilot.app.screen.query("#input-line"))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert app.main._shown()[0].id == "i0", "Esc 取消输入 ≠ 清除过滤"


def test_filter_composes_with_unread_only_and_empty_hint(stub_registry) -> None:
    """F4 与 / 是 AND 组合；过滤光行集给专属空态指路（比 F4 的更具体）。"""
    asyncio.run(_run_compose())


async def _run_compose() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        # F4 单独滤光：给 F4 那条指路（直接标 state 不重画——先标再按 F4 才进空态分支）
        app.state.mark_read("stub", "i0")
        app.state.mark_read("stub", "i1")
        app.state.mark_read("stub", "i2")
        await pilot.press("f4")
        assert await settle(
            pilot, lambda: "F4" in str(app.query_one("#empty-hint", Label).content)
        )
        # AND 组合：过滤命中 1 条未读语义外的东西——先关 F4 恢复全量再设过滤
        await pilot.press("f4")
        assert await settle(pilot, lambda: len(app.query_one("#feed", ListView).children) == 3)
        await _filter_by(pilot, "第1条")
        assert await settle(pilot, lambda: len(app.query_one("#feed", ListView).children) == 1)
        # 两开关并存且全滤光：给更具体的标题过滤那条（取消一条即可见另一条原因）
        await pilot.press("f4")
        assert await settle(
            pilot, lambda: "清除过滤" in str(app.query_one("#empty-hint", Label).content)
        )
        await pilot.press("f4")  # 关 F4：标题过滤还在但没滤光 → 恢复 1 行
        assert await settle(pilot, lambda: len(app.query_one("#feed", ListView).children) == 1)


def test_jump_unread_wraps_and_reports(two_source_registry) -> None:
    """] 环扫跳下一条未读（越界回绕）、[ 反向；全已读时 toast 收尾。"""
    asyncio.run(_run_jump())


async def _run_jump() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        view = app.query_one("#feed", ListView)
        app.state.mark_read("stub", "i0")  # 只有 i1/i2 未读
        await pilot.press("]")
        assert view.index == 1, "从 0 跳到首条未读"
        app.state.mark_read("stub", "i1")
        app.state.mark_read("stub", "i2")
        await pilot.press("]")
        assert view.index == 1, "全已读时不动选中（停在原地）"
        assert last_notification(app) == "没有未读条目"
        app.state.toggle_read("stub", "i0")  # 标回未读
        await pilot.press("[")
        assert view.index == 0, "反向环扫回绕到 0"


def test_load_more_under_filter_keeps_rows_aligned(stub_registry) -> None:
    """过滤态 Ctrl+N：行集重算而不是裸追加——修复前追加分支把不匹配词的新行
    直接挂上屏，行与 current_item()/_row_mark() 按 _shown() 的寻址从此错位。"""
    asyncio.run(_run_more_under_filter())


async def _run_more_under_filter() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await _filter_by(pilot, "第1条")
        assert await settle(pilot, lambda: len(app.query_one("#feed", ListView).children) == 1)
        await pilot.press("ctrl+n")  # 增量批 i10/i11（「第10条」不含子串「第1条」）
        assert await settle(pilot, lambda: len(app.main.items) == 5)
        view = app.query_one("#feed", ListView)
        assert len(view.children) == 1, "新批不匹配词：不该上屏成行"
        assert len(app.main._shown()) == 1
        assert app.main.current_item().id == "i1", "选中行仍指向匹配的那条"


def test_filter_word_with_markup_chars_is_escaped(stub_registry) -> None:
    """过滤词含 Rich markup 元字符（"["）：toast/空态原样展示词面——修复前
    方框对被 notify/Label 当样式标签解析吞掉。"""
    asyncio.run(_run_markup_word())


async def _run_markup_word() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await _filter_by(pilot, "[b]第1条")
        assert await settle(pilot, lambda: app.main._title_filter == "[b]第1条")
        assert "[b]第1条" in last_notification(app), "toast 里词面原样"
        assert "没有匹配" in str(app.main.query_one("#empty-hint", Label).content)
        assert "[b]第1条" in str(app.main.query_one("#empty-hint", Label).content)


def test_favorites_filter_and_status(stub_registry) -> None:
    """收藏屏 /：行集与计数跟随、状态行带过滤词、无匹配给专属空态。"""
    asyncio.run(_run_fav_filter())


async def _run_fav_filter() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+f")  # 收藏 i0
        await pilot.press("down")
        await pilot.press("ctrl+f")  # 收藏 i1（video，两个收藏）
        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        fav = app.screen
        assert await settle(pilot, lambda: len(fav.items) == 2)

        await _filter_by(pilot, "第1条")
        view = fav.query_one("#fav-list", ListView)
        assert await settle(pilot, lambda: len(view.children) == 1), "行集是异步重画"
        assert app.sub_title == '收藏 1 条 · 过滤「第1条」'

        await _filter_by(pilot, "不存在的词")
        assert await settle(pilot, lambda: not fav.query_one("#fav-list", ListView).children)
        assert "清除过滤" in str(fav.query_one("#fav-empty", Label).content)

        await _filter_by(pilot, "")
        assert await settle(pilot, lambda: len(fav._shown()) == 2)
        assert app.sub_title == "收藏 2 条"
