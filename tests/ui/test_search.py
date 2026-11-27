"""本地搜索（主屏 F6）：core.search 的筛选规则 + 搜索屏的输入即搜与跨源进详情。

数据是现成的：各流的列表缓存（feed_cache）+ 收藏快照。路线图里「全文搜索已
缓存条目」这一项在这里落地——标题级对所有源生效，正文级对带正文的 RSS /
Telegram 生效。
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, ListView

from feedscope.core import feed_cache, search
from feedscope.core.models import FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.search import SearchScreen
from tests.support.tui import last_notification, settle


def _item(source: str, i: int, title: str, published: float | None = None, **extra) -> FeedItem:
    return FeedItem(
        source=source,
        id=f"i{i}",
        title=title,
        url=f"https://e.com/{source}/{i}",
        published=published,
        extra=extra,
    )


# ---- 筛选规则 ----


def test_search_title_hits_first_then_body_hits_each_newest_first() -> None:
    items = [
        _item("hn", 1, "Python 3.15 released", published=300),
        _item("rss", 2, "Weekly notes", published=400, content_html="<p>all about <b>Rust</b> &amp; borrow</p>"),
        _item("hn", 3, "Rust 1.85 released", published=200),
        _item("x", 4, "rust-analyzer tips", published=100),
        _item("hn", 5, "unrelated", published=500),
    ]
    assert [it.id for it in search.search(items, "rust")] == ["i3", "i4", "i2"], (
        "标题命中在前（新的在前），正文命中在后"
    )
    assert [it.id for it in search.search(items, "RUST BORROW")] == ["i2"], "多词全部命中（AND），大小写不敏感"
    assert search.search(items, "rust") == search.search(items, "  ruST ")
    assert search.search(items, "nothing-here") == []
    assert search.search(items, "   ") == [], "空关键词 = 不搜"


def test_collect_merges_favorites_and_cache_dedup_and_skips_broken_files() -> None:
    feed_cache.save("rss", "all", [_item("rss", 1, "同一篇"), _item("rss", 2, "只在这里")])
    feed_cache.save("rss", "blog", [_item("rss", 1, "同一篇"), _item("rss", 3, "订阅流独有")])
    feed_cache.save("hn", "front", [_item("hn", 9, "一个 HN 帖子")])
    (feed_cache.cache_dir() / "broken.json").write_text("{ not json", encoding="utf-8")
    items = search.collect([_item("x", 7, "一条收藏")])
    assert [(it.source, it.id) for it in items] == [
        ("x", "i7"),
        ("hn", "i9"),
        ("rss", "i1"),
        ("rss", "i2"),
        ("rss", "i3"),
    ], "收藏在前；随后按缓存文件名序；同一 (source, id) 只留首次；坏文件只跳过自己"
    assert {(source, feed) for source, feed, _ in feed_cache.load_all()} == {
        ("rss", "all"),
        ("rss", "blog"),
        ("hn", "front"),
    }


# ---- 搜索屏 ----


def test_f6_searches_and_opens_a_result_through_its_own_source() -> None:
    from tests.support.stubs import SourceA, SourceB

    # 流名用 seed、序号避开 0-3：主屏启动刷新 ka 的 main 流会覆盖同名缓存，而它的条目 id 也是 i0-i3
    feed_cache.save("ka", "seed", [_item("ka", 100, "Rust 1.85 released", published=200)])
    feed_cache.save(
        "kb",
        "seed",
        [
            _item("kb", 101, "rust-analyzer tips", published=100),
            _item("kb", 102, "Notes", content_html="<p>mentions the borrow checker</p>"),
        ],
    )
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[SourceA.source_id] = SourceA
    _REGISTRY[SourceB.source_id] = SourceB
    try:
        asyncio.run(_search_flow())
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


async def _type(pilot, text: str) -> None:
    for ch in text:
        await pilot.press(ch)
    await pilot.pause()


async def _clear(pilot, n: int) -> None:
    for _ in range(n):
        await pilot.press("backspace")
    await pilot.pause()


async def _open_search(pilot, app: FeedScopeApp) -> SearchScreen:
    await pilot.press("f6")
    assert await settle(pilot, lambda: isinstance(app.screen, SearchScreen))
    assert await settle(pilot, lambda: app.screen._items), "索引完成（收藏 + 各流缓存）"
    assert await settle(pilot, lambda: app.screen.focused is app.screen.query_one("#search-input", Input))
    return app.screen


async def _search_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(110, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 4)

        screen = await _open_search(pilot, app)
        view = screen.query_one("#search-list", ListView)
        empty = screen.query_one("#search-empty")
        assert "输入即搜" in str(empty.visual) and app.sub_title == "0 条结果"

        await _type(pilot, "rust")
        assert [(it.source, it.id) for it in screen._results] == [
            ("ka", "i100"),
            ("kb", "i101"),
        ], "标题命中：新的在前"
        assert len(view.children) == 2 and app.sub_title == "2 条结果"
        assert app.state.is_read("ka", "i100") is False, "搜索不动已读态"

        await _clear(pilot, 4)
        await _type(pilot, "borrow")
        assert [(it.source, it.id) for it in screen._results] == [("kb", "i102")], "正文命中"

        await _clear(pilot, 6)
        assert screen._results == [] and "输入即搜" in str(empty.visual), "清空关键词回到提示"
        await _type(pilot, "zzz")
        assert screen._results == [] and "没有匹配「zzz」" in str(empty.visual)

        await pilot.press("escape")
        assert await settle(pilot, lambda: not isinstance(app.screen, SearchScreen))

        # 再进来：↓ 进结果列表，Enter 跨源进详情要用对源
        screen = await _open_search(pilot, app)
        await _type(pilot, "rust")
        await pilot.press("down")
        assert await settle(pilot, lambda: screen.query_one("#search-list", ListView).has_focus)
        await pilot.press("down")  # 第二条：乙源
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        assert (app.screen.source.source_id, app.screen.loader.doc.title) == ("kb", "kb:i101")


def test_search_pages_results_and_steps_through_them_in_detail() -> None:
    """命中很多时分页挂载（Ctrl+N 放量）；详情屏 ←/→ 在结果之间换篇，每篇用自己的源。"""
    from tests.support.stubs import SourceB

    # 序号从 10 起：主屏刷新 kb 的 main 流会覆盖同名缓存，它的条目占了 i0 / i1
    feed_cache.save("kb", "seed", [_item("kb", i, f"hit number {i}", published=float(i)) for i in range(10, 140)])
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[SourceB.source_id] = SourceB
    try:
        asyncio.run(_paging_flow())
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


async def _paging_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(110, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 2)
        screen = await _open_search(pilot, app)
        view = screen.query_one("#search-list", ListView)
        await _type(pilot, "hit")
        assert len(screen._results) == 130
        assert await settle(pilot, lambda: len(view.children) == 100)
        assert app.sub_title == "130 条结果（显示 100）"
        await pilot.press("ctrl+n")
        assert await settle(pilot, lambda: len(view.children) == 130)
        assert app.sub_title == "130 条结果"
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert "没有更多" in last_notification(app)

        await pilot.press("down")  # 进结果列表（停在第一条：最新的 i129）
        await pilot.press("enter")
        assert await settle(pilot, lambda: isinstance(app.screen, DetailScreen) and app.screen.loader)
        assert app.screen.loader.doc.title == "kb:i139"
        await pilot.press("right")  # 换到下一条结果
        assert await settle(pilot, lambda: app.screen.loader and app.screen.loader.doc.title == "kb:i138")
        await pilot.press("left")
        assert await settle(pilot, lambda: app.screen.loader and app.screen.loader.doc.title == "kb:i139")
        await pilot.press("ctrl+y")
        assert last_notification(app) == "链接已复制"
        await pilot.press("escape")
        assert await settle(pilot, lambda: isinstance(app.screen, SearchScreen))
        assert await settle(pilot, lambda: screen._row_mark(screen._results[0]) == ""), "读过的那条退回后圆点清"
