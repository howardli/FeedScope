"""订阅管理业务：rss 的增删查 + OPML 进出，reddit 的 subreddit 增删查。

只做管理编排：读改写 sources.toml（写路径统一走 config.update_feeds，
enabled/hide 联动在那里）、rss add 拉一次 feed 取标题当默认显示名、
reddit add 拉一次 r/{name}/about 校验存在性并取 display_name。TUI 订阅
管理屏不直接调这里的函数，走 SubsStore 适配器（屏保持源无关）；CLI 的
rss 子命令直接调 rss 函数。OPML 用 stdlib ElementTree 解析（嵌套目录
拍平），导出手写模板——结构固定，不值得引库。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from feedscope.core import config
from feedscope.core.config import RssFeed
from feedscope.core.source import SourceError
from feedscope.sources.reddit.parse import parse_subreddit_input
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


def _rename(source_id: str, key: str, new_label: str) -> RssFeed | None:
    """改显示名（rss / reddit 共用）；名字清空 / key 不存在返回 None（不动配置）。"""
    new_label = new_label.strip()
    if not new_label:
        return None
    feeds = config.feeds_for(source_id)
    if key not in {f.key for f in feeds}:
        return None
    renamed = RssFeed(
        key=key, label=new_label, url=next(f.url for f in feeds if f.key == key)
    )
    config.update_feeds(source_id, [renamed if f.key == key else f for f in feeds])
    return renamed


def rename_feed(key: str, new_label: str) -> RssFeed | None:
    """改 rss 订阅的显示名。"""
    return _rename("rss", key, new_label)


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


def _remove(source_id: str, key: str) -> RssFeed | None:
    """删一条订阅（rss / reddit 共用），返回被删的；key 不存在返回 None（不动配置）。"""
    feeds = config.feeds_for(source_id)
    kept = [f for f in feeds if f.key != key]
    if len(kept) == len(feeds):
        return None
    config.update_feeds(source_id, kept, drop_enabled_when_empty=source_id == "rss")
    return next(f for f in feeds if f.key == key)


def remove_feed(key: str) -> RssFeed | None:
    """删一条 rss 订阅。"""
    return _remove("rss", key)


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


# ---- Reddit subreddit 订阅 ----

# 与 Reddit 内置流（首页/热门）撞名的 subreddit 不能当订阅 key（会遮住内置流）
_REDDIT_BUILTIN = ("home", "popular")


async def add_subreddit(text: str, *, label: str | None = None) -> RssFeed:
    """加一个 subreddit 订阅并落盘；显示名缺省拉一次 r/{name}/about 的 display_name。

    输入四形态归一（裸名 / r/name / /r/name/ / 站点地址，见 parse_subreddit_input）；
    重复 / 撞内置流 / about 拉不到（subreddit 不存在或未登录）→ SubscriptionError
    （不动配置）。未登录时 about 的报错消息自带「需要登录」指引。
    """
    from feedscope.sources.reddit import _reddit_session
    from feedscope.sources.reddit.fetch import fetch_about

    name = parse_subreddit_input(text)
    if not name:
        raise SubscriptionError("没认出 subreddit 名（支持：rust、r/rust、/r/rust/ 或站点地址）")
    if name in _REDDIT_BUILTIN:
        raise SubscriptionError(f"{name} 与内置流重名（首页/热门），不能用作订阅名")
    feeds = config.feeds_for("reddit")
    if any(f.key == name for f in feeds):
        raise SubscriptionError(f"r/{name} 已经订阅过了")
    try:
        session = await _reddit_session(f"r/{name}")
        about = await fetch_about(session, name)
    except SourceError as exc:  # 含 LoginRequired：订阅管理屏没有账号动线，消息自带指引
        raise SubscriptionError(f"r/{name} 加不上：{exc}") from None
    display = str(about.get("display_name") or "") or name
    feed = RssFeed(key=name, label=label or display, url=f"https://www.reddit.com/r/{name}/")
    config.update_feeds("reddit", [*feeds, feed])
    return feed


async def add_subreddit_from_input(text: str) -> tuple[list[RssFeed], str]:
    """订阅管理屏的 reddit 添加入口（与 rss 的 add_from_input 同形，store 注入用）。"""
    feed = await add_subreddit(text)
    return [feed], f"已订阅 r/{feed.key}：{feed.label}"


def remove_subreddit(key: str) -> RssFeed | None:
    """删一个 subreddit 订阅；key 不存在返回 None（不动配置）。"""
    return _remove("reddit", key)


def rename_subreddit(key: str, new_label: str) -> RssFeed | None:
    """改 subreddit 订阅的显示名；名字清空 / key 不存在返回 None（不动配置）。"""
    return _rename("reddit", key, new_label)


# ---- 订阅管理屏的源适配器 ----


@dataclass(frozen=True)
class SubsStore:
    """一个 subscribable 源的订阅管理适配器：四操作 + 文案，订阅管理屏只认它。

    订阅概念不再只有 rss（reddit 的 subreddit、之后的 Telegram 频道共用
    一屏）：源的差异收进这个对象，屏保持通用（列表 + 增删改名），业务
    编排全部留在本模块。add 与 rss 的 add_from_input 同形——(新增, 概要)。
    """

    label: str  # 屏标题「{label} 订阅管理」
    add_placeholder: str  # 添加输入框占位（各源可输入的形态不同）
    hint: str  # 底部键位提示行
    empty: str  # 零订阅占位文案
    feeds: Callable[[], list[RssFeed]]  # 当前订阅表（列表顺序 = 配置顺序）
    add: Callable[[str], Awaitable[tuple[list[RssFeed], str]]]
    remove: Callable[[str], RssFeed | None]
    rename: Callable[[str, str], RssFeed | None]


def rss_store() -> SubsStore:
    return SubsStore(
        label="RSS",
        add_placeholder="RSS/Atom 地址、站点首页或 OPML 文件路径（可从 Finder 拖入终端）",
        hint="↑↓ 选择 · Enter 改名 · Insert 添加（地址 / 首页 / OPML 路径）· Delete 删除",
        empty="还没有订阅 · Insert 添加（可粘贴 OPML 路径）",
        feeds=config.rss_feeds,
        add=add_from_input,
        remove=remove_feed,
        rename=rename_feed,
    )


def reddit_store() -> SubsStore:
    return SubsStore(
        label="Reddit",
        add_placeholder="subreddit 名或地址（如 rust、r/rust、reddit.com/r/rust）",
        hint="↑↓ 选择 · Enter 改名 · Insert 添加 · Delete 删除",
        empty="还没有订阅 · Insert 添加（输入 subreddit 名即可）",
        feeds=lambda: config.feeds_for("reddit"),
        add=add_subreddit_from_input,
        remove=remove_subreddit,
        rename=rename_subreddit,
    )


# 源 id -> store 工厂：新增 subscribable 源在这里登记一个（订阅屏的入口分派）
_STORES: dict[str, Callable[[], SubsStore]] = {"rss": rss_store, "reddit": reddit_store}


def store_for(source_id: str) -> SubsStore:
    """订阅管理屏的入口工厂；未登记的 id = subscribable 声明与 store 不一致，尽早炸。"""
    if build := _STORES.get(source_id):
        return build()
    raise ValueError(f"{source_id} 没有订阅管理适配器（subscribable 与 SubsStore 缺一不可）")
