"""`feedscope rss` 子命令的实现：订阅增删查 + OPML 导入导出。

只做管理编排：读改写 sources.toml（写路径统一走 config.update_rss_feeds，
enabled/hide 联动在那里）、add 时拉一次 feed 取标题当默认显示名。TUI 不
加载本模块；OPML 用 stdlib ElementTree 解析（嵌套目录拍平），导出手写
模板——结构固定，不值得引库。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from feedscope.core import config
from feedscope.core.config import RssFeed
from feedscope.sources.rss.parse import discover_feed_link, probe


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


async def _fetch_body_or_none(url: str) -> bytes | None:
    """拉一次地址；失败回 None——取名/自动发现是尽力而为，不挡加订阅。"""
    from feedscope.core import http

    try:
        return (await http.get(url, source="RSS")).content
    except Exception:
        return None


async def add_feed(url: str, *, key: str | None = None, label: str | None = None) -> RssFeed:
    """加一条订阅并落盘；显示名缺省拉一次 feed 标题，再缺省回 key。

    地址不是 feed 而是站点首页时自动发现 feed 声明（<link rel="alternate">）
    并改订发现的地址（key 沿用）；重复地址 / 显式 key 撞已有或保留字 all →
    SubscriptionError（不动配置）。
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
        body = await _fetch_body_or_none(url)
        if body is not None:
            title, entries = probe(body)
            if not title and not entries:
                # 拉回来不像 feed：当首页试自动发现（粘博客首页也能订上）
                alt = discover_feed_link(body.decode("utf-8", "ignore"), url)
                if alt and alt != url:
                    return await add_feed(alt, key=key)
            label = title
    feed = RssFeed(key=key, label=label or key, url=url)
    config.update_rss_feeds([*feeds, feed])
    return feed


def rename_feed(key: str, new_label: str) -> RssFeed | None:
    """改显示名；名字清空 / key 不存在返回 None（不动配置）。"""
    new_label = new_label.strip()
    if not new_label:
        return None
    feeds = config.rss_feeds()
    if key not in {f.key for f in feeds}:
        return None
    renamed = RssFeed(
        key=key, label=new_label, url=next(f.url for f in feeds if f.key == key)
    )
    config.update_rss_feeds([renamed if f.key == key else f for f in feeds])
    return renamed


async def add_from_input(text: str) -> tuple[list[RssFeed], str]:
    """添加入口的智能分流：http(s) 地址 → 单条添加；本地路径 → OPML 导入。

    返回 (本次新增, 结果描述)；`~` 开头的路径自动展开。CLI 的 add 与 TUI
    订阅管理屏的添加框共用这里，行为永远一致。
    """
    text = text.strip()
    if not text:
        raise SubscriptionError("输入为空")
    if text.startswith(("http://", "https://")):
        feed = await add_feed(text)
        return [feed], f"已订阅 {feed.key}：{feed.label}"
    path = Path(text).expanduser()
    try:
        opml_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SubscriptionError(f"文件读不了：{exc}") from None
    try:
        added, skipped = import_opml(opml_text)
    except ET.ParseError as exc:
        raise SubscriptionError(f"不是合法的 OPML：{exc}") from None
    detail = f"，跳过 {len(skipped)} 条" if skipped else ""
    return added, f"导入 {len(added)} 条订阅{detail}"


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
