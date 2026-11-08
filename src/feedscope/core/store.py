"""已读/已见/收藏状态：state.json 记住条目见没见过、读没读过、收没收藏。

- 路径：FEEDSCOPE_STATE / FEEDSCOPE_FAVORITES 环境变量（测试隔离 / 多套状态
  切换），缺省用户状态目录（platformdirs，Linux 为 ~/.local/state/feedscope/）
  下的 state.json / favorites.json
- 选型 JSON 而非 SQLite：单进程单事件循环、纯按键查询、文件 500KB 量级且写入
  是用户节奏（打开一条 / 刷新一次）——索引与并发能力都用不上；纯文本可
  cat/grep，对齐 SECURITY 的「数据落点可检视」叙事
- 容错哲学同 config：读坏 = 警告 + 内存空态，绝不挡 UI；写坏 = 内存照改 +
  日志告警（丢一次落盘的代价远小于打断阅读）
- 键 = (source, id)（FeedItem 文档钦定的稳定键），嵌套 dict 存避免转义：
  state.json：{"version": 1, "items": {<source>: {<id>: {"seen", "read", "fav"?}}}}
  （read 为 0 = 未读；seen 兼作淘汰序；fav 见下）
- 每流未读计数：顶层 "feeds": {"<source>/<feed>": n}。**以刷新为准**——每次
  note_fetched 整流重数（本批未读条数），mark_read/toggle 按条目首见时记的
  feed 名增减（从收藏屏读也减对家）。已知近似：同一 RSS 条目同时在「全部」
  与单订阅流时，另一流的计数到它下次刷新才纠偏——误差有界自愈
- 收藏存**全量快照**（收藏那一刻的完整 FeedItem，含 extra 正文）于
  favorites.json：{"version": 1, "items": {<source>: {<id>: {"fav": ts,
  "item": {...}}}}}——条目会从列表流走、回答/推文会被删，按 id 回源重取
  一周后点开一半是死的；快照永远可读，RSS/Telegram 正文离线即在。两个文件
  分开是因为写频不同：state.json 每次标已读都全量重写，favorites.json 只在
  收藏/取消时写，互不放大
- 淘汰：_MAX_ITEMS 上限只作用于非收藏条目（按 seen FIFO）；收藏是用户自选
  数据，永不淘汰、无条数上限
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from platformdirs import user_state_path

from feedscope.core.models import FeedItem

logger = logging.getLogger(__name__)

_MAX_ITEMS = 10_000


def state_path() -> Path:
    if p := os.environ.get("FEEDSCOPE_STATE"):
        return Path(p)
    return user_state_path("feedscope") / "state.json"


def favorites_path() -> Path:
    if p := os.environ.get("FEEDSCOPE_FAVORITES"):
        return Path(p)
    return user_state_path("feedscope") / "favorites.json"


def item_key(it: FeedItem) -> str:
    """(source, id) -> 单串键（如 "zhihu:question/123"）。

    note_fetched 用它识别本批首见者（返回集供调用方计数/调试），不回解；
    source id 是受控 ASCII 标识（hn/reddit/…），不含冒号，前缀不会歧义。
    """
    return f"{it.source}:{it.id}"


def _valid_entry(e: object) -> bool:
    return isinstance(e, dict) and isinstance(e.get("seen"), (int, float))


# 条目值：时间戳们（float）+ 可选首见流名（str，计数递减的路由键）
Entry = dict[str, float | str]
SourceEntries = dict[str, Entry]


def _load() -> tuple[dict[str, SourceEntries], dict[str, int]]:
    """读状态文件；不存在/坏 = ({}, {}) + 警告，绝不抛（容错哲学见模块头）。"""
    path = state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("已读状态 %s 无法解析，已按空态处理：%s", path, exc)
        return {}, {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}, {}
    clean: dict[str, SourceEntries] = {}
    for src, ids in items.items():
        # 逐源逐条过滤形状：手编/旧版坏一条不波及整个状态文件
        if not isinstance(src, str) or not isinstance(ids, dict):
            continue
        ok = {i: e for i, e in ids.items() if isinstance(i, str) and _valid_entry(e)}
        if ok:
            clean[src] = ok
    raw_feeds = raw.get("feeds") if isinstance(raw, dict) else None
    feeds = (
        {k: v for k, v in raw_feeds.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(raw_feeds, dict)
        else {}
    )
    return clean, feeds


class ReadState:
    """已读/已见状态（App 级共享件，挂载点见 ui/app.py；惰性加载首个查询触发）。"""

    def __init__(self) -> None:
        # None = 尚未从磁盘加载；空 dict = 已加载且无历史（写路径都先经 _data()）
        self._items: dict[str, SourceEntries] | None = None
        self._feeds: dict[str, int] = {}  # "<source>/<feed>" -> 未读数（边栏角标）

    def _data(self) -> dict[str, SourceEntries]:
        if self._items is None:
            self._items, self._feeds = _load()
        return self._items

    def _feeds_data(self) -> dict[str, int]:
        self._data()  # 确保已加载
        return self._feeds

    def _flush(self) -> None:
        """全量落盘（临时文件 + os.replace 原子写，模式同 config；先淘汰再写）。"""
        items = self._data()
        _prune(items)
        path = state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {"version": 1, "items": items, "feeds": self._feeds},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError as exc:
            # 磁盘满/权限坏：内存态继续可用，丢一次落盘可接受（下次变更再试）
            logger.warning("已读状态落盘失败（内存态保留）：%s", exc)

    # ---- 查询 ----

    def is_read(self, source: str, id: str) -> bool:
        entry = self._data().get(source, {}).get(id)
        return bool(entry and entry.get("read"))

    def unread_count(self, source: str, feed: str) -> int:
        """某流的未读数（边栏角标；以最近一次刷新的重数为准，读一条减一条）。"""
        return self._feeds_data().get(f"{source}/{feed}", 0)

    # ---- 变更（每次变更即落盘：崩溃最丢一条标记，不值得为它上防抖）----

    def note_fetched(
        self, items: list[FeedItem], feed: str = "", *, recount: bool = False
    ) -> set[str]:
        """登记一批抓到的条目（未见过的记 seen=now + 首见流名），返回首见者的 key 集。

        recount=True（流的首批/刷新）整流重数未读角标 = 本批 read=0 条数——
        计数以刷新为准，这是纠偏点；False（Ctrl+N 增量批）只把首见数加上去
        （首见者必未读，局部批重数会把整流计数冲成批内值）。返回值即「新」
        快照：必须登记前先算；已见条目原记录不动。
        """
        data = self._data()
        now = time.time()
        fresh: set[str] = set()
        for it in items:
            ids = data.setdefault(it.source, {})
            if it.id not in ids:
                fresh.add(item_key(it))
                entry: Entry = {"seen": now, "read": 0.0}
                if feed:
                    entry["feed"] = feed  # 首见流：mark_read 按它路由计数递减
                ids[it.id] = entry
        if items and feed:
            key = f"{items[0].source}/{feed}"
            feeds = self._feeds_data()
            feeds[key] = (
                sum(1 for it in items if not self.is_read(it.source, it.id))
                if recount
                else feeds.get(key, 0) + len(fresh)
            )
        if fresh or feed:
            self._flush()  # 都没变时内存与磁盘本就一致：跳过全量重写
        return fresh

    def _bump(self, source: str, entry: Entry, delta: int) -> None:
        """按条目首见流名增减角标计数（下限 0；没有流名的条目无处可减）。"""
        feed = entry.get("feed")
        if not isinstance(feed, str) or not feed:
            return
        key = f"{source}/{feed}"
        if key in self._feeds_data():
            self._feeds[key] = max(0, self._feeds[key] + delta)

    def mark_read(self, source: str, id: str) -> None:
        """打开条目（进详情 / 浏览器兜底）即已读；未见过的条目也能标（直接开链接）。"""
        ids = self._data().setdefault(source, {})
        entry = ids.setdefault(id, {"seen": time.time(), "read": 0.0})
        if entry.get("read"):
            return  # 已读再打开（重读）：状态不变，不付全量重写；read 只做真值用
        entry["read"] = time.time()
        self._bump(source, entry, -1)
        self._flush()

    def toggle_read(self, source: str, id: str) -> bool:
        """Ctrl+R 手动切换，返回切换后的已读态。"""
        if self.is_read(source, id):
            entry = self._data()[source][id]
            entry["read"] = 0.0
            self._bump(source, entry, +1)
            self._flush()
            return False
        self.mark_read(source, id)
        return True

    def mark_all_read(self, items: list[FeedItem], feed: str) -> None:
        """一键清仓（Ctrl+Shift+R）：整批置已读 + 该流角标清零，单次落盘。"""
        data = self._data()
        now = time.time()
        for it in items:
            entry = data.setdefault(it.source, {}).setdefault(it.id, {"seen": now, "read": 0.0})
            entry["read"] = now
        if items and feed:
            self._feeds_data()[f"{items[0].source}/{feed}"] = 0
        self._flush()

    # ---- 收藏（快照在 favorites.json，判据在 state.json 的 fav 字段）----

    def is_fav(self, source: str, id: str) -> bool:
        entry = self._data().get(source, {}).get(id)
        return bool(entry and entry.get("fav"))

    def toggle_fav(self, item: FeedItem) -> bool:
        """Ctrl+F 收藏/取消，返回切换后的收藏态。

        收藏 = state.json 记 fav 时间戳（豁免淘汰）+ favorites.json 写全量
        快照（列表展示与离线正文都靠它）；取消 = 两处同删。未见过的条目
        也能收藏（直接在详情屏按 Ctrl+F 的路径），seen 顺手补上。
        """
        data = self._data()
        entry = data.setdefault(item.source, {}).setdefault(
            item.id, {"seen": time.time(), "read": 0.0}
        )
        favs = _load_favs()
        if entry.get("fav"):
            entry["fav"] = 0.0
            favs.get(item.source, {}).pop(item.id, None)
            now_fav = False
        else:
            now = time.time()
            entry["fav"] = now
            favs.setdefault(item.source, {})[item.id] = {"fav": now, "item": item.to_dict()}
            now_fav = True
        self._flush()
        _save_favs(favs)
        return now_fav

    def favorites(self) -> list[tuple[float, FeedItem]]:
        """全部收藏，按收藏时间新→旧；读的是 favorites.json 快照（零请求）。"""
        flat: list[tuple[float, FeedItem]] = []
        for ids in _load_favs().values():
            for e in ids.values():
                ts, item = e.get("fav"), e.get("item")
                if isinstance(ts, (int, float)) and isinstance(item, dict):
                    flat.append((float(ts), FeedItem.from_dict(item)))
        flat.sort(key=lambda p: p[0], reverse=True)
        return flat


def _prune(items: dict[str, SourceEntries]) -> None:
    """超过 _MAX_ITEMS 条按 seen 最旧淘汰（清空的源一起消失）；收藏条目豁免。"""
    evictable = [
        (float(e["seen"]), src, i)
        for src, ids in items.items()
        for i, e in ids.items()
        if not e.get("fav")
    ]
    excess = len(evictable) - _MAX_ITEMS
    if excess <= 0:
        return
    for _, src, i in sorted(evictable)[:excess]:
        del items[src][i]
        if not items[src]:
            del items[src]


# ---- favorites.json：收藏快照的落盘/读回（形状与容错见模块头）----


def _load_favs() -> dict[str, dict[str, dict[str, object]]]:
    """读收藏快照；不存在/坏 = {} + 警告（收藏丢了显示为空，可重新收）。"""
    try:
        raw = json.loads(favorites_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("收藏快照 %s 无法解析，已按空态处理：%s", favorites_path(), exc)
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    clean: dict[str, dict[str, dict[str, object]]] = {}
    for src, ids in items.items():
        if not isinstance(src, str) or not isinstance(ids, dict):
            continue
        ok = {
            i: e
            for i, e in ids.items()
            if isinstance(i, str)
            and isinstance(e, dict)
            and isinstance(e.get("fav"), (int, float))
            and isinstance(e.get("item"), dict)
        }
        if ok:
            clean[src] = ok
    return clean


def _save_favs(favs: dict[str, dict[str, dict[str, object]]]) -> None:
    """原子写收藏快照（只在收藏/取消时调用，频次低；tmp + os.replace）。"""
    path = favorites_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "items": favs}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("收藏快照落盘失败（内存态保留）：%s", exc)
