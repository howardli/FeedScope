"""时间列与可配置时区（0.4.0）：相对时间函数、列宽算术、成列坐标钉子、配置键。"""

from __future__ import annotations

import asyncio
import time

from conftest import settle, use_config
from test_tui_smoke import StubSource, StubSourceTwo
from textual.widgets import ListView

from feedscope.core import config
from feedscope.core.models import FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.timefmt import (
    fmt_relative,
    fmt_unix,
    iso_epoch,
    set_timezone,
    twitter_epoch,
)
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.layout import time_col_width

_NOW = 1_750_000_000  # 2025-06-15（北京时间 23:06）：固定锚，断言与环境钟解耦
_MIN = 60
_HOUR = 3600
_DAY = 86400


def test_fmt_relative_tiers() -> None:
    assert fmt_relative(_NOW - 30, now=_NOW) == "0m"  # 分钟内（不造「刚刚」词）
    assert fmt_relative(_NOW - 59 * _MIN, now=_NOW) == "59m"
    assert fmt_relative(_NOW - 23 * _HOUR, now=_NOW) == "23h"
    assert fmt_relative(_NOW - 13 * _DAY, now=_NOW) == "13d"  # 14 天内相对计龄
    assert fmt_relative(None, now=_NOW) == ""
    assert fmt_relative(0, now=_NOW) == ""
    assert fmt_relative(10**18, now=_NOW) == ""  # 脏数据回空串


def test_fmt_relative_dates_beyond_two_weeks() -> None:
    # 锚点 2025-06-15：同为 2025 年 → MM-DD；更早年份 → YY-MM
    assert fmt_relative(_NOW - 15 * _DAY, now=_NOW) == "05-31"
    assert fmt_relative(_NOW - 400 * _DAY, now=_NOW) == "24-05"


def test_fmt_relative_future_clamps_to_zero_minutes() -> None:
    """未来时间（钟差/源侧脏数据）钳成 0m，不产出负数档位。"""
    assert fmt_relative(_NOW + 5 * _MIN, now=_NOW) == "0m"


def test_set_timezone_switches_and_rejects() -> None:
    try:
        set_timezone("Asia/Tokyo")  # UTC+9：比北京快一小时
        assert fmt_unix(1735689600) == "2025-01-01 09:00"
        set_timezone("Not/AZone")  # 非法 IANA 名：回退默认北京时间并记警告
        assert fmt_unix(1735689600) == "2025-01-01 08:00"
        set_timezone(None)  # 缺失 = 默认
        assert fmt_unix(1735689600) == "2025-01-01 08:00"
    finally:
        set_timezone(None)  # 全局态：测完归位，不漏给后续用例


def test_iso_and_twitter_epoch_helpers() -> None:
    assert iso_epoch("2025-01-01T00:00:00Z") == 1735689600.0
    assert iso_epoch("2025-01-01T01:00:00+01:00") == 1735689600.0
    assert iso_epoch("2025-01-01T00:00:00") == 1735689600.0  # 无标记当 UTC（机器格式）
    assert iso_epoch("") is None
    assert iso_epoch("随时") is None
    assert twitter_epoch("Wed Oct 15 08:23:11 +0000 2025") == 1760516591.0
    assert twitter_epoch("随便") is None
    assert twitter_epoch(None) is None


def test_time_col_width_batch_presence() -> None:
    with_time = [FeedItem("s", "a", "t", "u"), FeedItem("s", "b", "t", "u", published=_NOW)]
    assert time_col_width(with_time) == 6  # 定宽 5 + 1 间隙
    assert time_col_width([FeedItem("s", "a", "t", "u")]) == 0  # 批内全无 → 不占列
    assert time_col_width([]) == 0


def test_feed_item_published_roundtrip() -> None:
    it = FeedItem("s", "a", "t", "u", published=123.5)
    assert FeedItem.from_dict(it.to_dict()).published == 123.5
    legacy = {"source": "s", "id": "a", "title": "t", "url": "u"}  # 旧缓存无 published 键
    assert FeedItem.from_dict(legacy).published is None


def test_config_timezone_read_and_preserve(monkeypatch, tmp_path) -> None:
    use_config(
        monkeypatch, tmp_path, 'language = "zh-CN"\ntimezone = "Asia/Tokyo"\n[x]\nhide = ["a"]\n'
    )
    assert config.get_timezone() == "Asia/Tokyo"
    config.save_theme("editor-light")  # 别的保存路径不得抹掉 timezone
    assert config.get_timezone() == "Asia/Tokyo"
    use_config(monkeypatch, tmp_path, "timezone = 9\n")  # 非字符串：当未设置
    assert config.get_timezone() is None


class TimedStubSource(StubSource):
    """带 published 的 stub：时间列成列、且档位内容可断言。"""

    source_id = "stub_t"
    label = "存根时间"

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        now = time.time()
        return [
            FeedItem(
                "stub_t", f"i{i}", f"标题{i}", f"https://e.com/{i}", published=now - i * _HOUR
            )
            for i in range(2)
        ] + [FeedItem("stub_t", "i9", "无时间条目", "https://e.com/9")]


def test_time_column_renders_and_aligns(monkeypatch, tmp_path) -> None:
    """批内有 published → 时间列成列（x 坐标跨行一致、档位文本正确）；全无 → 列不出现。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[TimedStubSource.source_id] = TimedStubSource
    _REGISTRY[StubSourceTwo.source_id] = StubSourceTwo  # 对照源：列表无 published
    try:
        asyncio.run(_run_time_column())
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


async def _run_time_column() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        view = app.query_one("#feed", ListView)
        times = view.query(".item-time")
        assert len(times) == 3, "批内有 published：全部行都占时间列（无时间条目空占位）"
        assert len({t.region.x for t in times}) == 1, "时间列跨行 x 一致（成列）"
        texts = [str(w.content) for w in times]
        assert texts == ["0m", "1h", ""], "档位随时间距就位；无时间条目空占位（不串列）"

        # 对照：切到无 published 的源，列消失（源切换后重绘是异步 worker，等它落地）
        await pilot.press("tab")
        assert await settle(pilot, lambda: app.main.current_source.source_id == "stub2")
        assert await settle(
            pilot, lambda: not app.query_one("#feed", ListView).query(".item-time")
        )
