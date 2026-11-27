"""跨重启续读位置：退出时记下源 / 流 / 选中条目，下次启动回到这里。

位置存 state.json 顶层 "last"。条目按 id 定位而不是记下标——两次启动之间新到的
条目会把它往下顶。目标要活到启动抓取定稿那一次上屏（缓存先画的那轮不算数），
期间用户自己挪了地方就作废，迟到的定稿不能把选中拽回去。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import ClassVar

from textual.widgets import ListView

from feedscope.core.loaders import DetailLoader, FeedLoader, StaticDetailLoader, StaticFeedLoader
from feedscope.core.models import DetailDoc, FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import Source
from feedscope.core.store import ReadState, state_path
from feedscope.ui.app import FeedScopeApp
from tests.support.tui import settle

_BASE_IDS = ("i0", "i1", "i2", "i3")


class ResumeSource(Source):
    """两个流的列表源；arrived 模拟两次启动之间顶上新到的条目，gate 卡住启动抓取。"""

    source_id = "ka"
    label = "续读源"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["main", "other"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主", "other": "副"}
    arrived: ClassVar[list[str]] = []
    gate: ClassVar[asyncio.Event | None] = None

    @staticmethod
    def _item(feed: str, n: str) -> FeedItem:
        return FeedItem(
            source="ka", id=f"{feed}-{n}", title=f"{feed} 的 {n}", url=f"https://e.com/{feed}/{n}"
        )

    async def open_feed(self, name: str) -> FeedLoader:
        if self.gate is not None:
            await self.gate.wait()
        return StaticFeedLoader([self._item(name, n) for n in (*self.arrived, *_BASE_IDS)])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return StaticDetailLoader(DetailDoc(title=item.title))


class SecondSource(ResumeSource):
    source_id = "kb"
    label = "第二源"
    arrived: ClassVar[list[str]] = []
    gate: ClassVar[asyncio.Event | None] = None


@contextmanager
def _sources(*classes: type[Source]) -> Iterator[None]:
    """注册表整体换成给定的 stub 源，并复位模拟开关；结束恢复。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    for cls in classes:
        _REGISTRY[cls.source_id] = cls
    ResumeSource.arrived, ResumeSource.gate = [], None
    SecondSource.arrived, SecondSource.gate = [], None
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


def _settled(app: FeedScopeApp) -> bool:
    """启动抓取已定稿上屏（缓存先画那一轮的状态行不是计数文案，不算定稿）。"""
    main = app.main
    return main is not None and main.flow.loader is not None and app.sub_title == main.flow.count_text()


def _selected(app: FeedScopeApp) -> str | None:
    item = app.main.current_item()
    return item.id if item is not None else None


def _index(app: FeedScopeApp) -> int | None:
    return app.main.query_one("#feed", ListView).index


_Act = Callable[..., Awaitable[None]]  # (app, pilot)：切站 / 切流后要用 pilot 等新流定稿


async def _session(act: _Act) -> None:
    """跑一次完整的「启动 → 操作 → 退出」；退出走正常卸载路径（位置在那里保存）。"""
    app = FeedScopeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        assert await settle(pilot, lambda: _settled(app)), "启动抓取定稿"
        await act(app, pilot)
    await asyncio.sleep(0)  # 让退出后的收尾消息跑完再读状态文件


# ---- 存储层 ----


def test_position_rides_along_with_the_next_flush_and_reads_back() -> None:
    st = ReadState()
    st.mark_read("ka", "main-i0")  # 这次落盘之前没记过位置：文件里不该有 last 键
    assert "last" not in json.loads(state_path().read_text(encoding="utf-8"))

    st.hold_position("ka", "main", "main-i2")  # 只改内存
    assert "last" not in state_path().read_text(encoding="utf-8"), "hold 不落盘"
    st.mark_read("ka", "main-i2")  # 标已读的落盘把它带上
    assert ReadState().last_position() == {"source": "ka", "feed": "main", "id": "main-i2"}

    st.save_position("kb", "only", "")  # 立刻落盘（退出路径），空条目也原样记
    assert ReadState().last_position() == {"source": "kb", "feed": "only", "id": ""}


def test_position_saved_on_a_fresh_instance_is_not_lost_to_lazy_loading() -> None:
    """回归：实例还没从磁盘加载过就记位置——曾先改内存再落盘，落盘时的惰性加载把刚记的
    位置冲回了磁盘上的旧值，等于没存。"""
    ReadState().save_position("kb", "main", "main-i3")
    assert ReadState().last_position() == {"source": "kb", "feed": "main", "id": "main-i3"}
    fresh = ReadState()  # hold 同理：随后的第一次落盘必须把它带上
    fresh.hold_position("kb", "other", "other-i1")
    fresh.mark_read("kb", "other-i1")
    assert ReadState().last_position() == {"source": "kb", "feed": "other", "id": "other-i1"}


def test_missing_or_damaged_position_is_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FEEDSCOPE_STATE", str(tmp_path / "state.json"))
    assert ReadState().last_position() == {}, "没有状态文件 = 没记过"
    state_path().write_text(
        json.dumps(
            {"version": 1, "items": {}, "feeds": {}, "last": {"source": 5, "feed": "x", "extra": "y"}}
        ),
        encoding="utf-8",
    )
    assert ReadState().last_position() == {"feed": "x"}, "非字符串的值与未知键逐个丢弃"
    state_path().write_text(
        json.dumps({"version": 1, "items": {}, "feeds": {}, "last": "ka"}), encoding="utf-8"
    )
    assert ReadState().last_position() == {}, "last 不是对象 = 没记过"


# ---- 两次启动 ----


async def _move_down_twice(app, pilot) -> None:
    assert _selected(app) == "main-i0", "全新安装：停在第一条"
    await pilot.press("down")
    await pilot.press("down")
    assert _index(app) == 2


def _expect(
    feed: str, item_id: str, *, index: int, source: str = "ka"
) -> _Act:
    """第二次启动的断言：回到给定的源 / 流，选中落在给定的条目（下标随之）。"""

    async def check(app, pilot) -> None:
        main = app.main
        assert (main.current_source.source_id, main.current_feed) == (source, feed)
        assert _selected(app) == item_id, "选中落在上次停下的那条上"
        assert _index(app) == index
        assert main.resume.target is None, "续读目标在抓取定稿那一轮用掉"

    return check


def test_restart_lands_on_the_same_source_feed_and_item() -> None:
    with _sources(ResumeSource):
        asyncio.run(_session(_move_down_twice))
        assert ReadState().last_position() == {"source": "ka", "feed": "main", "id": "main-i2"}
        asyncio.run(_session(_expect("main", "main-i2", index=2)))


def test_item_is_located_by_id_so_new_arrivals_above_do_not_matter() -> None:
    with _sources(ResumeSource):
        asyncio.run(_session(_move_down_twice))
        ResumeSource.arrived = ["n1", "n2", "n3"]  # 两次启动之间顶上来了三条
        asyncio.run(_session(_expect("main", "main-i2", index=5)))


def test_item_scrolled_off_and_missing_source_or_feed_fall_back() -> None:
    with _sources(ResumeSource, SecondSource):
        ReadState().save_position("ka", "main", "gone-i9")
        asyncio.run(_session(_expect("main", "main-i0", index=0)))  # 条目已滚出列表：回第一条

        ReadState().save_position("zz", "main", "main-i2")  # 上次的源已停用 / 下架：回第一个源
        asyncio.run(_session(_expect("main", "main-i0", index=0)))

        # 流已不存在：回该站的第一个流；条目目标属于那个已不存在的流，一并作废
        ReadState().save_position("kb", "no-such-feed", "main-i1")
        asyncio.run(_session(_expect("main", "main-i0", index=0, source="kb")))


def test_restart_lands_on_the_last_source_and_feed() -> None:
    with _sources(ResumeSource, SecondSource):
        asyncio.run(_session(_switch_source_and_feed))
        assert ReadState().last_position() == {"source": "kb", "feed": "other", "id": "other-i0"}

        async def check(app, pilot) -> None:
            main = app.main
            assert (main.current_source.source_id, main.current_feed) == ("kb", "other")
            assert main._feed_memory == {"kb": 1}, "切走再切回来还停在这个流"

        asyncio.run(_session(check))


async def _switch_source_and_feed(app, pilot) -> None:
    await app.main.action_select_source(1)  # 切到第二个源（刷新在 worker 里，不等它）
    await app.main.action_select_feed(1)  # 再切到它的第二个流（后一次刷新取代前一次）
    assert await settle(pilot, lambda: _settled(app) and len(app.main.items) == 4), "新流抓取定稿"
    assert app.main.current_feed == "other" and _selected(app) == "other-i0"


def test_opening_an_item_records_the_position_right_away() -> None:
    """打开条目 = 「我在读这儿」：当场记下（随标已读的落盘写出），异常退出也丢不了。"""
    with _sources(ResumeSource):
        asyncio.run(_session(_open_second))
        assert ReadState().last_position() == {"source": "ka", "feed": "main", "id": "main-i1"}


async def _open_second(app, pilot) -> None:
    await pilot.press("down")
    item = app.main.current_item()
    assert item.id == "main-i1"
    app.main._mark_read(item)  # 与 Enter 进详情同一条路径（不开屏：这里只关心位置）
    assert ReadState().last_position()["id"] == "main-i1", "不必等退出：打开即落盘"


def test_moving_before_the_startup_fetch_settles_retires_the_resume_target() -> None:
    """缓存先画时选中落在续读的那条上；此时用户挪走了，续读目标作废——迟到的抓取定稿
    与之后的刷新都不会再把选中拽回原来那条（定稿照既有行为回第一条）。"""
    with _sources(ResumeSource):
        asyncio.run(_session(_move_down_twice))  # 第一次：停在 i2，并留下列表缓存
        gate = asyncio.Event()
        ResumeSource.gate = gate
        asyncio.run(_moved_then_fetched(gate))


async def _moved_then_fetched(gate: asyncio.Event) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 24)) as pilot:

        def cache_painted() -> bool:
            return bool(app.main.items) and app.main.flow.loader is None and _index(app) == 2

        assert await settle(pilot, cache_painted), "缓存先画：选中已落在续读的那条（下标 2）"
        assert app.main.resume.target == "main-i2", "目标还没用掉：在等抓取定稿"

        await pilot.press("down")
        assert _index(app) == 3 and app.main.resume.target is None, "用户挪了：目标作废"

        gate.set()  # 启动抓取这才回来
        assert await settle(pilot, lambda: _settled(app))
        assert _selected(app) != "main-i2", "定稿没有把选中拽回续读的那条"

        await pilot.press("f5")  # 目标已作废：之后的刷新也不会把它翻出来
        assert await settle(pilot, lambda: _settled(app))
        assert _selected(app) != "main-i2" and app.main.resume.target is None
