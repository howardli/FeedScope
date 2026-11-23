"""阅读屏与收藏屏的未读键位 + 跨源换篇。

- 详情屏：]/[ 换到下一篇未读（跳过已读的、跳过 Enter 会跳浏览器的类型），
  Ctrl+R 把正在读的这篇切回未读；主列表的选中跟着走
- 收藏屏（稍后读队列）：]/[ 环扫到下一条未读，Ctrl+R 切未读——想回头细看的
  标回未读，这个队列里最要紧
- 跨源换篇：收藏屏的相邻条目来自不同源，详情屏要按条目换源对象（修复前
  拿旧源去开新条目）
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from conftest import last_notification, settle
from textual.css.query import NoMatches
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
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen


def _item(source: str, i: int, kind: str = "") -> FeedItem:
    return FeedItem(source=source, id=f"i{i}", title=f"{source} 第{i}条", url=f"https://e.com/{source}/{i}", kind=kind)


class DocSource(Source):
    """开详情 = 一份标题带「源:条目」标记的静态文档：换篇后看标题就知道用对了源、开对了条目。
    opened 是类级记录（屏内用的是 App 自己建的实例，记在类上才读得到）。"""

    supports_detail = True
    supports_comments = False
    detail_kinds = frozenset({""})  # video 这类 Enter 跳浏览器，不进详情
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    opened: ClassVar[list[str]] = []

    async def open_feed(self, name: str) -> FeedLoader:
        # KINDS 是 (kind, 序号)
        return StaticFeedLoader([_item(self.source_id, i, kind) for kind, i in self.KINDS])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        type(self).opened.append(f"{self.source_id}:{item.id}")
        return StaticDetailLoader(DetailDoc(title=f"{self.source_id}:{item.id}"))


class SourceA(DocSource):
    source_id = "ka"
    label = "源甲"
    KINDS = [("", 0), ("video", 1), ("", 2), ("", 3)]
    opened: ClassVar[list[str]] = []


class SourceB(DocSource):
    source_id = "kb"
    label = "源乙"
    KINDS = [("", 0), ("", 1)]
    opened: ClassVar[list[str]] = []


def _install(*classes: type[Source]) -> None:
    """注册表整体换成给定的 stub 源（结束后调用方 _restore 恢复）。"""
    _REGISTRY.clear()
    for cls in classes:
        _REGISTRY[cls.source_id] = cls


def _restore(saved: dict) -> None:
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _shown(app: FeedScopeApp, view_id: str) -> int:
    """当前屏列表的选中下标（App.query 只查当前屏，要从 app.screen 查）。"""
    return app.screen.query_one(view_id, ListView).index or 0


def _rows(app: FeedScopeApp) -> list:
    """收藏屏已挂载的行（屏还没挂上 / 还没建行时为空，供 settle 轮询）。"""
    try:
        return list(app.screen.query_one("#fav-list", ListView).children)
    except NoMatches:
        return []


# ---- 详情屏：]/[ 换未读篇、Ctrl+R 切未读 ----


def test_detail_brackets_move_to_next_unread_and_ctrl_r_toggles() -> None:
    saved = dict(_REGISTRY)
    _install(SourceA)
    SourceA.opened.clear()
    try:
        asyncio.run(_detail_flow())
    finally:
        _restore(saved)


async def _detail_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 4)
        await pilot.press("enter")  # 开 i0（打开即已读）
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)

        await pilot.press("]")  # 下一个未读且可进详情的：跳过 video 的 i1、跳过已读的 i0
        assert await settle(pilot, lambda: app.screen.item.id == "i2" and app.screen.loader)
        assert app.screen.loader.doc.title == "ka:i2"
        assert app.main.current_item().id == "i2", "主列表选中跟着走"

        await pilot.press("ctrl+r")  # 读到一半想回头细看：标回未读
        assert not app.state.is_read("ka", "i2") and "未读" in last_notification(app)

        await pilot.press("]")  # i2 自己是当前篇，不算「下一篇」：落到 i3
        assert await settle(pilot, lambda: app.screen.item.id == "i3" and app.screen.loader)
        await pilot.press("[")  # 往回：i2 刚被标回未读（换过去同样「打开即已读」）
        assert await settle(pilot, lambda: app.screen.item.id == "i2" and app.screen.loader)

        await pilot.press("]")  # i3 上一步已读、i2 刚读：沿方向再无未读，停在原地只提示
        await pilot.pause()
        assert app.screen.item.id == "i2" and "没有未读" in last_notification(app)


# ---- 收藏屏：]/[ 环扫、Ctrl+R 切未读、跨源换篇用对源 ----


def test_favorites_unread_keys_and_cross_source_stepping() -> None:
    saved = dict(_REGISTRY)
    _install(SourceA, SourceB)
    SourceA.opened.clear()
    SourceB.opened.clear()
    try:
        asyncio.run(_favorites_flow())
    finally:
        _restore(saved)


async def _favorites_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 4)
        for item in (_item("ka", 0), _item("kb", 0), _item("ka", 1, "video")):
            app.state.toggle_fav(item)  # 收藏时间新→旧：ka:video1、kb:i0、ka:i0
        app.state.mark_read("ka", "i1")  # 视频条目不进详情：预先标成已读，后面只剩 kb:i0 一条未读

        await pilot.press("f3")
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen) and len(_rows(app)) == 3)
        assert await settle(pilot, lambda: app.screen.query_one("#fav-list", ListView).has_focus)
        await pilot.press("down")  # 停在 kb:i0（ka 的 video 条目不进详情）
        assert _shown(app, "#fav-list") == 1
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        assert app.screen.loader.doc.title == "kb:i0"

        await pilot.press("right")  # 跨源换篇：下一可读收藏是 ka:i0，源对象要跟着换
        assert await settle(pilot, lambda: app.screen.item.id == "i0" and app.screen.loader)
        assert (app.screen.source.source_id, app.screen.loader.doc.title) == ("ka", "ka:i0")
        await pilot.press("left")  # 再换回乙源
        assert await settle(pilot, lambda: app.screen.item.id == "i0" and app.screen.loader)
        assert (app.screen.source.source_id, app.screen.loader.doc.title) == ("kb", "kb:i0")
        assert (SourceA.opened, SourceB.opened) == (
            ["ka:i0"],
            ["kb:i0", "kb:i0"],
        ), "各条目都由自己的源打开"

        await pilot.press("ctrl+r")  # 把 kb:i0 标回未读
        assert not app.state.is_read("kb", "i0")
        await pilot.press("escape")  # 回收藏屏
        assert await settle(pilot, lambda: isinstance(app.screen, FavoritesScreen))
        assert _shown(app, "#fav-list") == 1, "退回时选中停在读过的那条"
        await pilot.press("down")  # 到 ka:i0（已读）
        assert _shown(app, "#fav-list") == 2
        await pilot.press("]")  # 环扫到下一条未读：绕回去的 kb:i0
        await pilot.pause()
        assert _shown(app, "#fav-list") == 1
        await pilot.press("ctrl+r")  # 标成已读
        assert app.state.is_read("kb", "i0") and "已读" in last_notification(app)
        await pilot.press("]")  # 一圈再无未读
        await pilot.pause()
        assert _shown(app, "#fav-list") == 1 and "没有未读" in last_notification(app)
