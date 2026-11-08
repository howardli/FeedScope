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

import hashlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

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
    try:
        raw = json.loads(_file(source_id, feed).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("列表缓存读取失败（%s/%s，忽略）：%s", source_id, feed, exc)
        return None
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return None
    items = [FeedItem.from_dict(r) for r in rows if isinstance(r, dict)]
    return items or None


def _evict() -> None:
    """目录内 .json 超过 _MAX_FEEDS 个时删最旧（mtime = 最近一次 save）。"""
    d = cache_dir()
    try:
        files = [p for p in d.iterdir() if p.suffix == ".json"]
    except OSError:
        return  # 目录不存在（还没写过任何缓存）
    if len(files) <= _MAX_FEEDS:
        return
    files.sort(key=lambda p: p.stat().st_mtime)  # 调用点已兜 OSError，这里不再拦
    for p in files[: len(files) - _MAX_FEEDS]:
        try:
            p.unlink()
        except OSError:
            pass  # 并发/权限抖动：残留一个旧文件无害，下次淘汰再试
