"""已读/已见状态：state.json 记住条目见没见过、读没读过（「新」与「未读」的判据）。

- 路径：FEEDSCOPE_STATE 环境变量（测试隔离 / 多套状态切换），缺省用户状态
  目录下 state.json（platformdirs，Linux 为 ~/.local/state/feedscope/state.json）
- 选型 JSON 而非 SQLite：单进程单事件循环、纯按键查询、文件 500KB 量级且写入
  是用户节奏（打开一条 / 刷新一次）——索引与并发能力都用不上；纯文本可
  cat/grep，对齐 SECURITY 的「数据落点可检视」叙事
- 容错哲学同 config：读坏 = 警告 + 内存空态，绝不挡 UI；写坏 = 内存照改 +
  日志告警（丢一次落盘的代价远小于打断阅读）
- 键 = (source, id)（FeedItem 文档钦定的稳定键），嵌套 dict 存避免转义：
  {"version": 1, "items": {"zhihu": {"question/123": {"seen": ts, "read": ts}}}}
  （read 为 0 = 未读；seen 时间戳兼作淘汰序）
- 上限 _MAX_ITEMS 条按 seen FIFO 淘汰：条目本身会流走（热榜/时间线滚动），
  万级足够覆盖「上次会话见过吗」的判断窗口；将来收藏条目要豁免淘汰
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


def item_key(it: FeedItem) -> str:
    """(source, id) -> 单串键（如 "zhihu:question/123"）。

    note_fetched 用它识别本批首见者（返回集供调用方计数/调试），不回解；
    source id 是受控 ASCII 标识（hn/reddit/…），不含冒号，前缀不会歧义。
    """
    return f"{it.source}:{it.id}"


def _valid_entry(e: object) -> bool:
    return isinstance(e, dict) and isinstance(e.get("seen"), (int, float))


def _load() -> dict[str, dict[str, dict[str, float]]]:
    """读状态文件；不存在/坏 = {} + 警告，绝不抛（容错哲学见模块头）。"""
    path = state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("已读状态 %s 无法解析，已按空态处理：%s", path, exc)
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    clean: dict[str, dict[str, dict[str, float]]] = {}
    for src, ids in items.items():
        # 逐源逐条过滤形状：手编/旧版坏一条不波及整个状态文件
        if not isinstance(src, str) or not isinstance(ids, dict):
            continue
        ok = {i: e for i, e in ids.items() if isinstance(i, str) and _valid_entry(e)}
        if ok:
            clean[src] = ok
    return clean


class ReadState:
    """已读/已见状态（App 级共享件，挂载点见 ui/app.py；惰性加载首个查询触发）。"""

    def __init__(self) -> None:
        # None = 尚未从磁盘加载；空 dict = 已加载且无历史（写路径都先经 _data()）
        self._items: dict[str, dict[str, dict[str, float]]] | None = None

    def _data(self) -> dict[str, dict[str, dict[str, float]]]:
        if self._items is None:
            self._items = _load()
        return self._items

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
                    {"version": 1, "items": items}, ensure_ascii=False, separators=(",", ":")
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

    # ---- 变更（每次变更即落盘：崩溃最丢一条标记，不值得为它上防抖）----

    def note_fetched(self, items: list[FeedItem]) -> set[str]:
        """登记一批抓到的条目（未见过的记 seen=now），返回其中首见者的 key 集。

        返回值即「新」快照：必须登记前先算，登记后 is_seen 已翻真；调用方
        （MainScreen._refresh）拿它当本批渲染 ● 的判据。seen 已存在的条目
        原条目不动（保留原 read 态与淘汰序）。
        """
        data = self._data()
        now = time.time()
        fresh: set[str] = set()
        for it in items:
            ids = data.setdefault(it.source, {})
            if it.id not in ids:
                fresh.add(item_key(it))
                ids[it.id] = {"seen": now, "read": 0.0}
        if fresh:
            self._flush()  # 无新条目时内存与磁盘本就一致：跳过全量重写
        return fresh

    def mark_read(self, source: str, id: str) -> None:
        """打开条目（进详情 / 浏览器兜底）即已读；未见过的条目也能标（直接开链接）。"""
        ids = self._data().setdefault(source, {})
        entry = ids.setdefault(id, {"seen": time.time(), "read": 0.0})
        if entry.get("read"):
            return  # 已读再打开（重读）：状态不变，不付全量重写；read 只做真值用
        entry["read"] = time.time()
        self._flush()

    def toggle_read(self, source: str, id: str) -> bool:
        """Ctrl+R 手动切换，返回切换后的已读态。"""
        if self.is_read(source, id):
            self._data()[source][id]["read"] = 0.0
            self._flush()
            return False
        self.mark_read(source, id)
        return True


def _prune(items: dict[str, dict[str, dict[str, float]]]) -> None:
    """超过 _MAX_ITEMS 条按 seen 最旧淘汰（清空的源随最旧条目一起消失）。"""
    total = sum(len(ids) for ids in items.values())
    if total <= _MAX_ITEMS:
        return
    flat = sorted(
        (e["seen"], src, i) for src, ids in items.items() for i, e in ids.items()
    )
    for _, src, i in flat[: total - _MAX_ITEMS]:
        del items[src][i]
        if not items[src]:
            del items[src]
