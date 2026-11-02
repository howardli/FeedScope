"""`feedscope rss` 子命令的实现：订阅增删查 + OPML 导入导出。

只做管理编排：读改写 sources.toml（写路径统一走 config.update_rss_feeds，
enabled/hide 联动在那里）、add 时拉一次 feed 取标题当默认显示名。TUI 不
加载本模块；OPML 用 stdlib ElementTree 解析（嵌套目录拍平），导出手写
模板——结构固定，不值得引库。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from feedscope.core import config
from feedscope.core.config import RssFeed
from feedscope.sources.rss.parse import feed_title


class SubscriptionError(Exception):
    """订阅操作的业务失败（重复 / key 冲突 / 文件读不了）：消息面向用户可直接打印。"""


def derive_key(url: str, existing: Iterable[str]) -> str:
    """URL -> 订阅 key：域名清洗成 ASCII 段（www 剥掉、非法字符折叠成 -），
    与已有 key 撞名则加 -2、-3…；域名清洗后为空（如纯 IP 端口）回退 feed。"""
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    host = re.sub(r"^www\.", "", host)
    base = re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "feed"
    keys = set(existing)
    if base not in keys:
        return base
    n = 2
    while f"{base}-{n}" in keys:
        n += 1
    return f"{base}-{n}"


async def _fetch_feed_title(url: str) -> str:
    """拉一次 feed 取自述标题；失败回空串——取名是尽力而为，不挡加订阅。"""
    from feedscope.core import http

    try:
        r = await http.get(url, source="RSS")
    except Exception:
        return ""
    return feed_title(r.content)


async def add_feed(url: str, *, key: str | None = None, label: str | None = None) -> RssFeed:
    """加一条订阅并落盘；显示名缺省拉一次 feed 标题，再缺省回 key。

    重复地址 / 显式 key 撞已有或保留字 all → SubscriptionError（不动配置）。
    """
    feeds = config.rss_feeds()
    if any(f.url == url for f in feeds):
        dup = next(f.key for f in feeds if f.url == url)
        raise SubscriptionError(f"这个地址已经订阅过了（key：{dup}）")
    if key is None:
        key = derive_key(url, (f.key for f in feeds))
    elif key == config.RSS_ALL_FEED:
        raise SubscriptionError("all 是聚合流保留字，不能用作订阅 key")
    elif key in {f.key for f in feeds}:
        raise SubscriptionError(f"key {key!r} 已被占用（feedscope rss list 查看）")
    if not label:
        label = (await _fetch_feed_title(url)) or key
    feed = RssFeed(key=key, label=label, url=url)
    config.update_rss_feeds([*feeds, feed])
    return feed


def remove_feed(key: str) -> RssFeed | None:
    """删一条订阅，返回被删的订阅；key 不存在返回 None（不动配置）。"""
    feeds = config.rss_feeds()
    kept = [f for f in feeds if f.key != key]
    if len(kept) == len(feeds):
        return None
    config.update_rss_feeds(kept)
    return next(f for f in feeds if f.key == key)


# ---- OPML 进出 ----


def parse_opml(text: str) -> list[tuple[str, str]]:
    """OPML 文本 -> [(label, xmlUrl)]；嵌套目录拍平，无 xmlUrl 的节点只当容器。"""
    root = ET.fromstring(text)
    out: list[tuple[str, str]] = []
    for outline in root.iter("outline"):
        url = (outline.get("xmlUrl") or "").strip()
        if not url:
            continue
        label = (outline.get("text") or outline.get("title") or "").strip()
        out.append((label, url))
    return out


def import_opml(text: str) -> tuple[list[RssFeed], list[str]]:
    """OPML 批量导入（幂等合并）：返回 (新增订阅, 跳过原因列表)。

    已订阅的地址（含 OPML 内部重复）跳过；label 用 outline 的 text/title，
    缺了回 key；一次落盘（中途失败不写半截配置）。
    """
    feeds = config.rss_feeds()
    known_urls = {f.url for f in feeds}
    known_keys = {f.key for f in feeds}
    added: list[RssFeed] = []
    skipped: list[str] = []
    for label, url in parse_opml(text):
        if url in known_urls:
            skipped.append(f"{label or url}：地址已订阅")
            continue
        key = derive_key(url, known_keys)
        known_urls.add(url)
        known_keys.add(key)
        added.append(RssFeed(key=key, label=label or key, url=url))
    if added:
        config.update_rss_feeds([*feeds, *added])
    return added, skipped


_XML_QUOTE = '"'


def _attr(value: str) -> str:
    """字符串 -> XML 属性值：双引号包裹，& < > 与引号本体转义。"""
    escaped = escape(value, {_XML_QUOTE: "&quot;"})
    return f"{_XML_QUOTE}{escaped}{_XML_QUOTE}"


def export_opml(feeds: list[RssFeed]) -> str:
    """订阅表 -> OPML 2.0 文本（标准阅读器都认，可再迁去别家）。"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<opml version=\"2.0\">",
        "  <head><title>FeedScope 订阅导出</title></head>",
        "  <body>",
    ]
    lines += [
        f"    <outline type=\"rss\" text={_attr(f.label)} xmlUrl={_attr(f.url)} />"
        for f in feeds
    ]
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)
