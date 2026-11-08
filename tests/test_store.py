"""core/store 已读/已见状态单元测试：登记语义 / 查询 / 跨实例持久 / 容错 / 淘汰。

env 隔离由 conftest._isolated_user_env 的 FEEDSCOPE_STATE 负责（同一测试内
多个 ReadState 实例共享同一个文件——正是「跨实例持久」要断言的形态）。
"""

from __future__ import annotations

import json

import pytest

from feedscope.core.models import FeedItem
from feedscope.core.store import ReadState, favorites_path, item_key, state_path


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


# ---- 收藏（fav 字段 + favorites.json 快照）----


def test_toggle_fav_and_favorites_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """收藏往返与排序（新→旧）：快照完整可回，取消时两处同删。"""
    import itertools

    # time.time 平局会让排序不稳定（同秒收藏的顺序漂移），钉成单调递增
    counter = itertools.count(1000)
    monkeypatch.setattr("feedscope.core.store.time.time", lambda: next(counter))

    st = ReadState()
    a = _item("zhihu", "question/1", "甲")
    b = _item("hn", "5", "乙")
    assert st.toggle_fav(a) is True
    assert st.toggle_fav(b) is True
    assert st.is_fav("zhihu", "question/1") and st.is_fav("hn", "5")

    favs = st.favorites()
    assert [it.title for _, it in favs] == ["乙", "甲"]  # 后收藏的在前
    assert favs[1][1] == a  # 快照完整可回（含 url/meta）

    # 跨实例（重启）仍在
    assert [it.title for _, it in ReadState().favorites()] == ["乙", "甲"]

    assert st.toggle_fav(a) is False  # 取消：state.json + favorites.json 同删
    assert not st.is_fav("zhihu", "question/1")
    assert [it.title for _, it in ReadState().favorites()] == ["乙"]


def test_prune_never_evicts_favorites() -> None:
    """淘汰只在非收藏条目里进行：收藏条目无论多旧都豁免。"""
    st = ReadState()
    items = [_item("hn", str(i)) for i in range(5)]
    st.note_fetched(items)
    st.toggle_fav(_item("hn", "2", "收藏的"))
    _prune_with_cap(st._data(), cap=4)
    assert st.is_fav("hn", "2"), "收藏条目不被淘汰"
    assert sum(len(ids) for ids in st._data().values()) == 5  # 4 普通 + 1 收藏


def test_favorites_bad_file_tolerated() -> None:
    """favorites.json 坏 = 警告 + 空态，下一次收藏写回即修好。"""
    favorites_path().write_text("{ 坏 JSON", encoding="utf-8")
    st = ReadState()
    assert st.favorites() == []
    st.toggle_fav(_item("x", "1"))
    assert len(ReadState().favorites()) == 1


# ---- 每流未读计数（边栏角标）----


def test_feed_counts_recount_and_decrement() -> None:
    """整流重数以刷新为准；mark_read 按首见流路由递减；刷新纠偏。"""
    st = ReadState()
    items = [_item("zhihu", f"q{i}") for i in range(3)]
    st.note_fetched(items, "hot", recount=True)
    assert st.unread_count("zhihu", "hot") == 3
    st.mark_read("zhihu", "q0")
    assert st.unread_count("zhihu", "hot") == 2
    st.note_fetched(items, "hot", recount=True)  # 刷新重数与递减一致（无漂移）
    assert st.unread_count("zhihu", "hot") == 2
    st.mark_read("zhihu", "q1")
    st.note_fetched(items, "hot", recount=True)
    assert st.unread_count("zhihu", "hot") == 1
    assert st.unread_count("zhihu", "never") == 0  # 没抓过的流零


def test_feed_count_routes_by_entry_feed_not_name() -> None:
    """递减按条目首见流路由：流名跨源撞名不误伤（rss/telegram 都有 all），
    从收藏屏读也减主场的数（跨屏路由正确性）。"""
    st = ReadState()
    st.note_fetched([_item("rss", "a/1")], "all", recount=True)
    st.note_fetched([_item("telegram", "x/1")], "all", recount=True)
    st.mark_read("rss", "a/1")
    assert st.unread_count("rss", "all") == 0
    assert st.unread_count("telegram", "all") == 1, "同名流不误伤"


def test_load_more_partial_batch_adds_not_recounts() -> None:
    """Ctrl+N 增量批：首见数累加，局部批不得把整流计数冲成批内值。"""
    st = ReadState()
    st.note_fetched([_item("hn", str(i)) for i in range(3)], "front", recount=True)
    st.note_fetched([_item("hn", str(i)) for i in range(10, 12)], "front")
    assert st.unread_count("hn", "front") == 5


def test_toggle_adjusts_count_both_ways() -> None:
    st = ReadState()
    items = [_item("v2ex", f"t{i}") for i in range(3)]
    st.note_fetched(items, "hot", recount=True)
    st.toggle_read("v2ex", "t0")  # → 已读：3→2
    assert st.unread_count("v2ex", "hot") == 2
    st.toggle_read("v2ex", "t0")  # → 未读：2→3
    assert st.unread_count("v2ex", "hot") == 3


def test_overlap_across_sibling_feeds() -> None:
    """兄弟流重叠（知乎热榜↔推荐 / HN front↔new / X 推荐↔关注：同一 id 同时
    出现在两个流）与 RSS 聚合同类：成员模型统一覆盖，两边角标都减。"""
    st = ReadState()
    q = _item("zhihu", "question/9")
    st.note_fetched([q], "hot", recount=True)
    st.note_fetched([q], "recommend", recount=True)
    assert st.unread_count("zhihu", "hot") == 1
    assert st.unread_count("zhihu", "recommend") == 1, "兄弟流各自计它"
    st.mark_read("zhihu", "question/9")
    assert st.unread_count("zhihu", "hot") == 0
    assert st.unread_count("zhihu", "recommend") == 0, "读它两边同步减"


def test_membership_decrements_both_feeds() -> None:
    """RSS 条目同时活在「全部」与单订阅流：两边角标都计它，读它两边同步减
    （曾只记首见流——在单订阅流里读它、聚合流角标不动，反之亦然）。"""
    st = ReadState()
    item = _item("rss", "k/1")
    st.note_fetched([item], "all", recount=True)  # 启动默认停聚合流：首见在 all
    st.note_fetched([item], "k", recount=True)  # 再进单订阅流：成员并入
    assert st.unread_count("rss", "all") == 1
    assert st.unread_count("rss", "k") == 1, "两边角标都计它"
    st.mark_read("rss", "k/1")
    assert st.unread_count("rss", "k") == 0, "在读的这个流减了"
    assert st.unread_count("rss", "all") == 0, "另一个成员流同步减"

    # Ctrl+A 清仓同理：单订阅流清零，「全部」的角标一起塌
    st2 = ReadState()
    a, b = _item("rss", "k/1"), _item("rss", "k/2")
    st2.note_fetched([a, b], "all", recount=True)
    st2.note_fetched([a, b], "k", recount=True)
    st2.mark_all_read([a, b], "k")
    assert st2.unread_count("rss", "k") == 0
    assert st2.unread_count("rss", "all") == 0, "成员流随清仓同步塌"


def test_mark_all_read_zeroes_feed_count() -> None:
    st = ReadState()
    items = [_item("hn", str(i)) for i in range(4)]
    st.note_fetched(items, "front", recount=True)
    st.mark_all_read(items, "front")
    assert st.unread_count("hn", "front") == 0
    assert all(st.is_read("hn", str(i)) for i in range(4))
    assert ReadState().unread_count("hn", "front") == 0  # 跨实例（重启）持久


def test_old_entries_without_feed_get_backfilled() -> None:
    """角标功能之前建的条目没有 feed 字段：刷新时回填，读它才能递减。

    真实场景即「知乎热榜读了一条角标不减」——存量条目递减无路由。
    """
    # 造旧形状数据：条目存在但无 feed（模拟 0.2.x 时代的 state.json）
    state_path().write_text(
        json.dumps(
            {
                "version": 1,
                "items": {"zhihu": {"question/1": {"seen": 1.0, "read": 0.0}}},
                "feeds": {"zhihu/hot": 1},
            }
        ),
        encoding="utf-8",
    )
    st = ReadState()
    st.mark_read("zhihu", "question/1")
    assert st.unread_count("zhihu", "hot") == 1, "旧病：无路由，读了不减"

    # 重造干净的旧形状（上面的 mark_read 已把 q1 落盘成已读）
    state_path().write_text(
        json.dumps(
            {
                "version": 1,
                "items": {"zhihu": {"question/1": {"seen": 1.0, "read": 0.0}}},
                "feeds": {"zhihu/hot": 1},
            }
        ),
        encoding="utf-8",
    )
    st2 = ReadState()
    items = [_item("zhihu", "question/1"), _item("zhihu", "question/2")]
    st2.note_fetched(items, "hot", recount=True)  # 刷新：q1 是无 feed 的旧条目 → 回填
    assert st2.unread_count("zhihu", "hot") == 2
    st2.mark_read("zhihu", "question/1")
    assert st2.unread_count("zhihu", "hot") == 1, "回填后递减有路由"


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
