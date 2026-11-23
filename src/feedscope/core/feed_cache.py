"""列表缓存：每流一个 JSON 文件，重启先画缓存秒开、抓取失败时兜底不清屏。

- 目录：FEEDSCOPE_FEED_CACHE 环境变量（测试隔离），缺省用户缓存目录下 feeds/
  （platformdirs，与 chromium-profile / samples 同根）
- 每流一文件是硬约束不是风格：RSS 的 extra 带全文 content_html（单条 5-20KB），
  聚合到单文件 JSON 后每次刷新任何一个流都要原子重写全部缓存（十几 MB 写
  放大）；拆文件后每次只重写本流自己的文件（几十 KB～几 MB）
- 缓存是派生数据：删除/损坏的最坏代价 = 下次刷新重抓，所以 LRU 淘汰（按
  mtime 留最近 _MAX_FEEDS 个流）与坏文件容错都可以放心地激进
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote, unquote

from platformdirs import user_cache_path

from feedscope.core.models import FeedItem

logger = logging.getLogger(__name__)

_MAX_FEEDS = 60        # LRU：超过即删最旧（切流频繁的缓存自然留大，冷流自然让位）
_MAX_ITEMS = 200       # 单流上限：取列表头部（各源列表都是新→旧序）
_NAME_CAP = 120        # 文件名上限（quote 后）：防超长订阅 key 撞文件系统限制


def cache_dir() -> Path:
    if p := os.environ.get("FEEDSCOPE_FEED_CACHE"):
        return Path(p)
    return user_cache_path("feedscope") / "feeds"


def _file(source_id: str, feed: str) -> Path:
    """(source, feed) -> 缓存文件路径。

    quote 编码非 URL 安全字符（中文订阅 key、路径分隔符都变成 %XX）：
    唯一、路径安全；只做正向映射不回解，寻址永远走 (source, feed) 入参。
    超长名截断后补内容散列前缀，防不同长 key 截断后撞名。
    """
    name = quote(f"{source_id}__{feed}", safe="")
    if len(name) > _NAME_CAP:
        digest = hashlib.sha1(name.encode()).hexdigest()[:8]
        name = f"{name[:_NAME_CAP]}~{digest}"
    return cache_dir() / f"{name}.json"


def save(source_id: str, feed: str, items: list[FeedItem]) -> None:
    """流刷新成功后整流覆写（头部 _MAX_ITEMS 条）；空列表不落盘（无可兜底）。"""
    if not items:
        return
    path = _file(source_id, feed)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {"version": 1, "items": [it.to_dict() for it in items[:_MAX_ITEMS]]},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("列表缓存写入失败（%s/%s，忽略）：%s", source_id, feed, exc)
    _evict()


def load(source_id: str, feed: str) -> list[FeedItem] | None:
    """读缓存；不存在/坏/形状不符 = None（调用方当作「无缓存」走正常空态）。"""
    return _read(_file(source_id, feed))


def load_all() -> list[tuple[str, str, list[FeedItem]]]:
    """全部已缓存的流 -> [(source, feed, 条目)]：本地搜索的数据源。

    文件名是 quote(f"{source}__{feed}")，解码后按第一个 __ 切开即还原键
    （超长名截断加散列的那批还原不出原 feed 名——搜索不关心流名，条目照读）。
    单个文件坏了只跳过它（_read 已记警告）。"""
    try:
        files = sorted(cache_dir().glob("*.json"))
    except OSError:
        return []  # 目录不存在 / 读不了 = 没有可搜的缓存
    out: list[tuple[str, str, list[FeedItem]]] = []
    for path in files:
        items = _read(path)
        if items:
            source, _, feed = unquote(path.stem).partition("__")
            out.append((source, feed, items))
    return out


def _read(path: Path) -> list[FeedItem] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("列表缓存读取失败（%s，忽略）：%s", path.name, exc)
        return None
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return None
    items = [FeedItem.from_dict(r) for r in rows if isinstance(r, dict)]
    return items or None


def _mtime(p: Path) -> float:
    """文件的 mtime；stat 不动（iterdir 与 sort 之间被并发 `cache clear` 抢删）
    按最旧排——它本来就该进淘汰前缀，unlink 处自会再吞一次错。"""
    with contextlib.suppress(OSError):
        return p.stat().st_mtime
    return 0.0


def _evict() -> None:
    """目录内 .json 超过 _MAX_FEEDS 个时删最旧（mtime = 最近一次 save）。"""
    d = cache_dir()
    try:
        files = [p for p in d.iterdir() if p.suffix == ".json"]
    except OSError:
        return  # 目录不存在（还没写过任何缓存）
    if len(files) <= _MAX_FEEDS:
        return
    files.sort(key=_mtime)
    for p in files[: len(files) - _MAX_FEEDS]:
        # 并发/权限抖动：残留一个旧文件无害，下次淘汰再试
        with contextlib.suppress(OSError):
            p.unlink()


def cache_stats() -> tuple[int, int]:
    """(文件数, 总字节)——`feedscope cache` 的体检数。目录不存在 = (0, 0)。"""
    try:
        files = [p for p in cache_dir().iterdir() if p.suffix == ".json"]
    except OSError:
        return 0, 0
    size = 0
    for p in files:
        with contextlib.suppress(OSError):
            size += p.stat().st_size
    return len(files), size


def clear_cache() -> int:
    """删光缓存文件，返回删掉的数量（`feedscope cache clear`）。

    缓存是纯派生数据（下次刷新重建），删错无损；残留并发写的新文件无害。
    """
    try:
        files = [p for p in cache_dir().iterdir() if p.suffix == ".json"]
    except OSError:
        return 0
    removed = 0
    for p in files:
        with contextlib.suppress(OSError):
            p.unlink()
            removed += 1
    return removed
