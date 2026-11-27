"""加载器与聚合编排：merge_feeds（「全部」流）、内存切片 loader、批次去重。

RSS / Telegram 的聚合流与各源的内存切片翻页都建在这几件上，纯内存、零请求。
"""

from __future__ import annotations

import asyncio

from feedscope.core.loaders import SliceFeedLoader, dedup_append
from feedscope.core.models import FeedItem
from feedscope.core.source import merge_feeds

# ---- merge_feeds：「全部」聚合的共享编排（core.source，RSS/Telegram 共用）----


def _m_item(id_: str, url: str = "", published: float = 0.0) -> FeedItem:
    return FeedItem(source="rss", id=id_, title=id_, url=url, extra={"published": published})


def test_merge_feeds_sorts_desc_and_isolates_failures() -> None:
    """倒序合并（稳定排序：同键保持订阅书写顺序）+ 单订阅失败隔离。"""

    async def one(feed) -> list[FeedItem]:
        if feed == "dead":
            raise RuntimeError("boom")
        if feed == "empty":
            return []  # 空订阅不是失败
        return [_m_item(f"{feed}-a", published=2.0), _m_item(f"{feed}-b", published=1.0)]

    merged, errors = asyncio.run(
        merge_feeds(
            ["dead", "first", "empty", "second"],
            one,
            error_label=lambda f: f"订阅{f}",
            sort_key=lambda it: float(it.extra.get("published") or 0.0),
        )
    )
    assert [it.id for it in merged] == ["first-a", "second-a", "first-b", "second-b"]  # 2.0 > 1.0，同键按书写序
    assert errors == ["订阅dead：boom"]  # 死源只损失自己


def test_merge_feeds_dedup_urls_keeps_first_written() -> None:
    """dedup_urls：同一 URL 被两个订阅覆盖只留书写顺序靠前的一份。"""

    async def one(feed) -> list[FeedItem]:
        return [_m_item(f"{feed}-x", url="https://same.example/a")] if feed == "one" else [
            _m_item(f"{feed}-x", url="https://same.example/a"),
            _m_item(f"{feed}-y", url="https://other.example/b"),
        ]

    merged, errors = asyncio.run(
        merge_feeds(["one", "two"], one, error_label=str, sort_key=lambda it: 0.0, dedup_urls=True)
    )
    assert [it.id for it in merged] == ["one-x", "two-y"]  # two 的重复 URL 被剔
    assert errors == []


def test_slice_feed_loader_pages_in_memory() -> None:
    """SliceFeedLoader：整批在内存、首屏一片、Ctrl+N 零请求增量（聚合流防冻）。

    feed_page 用小值驱动翻页路径（真实默认 100：约三屏一批）。"""
    from feedscope.core.models import FeedItem

    def _items(n: int) -> list[FeedItem]:
        return [FeedItem(source="rss", id=str(i), title=f"t{i}", url=f"u{i}") for i in range(n)]

    class _Narrow(SliceFeedLoader):
        feed_page = 3

    loader = _Narrow(_items(8))
    assert [it.id for it in loader.items] == ["0", "1", "2"], "首批只有一页"
    assert not loader.done
    assert [it.id for it in asyncio.run(loader.load_more())] == ["3", "4", "5"]
    assert not loader.done
    assert [it.id for it in asyncio.run(loader.load_more())] == ["6", "7"], "末批可短"
    assert loader.done and len(loader.items) == 8, "items = 已加载全部（不变量）"
    assert asyncio.run(loader.load_more()) == [], "到头后空批"

    assert SliceFeedLoader(_items(2)).done, "不足一页 = 打开即到头（小列表零行为变化）"


def test_dedup_append_drops_none_and_repeats() -> None:
    a = FeedItem(source="zhihu", id="x", title="t", url="u")
    b = FeedItem(source="zhihu", id="x", title="t2", url="u2")
    c = FeedItem(source="zhihu", id="y", title="t3", url="u3")
    items: list[FeedItem] = [a]
    seen = {a.id}
    new = dedup_append(items, seen, [a, None, b, c])
    assert items == [a, c]  # 重复/噪音不追加，新 id 追加
    assert new == [c]  # 返回值 = 新增部分（翻页 UI 只挂它）


