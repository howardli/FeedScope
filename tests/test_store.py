"""core/store 已读/已见状态单元测试：登记语义 / 查询 / 跨实例持久 / 容错 / 淘汰。

env 隔离由 conftest._isolated_user_env 的 FEEDSCOPE_STATE 负责（同一测试内
多个 ReadState 实例共享同一个文件——正是「跨实例持久」要断言的形态）。
"""

from __future__ import annotations

import json

import pytest

from feedscope.core.models import FeedItem
from feedscope.core.store import ReadState, item_key, state_path


def _item(source: str, id: str, title: str = "标题") -> FeedItem:
    return FeedItem(source=source, id=id, title=title, url=f"https://example.com/{id}")


def test_note_fetched_returns_first_seen_only() -> None:
    """首见者进「新」快照；同一批再来一次全部是旧面孔。"""
    st = ReadState()
    items = [_item("zhihu", "question/1"), _item("zhihu", "question/2")]
    fresh = st.note_fetched(items)
    assert fresh == {item_key(items[0]), item_key(items[1])}
    assert st.note_fetched(items) == set()


def test_item_key_distinguishes_sources() -> None:
    """同 id 不同源是两条状态；键不回解、只做成员比对。"""
    assert item_key(_item("x", "1")) != item_key(_item("zhihu", "1"))
    assert item_key(_item("x", "1")) == "x:1"


def test_mark_read_and_toggle() -> None:
    st = ReadState()
    st.mark_read("hn", "5")
    assert st.is_read("hn", "5")
    assert not st.is_read("hn", "6")  # 不同条目不受牵连
    assert st.toggle_read("hn", "5") is False  # 已读 -> 未读
    assert not st.is_read("hn", "5")
    assert st.toggle_read("hn", "5") is True  # 未读 -> 已读
    assert st.is_read("hn", "5")


def test_toggle_unread_item_never_fetched() -> None:
    """未登记过的条目直接 toggle：不炸、按未读起步翻成已读。"""
    st = ReadState()
    assert st.toggle_read("v2ex", "999") is True
    assert st.is_read("v2ex", "999")


def test_persistence_across_instances() -> None:
    """每次变更即落盘：新实例（模拟重启）读回已读/已见。"""
    ReadState().mark_read("reddit", "abc")
    items = [_item("reddit", "abc"), _item("reddit", "def")]
    st2 = ReadState()
    assert st2.is_read("reddit", "abc")  # 跨实例已读仍在
    assert st2.note_fetched(items) == {item_key(items[1])}  # 只 def 是首见


def test_note_fetched_preserves_read_state() -> None:
    """已读条目再次出现在列表里（F5 / 重启）不被覆写成未读。"""
    st = ReadState()
    items = [_item("rss", "k/x")]
    st.note_fetched(items)
    st.mark_read("rss", "k/x")
    assert st.note_fetched(items) == set()  # 已见过：不进新快照
    assert st.is_read("rss", "k/x")  # 已读保留


def test_bad_file_tolerated_as_empty() -> None:
    """坏 JSON = 警告 + 空态，绝不抛（不挡 UI 启动）。"""
    state_path().write_text("{ 不是 JSON", encoding="utf-8")
    st = ReadState()
    items = [_item("hn", "1")]
    assert st.note_fetched(items) == {item_key(items[0])}
    assert state_path().exists()  # 落盘修好了文件


def test_load_filters_invalid_entries() -> None:
    """条目级坏数据只丢自己：缺 seen 的、非 dict 的清掉，其余保留。"""
    state_path().write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "hn": {
                        "1": {"seen": 100, "read": 200},
                        "bad": {"read": 1},  # 缺 seen
                        "worse": "not-a-dict",
                    },
                    "not-a-source": 42,
                }
            }
        ),
        encoding="utf-8",
    )
    st = ReadState()
    assert st.is_read("hn", "1")
    assert not st.is_read("hn", "bad")


def test_prune_evicts_oldest_seen() -> None:
    """超上限按 seen 最旧淘汰；淘汰到空的源一起消失。"""
    items = {"zhihu": {f"q{i}": {"seen": float(i), "read": 0.0} for i in range(1, 7)}}
    _prune_with_cap(items, cap=4)
    assert sum(len(ids) for ids in items.values()) == 4
    assert "q1" not in items["zhihu"] and "q2" not in items["zhihu"]  # 最旧的走了
    assert "q6" in items["zhihu"]  # 最新的留下


def test_prune_under_cap_is_noop() -> None:
    items = {"zhihu": {"q1": {"seen": 1.0, "read": 0.0}}}
    _prune_with_cap(items, cap=10)
    assert items == {"zhihu": {"q1": {"seen": 1.0, "read": 0.0}}}


def _prune_with_cap(items: dict, cap: int) -> None:
    """_prune 直接吃模块常量上限，测试里换成小上限重放同一逻辑。"""
    import feedscope.core.store as store

    saved = store._MAX_ITEMS
    store._MAX_ITEMS = cap
    try:
        store._prune(items)
    finally:
        store._MAX_ITEMS = saved


def test_no_tmp_file_left_behind() -> None:
    """原子写不留 .tmp 残骸（os.replace 成功后 tmp 即消失）。"""
    st = ReadState()
    st.mark_read("hn", "1")
    assert not (state_path().parent / (state_path().name + ".tmp")).exists()
    assert json.loads(state_path().read_text(encoding="utf-8"))["version"] == 1


def test_note_fetched_without_new_items_skips_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """重拉同一个流（全旧面孔）不触发全量重写——膨胀顾虑的主要来源。"""
    st = ReadState()
    items = [_item("zhihu", "question/1")]
    st.note_fetched(items)  # 首见：正常落盘

    def boom(self) -> None:
        raise AssertionError("无新条目不应落盘")

    monkeypatch.setattr(ReadState, "_flush", boom)
    assert st.note_fetched(items) == set()  # 不炸 = 没写


def test_mark_read_idempotent_skips_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """重读已读条目不触发全量重写（read 只做真值用，时间戳没有再写的价值）。"""
    st = ReadState()
    st.mark_read("hn", "1")

    def boom(self) -> None:
        raise AssertionError("已读条目再标已读不应落盘")

    monkeypatch.setattr(ReadState, "_flush", boom)
    st.mark_read("hn", "1")  # 不炸 = 没写
    assert st.is_read("hn", "1")


def test_write_failure_keeps_memory_state(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """落盘失败（磁盘/权限）不炸不丢内存态：警告 + 照常服务。"""
    st = ReadState()
    st.mark_read("hn", "1")  # 先成功一次，保证目录与文件就位

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("feedscope.core.store.os.replace", boom)
    with caplog.at_level("WARNING"):
        st.mark_read("hn", "2")
    assert "落盘失败" in caplog.text
    assert st.is_read("hn", "2")  # 内存态还在
