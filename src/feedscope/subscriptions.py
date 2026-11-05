"""订阅管理业务：rss 的增删查 + OPML 进出，reddit 的 subreddit 与 telegram
的频道增删查。

只做管理编排：读改写 sources.toml（写路径统一走 config.update_feeds，
enabled/hide 联动在那里）、rss add 拉一次 feed 取标题当默认显示名、
reddit add 拉一次 r/{name}/about 校验存在性并取 display_name、telegram
add 拉一次 t.me/s 预览页校验可读并取频道名。TUI 订阅管理屏不直接调这里
的函数，走 SubsStore 适配器（屏保持源无关）；CLI 的 rss 子命令直接调
rss 函数。OPML 用 stdlib ElementTree 解析（嵌套目录拍平），导出手写
模板——结构固定，不值得引库。
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
from feedscope.core.config import Subscription
from feedscope.core.registry import get_class
from feedscope.core.source import SourceError
from feedscope.sources.reddit.parse import parse_subreddit_input
from feedscope.sources.rss.parse import discover_feed_link, probe


def _reserved(source_id: str) -> tuple[str, ...]:
    """订阅 key 的保留字（聚合流名 / 内置流名）：一律读源自己的声明
    （Source.reserved_feed_keys，单一出处）——源新增内置流后保留字自动
    跟上，不会再出现这里抄一份、那边忘了同步的窗口。"""
    cls = get_class(source_id)
    return cls.reserved_feed_keys if cls is not None else ()


class SubscriptionError(Exception):
    """订阅操作的业务失败（重复 / key 冲突 / 文件读不了）：消息面向用户可直接打印。"""


class BadSubscriptionFile(SubscriptionError):
    """OPML 文件读不了 / 不是合法 OPML：用法级错误（CLI 退出码 2，与 argparse 约定一致）。"""


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


async def add_feed(url: str, *, key: str | None = None, label: str | None = None) -> Subscription:
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
    elif key in _reserved("rss"):
        raise SubscriptionError(f"{key} 是聚合流保留字，不能用作订阅 key")
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
    feed = Subscription(key=key, label=label or key, url=url)
    config.update_rss_feeds([*feeds, feed])
    return feed


def _rename(source_id: str, key: str, new_label: str) -> Subscription | None:
    """改显示名（rss / reddit 共用）；名字清空 / key 不存在返回 None（不动配置）。"""
    new_label = new_label.strip()
    if not new_label:
        return None
    feeds = config.feeds_for(source_id)
    if key not in {f.key for f in feeds}:
        return None
    renamed = Subscription(key=key, label=new_label, url=next(f.url for f in feeds if f.key == key))
    config.update_feeds(source_id, [renamed if f.key == key else f for f in feeds])
    return renamed


def rename_feed(key: str, new_label: str) -> Subscription | None:
    """改 rss 订阅的显示名。"""
    return _rename("rss", key, new_label)


async def add_from_input(text: str) -> tuple[list[Subscription], str]:
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
    added, skipped = import_opml(read_opml_file(text))
    detail = f"，跳过 {len(skipped)} 条" if skipped else ""
    return added, f"导入 {len(added)} 条订阅{detail}"


def _remove(source_id: str, key: str, *, drop_when_empty: bool) -> Subscription | None:
    """删一条订阅（各源共用），返回被删的；key 不存在返回 None（不动配置）。

    drop_when_empty：删到空是否从 enabled 摘掉该源——rss/telegram 的 feeds
    全来自订阅（删空 = 没有任何流）摘；reddit 有内置流不摘。
    """
    feeds = config.feeds_for(source_id)
    kept = [f for f in feeds if f.key != key]
    if len(kept) == len(feeds):
        return None
    config.update_feeds(source_id, kept, drop_enabled_when_empty=drop_when_empty)
    return next(f for f in feeds if f.key == key)


def remove_feed(key: str) -> Subscription | None:
    """删一条 rss 订阅。"""
    return _remove("rss", key, drop_when_empty=True)


# ---- OPML 进出 ----


def read_opml_file(path: str | Path) -> str:
    """读 OPML 文件文本（`~` 展开）；读不了抛 BadSubscriptionFile（CLI/TUI 同一出口）。"""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise BadSubscriptionFile(f"OPML 文件读不了：{exc}") from None


def parse_opml(text: str) -> list[tuple[str, str]]:
    """OPML 文本 -> [(label, xmlUrl)]；嵌套目录拍平，无 xmlUrl 的节点只当容器。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise BadSubscriptionFile(f"不是合法的 OPML：{exc}") from None
    out: list[tuple[str, str]] = []
    for outline in root.iter("outline"):
        url = (outline.get("xmlUrl") or "").strip()
        if not url:
            continue
        label = (outline.get("text") or outline.get("title") or "").strip()
        out.append((label, url))
    return out


def import_opml(text: str) -> tuple[list[Subscription], list[str]]:
    """OPML 批量导入（幂等合并）：返回 (新增订阅, 跳过原因列表)。

    文件读不了 / 不是合法 OPML 抛 BadSubscriptionFile（用法级错误）。
    已订阅的地址（含 OPML 内部重复）跳过；label 用 outline 的 text/title，
    缺了回 key；一次落盘（中途失败不写半截配置）。
    """
    feeds = config.rss_feeds()
    known_urls = {f.url for f in feeds}
    known_keys = {f.key for f in feeds}
    added: list[Subscription] = []
    skipped: list[str] = []
    for label, url in parse_opml(text):
        if url in known_urls:
            skipped.append(f"{label or url}：地址已订阅")
            continue
        key = derive_key(url, known_keys)
        known_urls.add(url)
        known_keys.add(key)
        added.append(Subscription(key=key, label=label or key, url=url))
    if added:
        config.update_rss_feeds([*feeds, *added])
    return added, skipped


_XML_QUOTE = '"'


def _attr(value: str) -> str:
    """字符串 -> XML 属性值：双引号包裹，& < > 与引号本体转义。"""
    escaped = escape(value, {_XML_QUOTE: "&quot;"})
    return f"{_XML_QUOTE}{escaped}{_XML_QUOTE}"


def export_opml(feeds: list[Subscription]) -> str:
    """订阅表 -> OPML 2.0 文本（标准阅读器都认，可再迁去别家）。"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head><title>FeedScope 订阅导出</title></head>",
        "  <body>",
    ]
    lines += [
        f'    <outline type="rss" text={_attr(f.label)} xmlUrl={_attr(f.url)} />' for f in feeds
    ]
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)


# ---- Reddit subreddit 订阅 ----
# （订阅 key 的保留字＝内置流名 home/popular，读源声明：见 _reserved）


# ---- Telegram 频道订阅 ----
# （订阅 key 的保留字＝聚合流名 all，读源声明：见 _reserved）


async def add_subreddit(text: str, *, label: str | None = None) -> Subscription:
    """加一个 subreddit 订阅并落盘；显示名缺省拉一次 r/{name}/about 的 display_name。

    输入四形态归一（裸名 / r/name / /r/name/ / 站点地址，见 parse_subreddit_input）；
    重复 / 撞内置流 / about 拉不到（subreddit 不存在或未登录）→ SubscriptionError
    （不动配置）。未登录时 about 的报错消息自带「需要登录」指引。
    """
    from feedscope.sources.reddit import require_login
    from feedscope.sources.reddit.fetch import fetch_about

    name = parse_subreddit_input(text)
    if not name:
        raise SubscriptionError("没认出 subreddit 名（支持：rust、r/rust、/r/rust/ 或站点地址）")
    if name in _reserved("reddit"):
        raise SubscriptionError(f"{name} 与内置流重名（首页/热门），不能用作订阅名")
    feeds = config.feeds_for("reddit")
    if any(f.key == name for f in feeds):
        raise SubscriptionError(f"r/{name} 已经订阅过了")
    try:
        await require_login(f"r/{name}")
        about = await fetch_about(name)
    except SourceError as exc:  # 含 LoginRequired：订阅管理屏没有账号动线，消息自带指引
        raise SubscriptionError(f"r/{name} 加不上：{exc}") from None
    display = str(about.get("display_name") or "") or name
    feed = Subscription(key=name, label=label or display, url=f"https://www.reddit.com/r/{name}/")
    config.update_feeds("reddit", [*feeds, feed])
    return feed


async def add_subreddit_from_input(text: str) -> tuple[list[Subscription], str]:
    """订阅管理屏的 reddit 添加入口（与 rss 的 add_from_input 同形，store 注入用）。"""
    feed = await add_subreddit(text)
    return [feed], f"已订阅 r/{feed.key}：{feed.label}"


def remove_subreddit(key: str) -> Subscription | None:
    """删一个 subreddit 订阅；key 不存在返回 None（不动配置）。"""
    return _remove("reddit", key, drop_when_empty=False)


def rename_subreddit(key: str, new_label: str) -> Subscription | None:
    """改 subreddit 订阅的显示名；名字清空 / key 不存在返回 None（不动配置）。"""
    return _rename("reddit", key, new_label)


async def add_channel(text: str, *, label: str | None = None) -> Subscription:
    """加一个 Telegram 频道订阅并落盘；显示名缺省拉一次预览页的频道名。

    输入形态见 parse_telegram_input（durov / @durov / t.me/durov / 帖子直链）；
    重复 / 撞保留字 all / 预览页拉不到任何消息（不存在、为空、站长关了 Web
    预览）→ SubscriptionError（不动配置）。
    """
    from feedscope.sources.telegram.fetch import fetch_channel_html
    from feedscope.sources.telegram.parse import (
        channel_title,
        parse_channel_html,
        parse_telegram_input,
    )

    name = parse_telegram_input(text)
    if not name:
        raise SubscriptionError("没认出频道名（支持：durov、@durov、t.me/durov 或帖子链接）")
    if name in _reserved("telegram"):
        raise SubscriptionError("all 是聚合流保留字，不能用作订阅名")
    feeds = config.feeds_for("telegram")
    if any(f.key == name for f in feeds):
        raise SubscriptionError(f"@{name} 已经订阅过了")
    html = await fetch_channel_html(name)  # SourceError 顺势上抛（HTTP 层的中文消息够用）
    items = parse_channel_html(html, channel=name, label=name)
    if not items:
        raise SubscriptionError(f"@{name} 拉不到任何帖子：频道可能不存在、为空，或关闭了 Web 预览")
    display = channel_title(html) or name
    feed = Subscription(key=name, label=label or display, url=f"https://t.me/{name}/")
    config.update_feeds("telegram", [*feeds, feed])
    return feed


async def add_channel_from_input(text: str) -> tuple[list[Subscription], str]:
    """订阅管理屏的 Telegram 添加入口（与 rss 的 add_from_input 同形，store 注入用）。"""
    feed = await add_channel(text)
    return [feed], f"已订阅 @{feed.key}：{feed.label}"


def remove_channel(key: str) -> Subscription | None:
    """删一个频道订阅；key 不存在返回 None（不动配置）。"""
    return _remove("telegram", key, drop_when_empty=True)


def rename_channel(key: str, new_label: str) -> Subscription | None:
    """改频道订阅的显示名；名字清空 / key 不存在返回 None（不动配置）。"""
    return _rename("telegram", key, new_label)


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
    feeds: Callable[[], list[Subscription]]  # 当前订阅表（列表顺序 = 配置顺序）
    add: Callable[[str], Awaitable[tuple[list[Subscription], str]]]
    remove: Callable[[str], Subscription | None]
    rename: Callable[[str, str], Subscription | None]


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


def telegram_store() -> SubsStore:
    return SubsStore(
        label="Telegram",
        add_placeholder="频道名或链接（如 durov、@durov、t.me/durov）",
        hint="↑↓ 选择 · Enter 改名 · Insert 添加 · Delete 删除",
        empty="还没有订阅 · Insert 添加（输入频道名或链接即可）",
        feeds=lambda: config.feeds_for("telegram"),
        add=add_channel_from_input,
        remove=remove_channel,
        rename=rename_channel,
    )


# 源 id -> store 工厂：新增 subscribable 源在这里登记一个（订阅屏的入口分派）
_STORES: dict[str, Callable[[], SubsStore]] = {
    "rss": rss_store,
    "reddit": reddit_store,
    "telegram": telegram_store,
}


def store_for(source_id: str) -> SubsStore:
    """订阅管理屏的入口工厂；未登记的 id = subscribable 声明与 store 不一致，尽早炸。"""
    if build := _STORES.get(source_id):
        return build()
    raise ValueError(f"{source_id} 没有订阅管理适配器（subscribable 与 SubsStore 缺一不可）")
