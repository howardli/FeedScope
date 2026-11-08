"""core/feed_cache 列表缓存单元测试：每流一文件 / 往返 / 截断 / LRU / 容错。

env 隔离由 conftest._isolated_user_env 的 FEEDSCOPE_FEED_CACHE 负责。
FeedItem 的 to_dict/from_dict 往返也在这里钉（缓存是唯一消费方）。
"""

from __future__ import annotations

import json
import os

from feedscope.core import feed_cache
from feedscope.core.feed_cache import _file, cache_dir, load, save
from feedscope.core.models import FeedItem


def _items(n: int, source: str = "stub", feed: str = "one") -> list[FeedItem]:
    return [
        FeedItem(
            source=source,
            id=f"{feed}/{i}",
            title=f"第{i}条",
            url=f"https://example.com/{feed}/{i}",
            kind="topic",
            meta="3 回复",
            extra={"topic": {"id": i, "content": f"正文{i}"}, "author": "作者"},
        )
        for i in range(n)
    ]


def test_feeditem_roundtrip() -> None:
    """to_dict/from_dict 全字段往返（extra 原样，含嵌套 dict 与中文）。"""
    it = _items(1)[0]
    again = FeedItem.from_dict(it.to_dict())
    assert again == it
    # from_dict 对旧缓存宽容：缺字段 = 缺省值，未知键忽略
    legacy = FeedItem.from_dict({"source": "rss", "id": "k/1", "title": "t", "url": "u", "junk": 1})
    assert legacy == FeedItem(source="rss", id="k/1", title="t", url="u")
    assert legacy.extra == {}


def test_save_load_roundtrip() -> None:
    items = _items(3)
    save("stub", "one", items)
    assert load("stub", "one") == items  # 全字段等值（含 extra）


def test_missing_feed_returns_none() -> None:
    assert load("stub", "never") is None


def test_bad_file_returns_none() -> None:
    save("stub", "one", _items(1))
    _file("stub", "one").write_text("{ 坏 JSON", encoding="utf-8")
    assert load("stub", "one") is None


def test_wrong_shape_returns_none() -> None:
    cache_dir().mkdir(parents=True, exist_ok=True)
    _file("stub", "one").write_text(json.dumps({"version": 1, "items": "not-a-list"}), encoding="utf-8")
    assert load("stub", "one") is None


def test_empty_save_is_noop() -> None:
    """空列表不落盘：无可兜底的内容，也不该制造「缓存了 0 条」的假文件。"""
    save("stub", "one", [])
    assert not _file("stub", "one").exists()
    assert load("stub", "one") is None


def test_truncates_to_cap_keeping_head() -> None:
    """单流上限 200 条、保头部（各源列表都是新→旧序，尾部是更旧的历史）。"""
    save("stub", "one", _items(250))
    cached = load("stub", "one")
    assert cached is not None and len(cached) == 200
    assert cached[0].id == "one/0" and cached[-1].id == "one/199"


def test_per_feed_files_are_independent() -> None:
    """每流一文件：覆写一流不碰别流（单文件方案的写放大正是要消灭的）。"""
    save("stub", "one", _items(1))
    save("stub", "two", _items(1, feed="two"))
    p1, p2 = _file("stub", "one"), _file("stub", "two")
    assert p1.exists() and p2.exists() and p1 != p2
    save("stub", "one", _items(5))
    assert load("stub", "two") == _items(1, feed="two")  # two 原封不动


def test_chinese_feed_key_is_quoted_and_roundtrips() -> None:
    """中文订阅 key：文件名 quote 编码（路径安全），寻址走 (source, feed) 入参。"""
    items = _items(1, feed="阮一峰周刊")
    save("rss", "阮一峰周刊", items)
    assert load("rss", "阮一峰周刊") == items
    name = _file("rss", "阮一峰周刊").name
    assert "%" in name and "/" not in name  # 非 ASCII 都转义，路径分隔符进不了文件名


def test_lru_evicts_oldest_files(monkeypatch) -> None:
    """目录超上限按 mtime 淘汰最旧的流文件；不认识的文件不动。"""
    monkeypatch.setattr(feed_cache, "_MAX_FEEDS", 10)  # 先放宽：三个流安稳落盘
    for feed in ("a", "b", "c"):
        save("stub", feed, _items(1, feed=feed))
    # mtime 同秒粒度会平局：显式设三个可区分的时间
    for i, feed in enumerate(("a", "b", "c")):
        os.utime(_file("stub", feed), (1000 + i, 1000 + i))
    monkeypatch.setattr(feed_cache, "_MAX_FEEDS", 3)
    save("stub", "d", _items(1, feed="d"))  # 第 4 个流触发淘汰
    assert load("stub", "a") is None  # 最旧的走了
    assert load("stub", "b") is not None
    assert load("stub", "c") is not None
    assert load("stub", "d") is not None


def test_evict_ignores_non_json_neighbors() -> None:
    """缓存目录里非 .json 的邻居（理论上的手动放置文件）不参与淘汰计数。"""
    (cache_dir()).mkdir(parents=True, exist_ok=True)
    (cache_dir() / "notes.txt").write_text("人工文件", encoding="utf-8")
    save("stub", "one", _items(1))  # 不炸；txt 还在
    assert (cache_dir() / "notes.txt").exists()


def test_write_failure_is_silent(monkeypatch) -> None:
    """写坏（磁盘/权限）只警告不抛：缓存是派生数据，坏一次不值得打断刷新。"""
    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr("feedscope.core.feed_cache.Path.write_text", boom)
    save("stub", "one", _items(1))  # 不抛
    assert load("stub", "one") is None
