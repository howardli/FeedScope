"""已读/未读 + 新条目标记 + 离线缓存的主屏动线（stub 源，全离线）。

跨实例 = 同一测试里再建一个 FeedScopeApp（FEEDSCOPE_STATE / FEEDSCOPE_FEED_CACHE
在 conftest 里按测试隔离，同测试内两个实例共享同一套文件——正是「重启」的形态）。
stub 源与注册表 fixture 复用 test_tui_smoke（同款跨测试模块 import 惯例）。
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Label, ListView

from feedscope.core import feed_cache
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import FeedLoader, SourceError
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.list_render import MARK_UNREAD
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.main import MainScreen

from conftest import last_notification, settle
from test_tui_smoke import StubSource


@pytest.fixture()
def stub_registry():
    """只留 stub 源（pytest fixture 不能跨模块直调，这里内联 test_tui_smoke 的同款替换）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _marks(app: FeedScopeApp) -> list[str]:
    """主列表逐行的圆点内容（行序 = items 序；空串 = 已读）。"""
    view = app.main.query_one("#feed", ListView)
    return [str(row.query_one(".item-mark", Label).content) for row in view.children]


def test_first_run_marks_then_read_on_open(stub_registry) -> None:
    """首跑全 ●（未读）→ Enter 打开即清点 → Ctrl+R 可标回 → 详情 ←/→ 换篇也计已读。"""
    asyncio.run(_run_first_flow())


async def _run_first_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        await _wait_items(pilot, app, 3)
        # 全新状态：三条全是未读 ●，状态行带新计数（首见 3 条）
        assert _marks(app) == [MARK_UNREAD] * 3
        assert app.sub_title == "3 条 · 3 新"

        # Enter 进详情 = 打开即已读；回列表该行圆点已清
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert _marks(app) == ["", MARK_UNREAD, MARK_UNREAD]

        # 详情屏 →：静默推进到下一篇可读条目（列表选中跟着走，也计已读）。
        # i1 是 video 类型（不在 detail_kinds，Enter 跳浏览器）：换篇跳过它到 i2
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("right")
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))
        assert _marks(app) == ["", MARK_UNREAD, ""], "←/→ 换篇的目标（i2）也计已读，i1 未动"
        assert app.main.query_one("#feed", ListView).index == 2, "列表选中停在读过的那篇"

        # Ctrl+R：未读 → 已读 → 再标回未读（圆点回来，与刷新无关的持久态）
        await pilot.press("up")  # 光标上移到 i1（视频行）
        await pilot.press("ctrl+r")
        assert _marks(app)[1] == ""
        assert last_notification(app) == "已标记已读"
        await pilot.press("ctrl+r")
        assert _marks(app)[1] == MARK_UNREAD
        assert last_notification(app) == "已标记未读"


def test_second_run_remembers_state_and_marks_new_batch(stub_registry) -> None:
    """重启（第二个 App 实例）：未读 ● 持久不降级，load_more 计数带「新」。"""
    asyncio.run(_run_second_flow())


async def _run_second_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        await _wait_items(pilot, app, 3)
        await pilot.press("enter")  # 第一条标已读
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen))
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, MainScreen))

    app2 = FeedScopeApp()  # 同一套 FEEDSCOPE_STATE / FEEDSCOPE_FEED_CACHE
    async with app2.run_test(size=(90, 36)) as pilot:
        await _wait_items(pilot, app2, 3)
        # 重新抓取同一批条目：读过的空，未读的 ● 原样保持（不因「又抓了一次」
        # 降级——圆点只随打开消失），且没有首见、「新」计数为零
        assert _marks(app2) == ["", MARK_UNREAD, MARK_UNREAD]
        assert app2.sub_title == "3 条"

        # Ctrl+N：增量批次是新面孔 → 状态行计数带「新」，新行同样是未读 ●
        await pilot.press("ctrl+n")
        await _wait_items(pilot, app2, 5)
        assert _marks(app2) == ["", MARK_UNREAD, MARK_UNREAD, MARK_UNREAD, MARK_UNREAD]
        assert app2.sub_title == "5 条 · 2 新"


def test_offline_restart_paints_cache_and_keeps_it_on_failure(
    stub_registry, monkeypatch
) -> None:
    """断网重启：先画缓存秒开；抓取失败不清屏，旧内容保留可翻。"""
    asyncio.run(_run_offline_flow(monkeypatch))


async def _run_offline_flow(monkeypatch) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        await _wait_items(pilot, app, 3)
    assert feed_cache.load("stub", "one") is not None, "成功刷新后缓存已落盘"

    async def boom(self, name: str) -> FeedLoader:
        raise SourceError("存根源 请求失败（HTTP 500）：断网模拟")

    monkeypatch.setattr(StubSource, "open_feed", boom)
    app2 = FeedScopeApp()
    async with app2.run_test(size=(90, 36)) as pilot:
        await settle(pilot, lambda: app2.sub_title == "加载失败")
        # 缓存行保留（不清屏、不亮空态），圆点按已读状态渲染（未读 ● / 已读空）
        assert len(app2.main.items) == 3
        assert _marks(app2) == [MARK_UNREAD] * 3
        hint = app2.main.query_one("#empty-hint", Label)
        assert not hint.display, "有缓存兜底时不亮空态"


def test_ctrl_r_footer_label_is_translated(stub_registry) -> None:
    """zh 下页脚 ctrl+r 是文案而非键名（retranslate 覆盖控件级键位的既有坑）。"""

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            await _wait_items(pilot, app, 3)
            assert app.main._bindings.key_to_bindings["ctrl+r"][0].description == "已读/未读"

    asyncio.run(flow())


def test_selected_row_mark_stays_visible(stub_registry) -> None:
    """选中行圆点不得被高亮底吃掉：高亮底=编辑器蓝=圆点本色，蓝叠蓝不可见——
    高亮态圆点改走选中文字的同源变量（rgb 与行文字一致、与高亮底不同）。"""

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            await _wait_items(pilot, app, 3)
            view = app.main.query_one("#feed", ListView)
            sel = view.children[view.index or 0]
            assert sel.has_class("-highlight"), "首行自动选中"
            mark = sel.query_one(".item-mark", Label)
            title = sel.query_one(".item-title", Label)
            assert mark.styles.color.rgb == title.styles.color.rgb, "与选中行文字同色源"
            assert mark.styles.color.rgb != sel.styles.background.rgb, "不再蓝叠蓝"

    asyncio.run(flow())


async def _wait_items(pilot, app: FeedScopeApp, n: int) -> None:
    assert await settle(pilot, lambda: len(app.main.items) == n), f"等 {n} 条超时"
