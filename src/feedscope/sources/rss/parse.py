"""解析层：RSS / Atom / JSON Feed -> FeedItem / 详情原料，纯函数不碰网络。

RSS/Atom 野源格式千奇百怪，容错原则「能救则救、救不了就丢条不丢流」：
标题带 HTML、缺 author、缺发布时间都只是字段级缺失；guid 与 link 双缺才
整条跳过（无法寻址，进详情 / 跳浏览器都没有落点）。

JSON Feed（jsonfeed.org）：自动发现与 Accept 头一直认 feed+json，但
feedparser 只认 XML——这种订阅以前写得进去，每次打开都报「站点可能已改版」。
现在按内容分流（顶层是带 items 的 JSON 对象就走这条），映射成与 RSS 同形的
FeedItem；content_text（纯文本）转成段落 HTML 走同一条详情管线。

enclosure / attachments（播客、视频源）：这里只把地址与 mime 存进 extra
（语言无关，进列表缓存），详情层再按当前语言追加 [音频]/[视频]/[附件] 记号。

正文里的相对地址（/static/a.png、?p=1）：以条目自己的页面地址（extra.base）
为基准在详情层绝对化——根路径的「图片链接」点开是打不开的。
"""

from __future__ import annotations

import calendar
import html as html_mod
import json
import re
from time import struct_time
from typing import Any
from urllib.parse import urljoin

import feedparser

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.text import strip_zwsp
from feedscope.core.timefmt import iso_epoch

SOURCE_ID = "rss"

# 标题/作者里的残余标签（<b>标题</b>、署名 <a> 之类），清理成纯文本
_TAGS = re.compile(r"<[^>]*>")
# 首页 HTML 里的 <link> 声明（自动发现 feed 用）
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_HREF = re.compile(r"""href=(["'])(.*?)\1""", re.IGNORECASE)
_FEED_TYPES = ("rss+xml", "atom+xml", "feed+json", "feed+xml")

# enclosure 的 mime 大类 -> 记号文案键（对不上的走 attachment）
_ENCLOSURE_KINDS: tuple[tuple[str, str], ...] = (
    ("audio/", "media.audio"),
    ("video/", "media.video"),
    ("image/", "media.image"),
)
_ENCLOSURE_FALLBACK = "media.attachment"


def _clean(text: object) -> str:
    """字段级清洗：去标签、还原实体、去零宽；None / 非 str 回空串。"""
    if not isinstance(text, str):
        return ""
    return strip_zwsp(html_mod.unescape(_TAGS.sub("", text)))


def _epoch(st: object) -> float:
    """feedparser 的 *_parsed（struct_time，UTC）-> unix 秒；缺省 0（排序沉底）。"""
    return float(calendar.timegm(st)) if isinstance(st, struct_time) and st.tm_year > 1 else 0.0


def _content_html(entry: dict[str, Any]) -> str:
    """正文 HTML：优先 content（多为全文），退 summary（多为摘要）。"""
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            value = first.get("value")  # 捕获收窄：get 的 isinstance 守卫不传导到下标访问
            if isinstance(value, str):
                return value
    summary = entry.get("summary")
    return summary if isinstance(summary, str) else ""


# ---- 条目构造（XML 与 JSON Feed 两条解析路共用）----


def _build(
    feed_key: str,
    feed_label: str,
    all_view: bool,
    *,
    guid: str,
    link: str,
    title: str,
    author: str,
    epoch: float,
    content_html: str,
    enclosures: list[dict[str, str]],
) -> FeedItem:
    return FeedItem(
        source=SOURCE_ID,
        id=f"{feed_key}/{guid or link}",
        title=title or t("words.no_title"),
        url=link,
        meta=author,
        # 聚合流的订阅名走源名列（拼进元信息列会让右对齐列左缘参差），
        # 单订阅流整屏同名不占列
        tag=feed_label if all_view else "",
        published=epoch or None,  # 0（无时间）不进时间列：整列有无看批内
        extra={
            "feed_key": feed_key,
            "feed_label": feed_label,
            "author": author,
            "published": epoch,
            "content_html": content_html,
            "base": link,  # 正文里相对地址的绝对化基准（条目自己的页面）
            "enclosures": enclosures,
        },
    )


def _enclosures(enclosures: object, links: object) -> list[dict[str, str]]:
    """enclosure / attachment -> [{url, type}]，去重保持顺序。

    enclosures：整组都是附件——feedparser 的 entry.enclosures，或 JSON Feed 的
    attachments（键是 url / mime_type）。links：feedparser 的 entry.links，里面
    混着条目自己的页面地址，只认 rel=enclosure 的那几项。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    candidates = [*(enclosures if isinstance(enclosures, list) else [])]
    candidates += [
        link
        for link in (links if isinstance(links, list) else [])
        if isinstance(link, dict) and link.get("rel") == "enclosure"
    ]
    for c in candidates:
        if not isinstance(c, dict):
            continue
        url = str(c.get("href") or c.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url, "type": str(c.get("type") or c.get("mime_type") or "")})
    return out


def parse_enclosure_marks(enclosures: object) -> list[tuple[str, str]]:
    """[{url, type}] -> [(记号文案键, url)]（详情层按当前语言解析记号）。"""
    marks: list[tuple[str, str]] = []
    for e in enclosures if isinstance(enclosures, list) else []:
        if not isinstance(e, dict) or not (url := str(e.get("url") or "")):
            continue
        mime = str(e.get("type") or "").lower()
        key = next(
            (k for prefix, k in _ENCLOSURE_KINDS if mime.startswith(prefix)),
            _ENCLOSURE_FALLBACK,
        )
        marks.append((key, url))
    return marks


# ---- XML（RSS / Atom）----


def parse_xml_feed(
    body: bytes | str, *, feed_key: str, feed_label: str, all_view: bool, fallback_url: str
) -> list[FeedItem]:
    parsed = feedparser.parse(body)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = entry.get("link") or fallback_url
        guid = entry.get("id")
        if not guid and not link:
            continue  # 双缺无法寻址
        items.append(
            _build(
                feed_key,
                feed_label,
                all_view,
                guid=str(guid or ""),
                link=link,
                title=_clean(entry.get("title")),
                author=_clean(entry.get("author")),
                epoch=_epoch(entry.get("published_parsed") or entry.get("updated_parsed")),
                content_html=_content_html(entry),
                enclosures=_enclosures(entry.get("enclosures"), entry.get("links")),            )
        )
    return items


# ---- JSON Feed ----


def _json_feed(body: bytes | str) -> dict[str, Any] | None:
    """JSON Feed 的顶层对象；不是（XML / 普通页面 / 坏 JSON）回 None。

    只看内容不看声明：首页自动发现会把 feed+json 的地址直接交进来，没有别处
    可查类型。判定 = 顶层 JSON 对象且 items 是数组。"""
    text = (
        body.decode("utf-8-sig", "ignore") if isinstance(body, bytes) else body.lstrip("\ufeff")
    )
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None


def _paragraphs(text: str) -> str:
    """纯文本 -> 段落 HTML：空行分段、单换行转 <br>（文本先转义），走详情的同一管线。"""
    blocks = [
        html_mod.escape(block.strip()).replace("\n", "<br>")
        for block in re.split(r"\n\s*\n", text.strip())
    ]
    return "".join(f"<p>{block}</p>" for block in blocks)


def _author_of(value: object) -> str:
    """JSON Feed 的作者：authors 数组（对象或字符串）或旧式的单个 author 对象。"""
    if isinstance(value, dict):
        return _clean(str(value.get("name") or ""))
    if isinstance(value, list):
        return next((name for name in map(_author_of, value) if name), "")
    return _clean(value)


def parse_json_feed(
    feed: dict[str, Any], *, feed_key: str, feed_label: str, all_view: bool
) -> list[FeedItem]:
    items: list[FeedItem] = []
    for raw in feed.get("items") or []:
        if not isinstance(raw, dict):
            continue
        link = str(raw.get("url") or raw.get("external_url") or "")
        guid = str(raw.get("id") or "")
        if not guid and not link:
            continue  # 双缺无法寻址（与 XML 同一条规则）
        content = str(raw.get("content_html") or "")
        if not content and isinstance(plain := raw.get("content_text"), str):
            content = _paragraphs(plain)
        items.append(
            _build(
                feed_key,
                feed_label,
                all_view,
                guid=guid,
                link=link,
                title=_clean(str(raw.get("title") or "")),
                author=_author_of(raw.get("authors")) or _author_of(raw.get("author")),
                epoch=iso_epoch(
                    str(raw.get("date_published") or raw.get("date_modified") or "")
                )
                or 0.0,
                content_html=content,
                enclosures=_enclosures(raw.get("attachments"), None),
            )
        )
    return items


def parse_feed(
    body: bytes | str,
    *,
    feed_key: str,
    feed_label: str,
    all_view: bool,
    fallback_url: str = "",
) -> list[FeedItem]:
    """一个订阅的 XML 或 JSON Feed -> FeedItem 列表（保持源内顺序，通常已是倒序）。

    all_view 决定元信息列内容：聚合「全部」里必须能认出条目来自哪个订阅
    （订阅名打头），单订阅视图里订阅名是噪音、只留作者。extra 带 detail
    所需的全部字段（feed 自带正文，进详情不重新抓）。
    """
    if (feed := _json_feed(body)) is not None:
        return parse_json_feed(feed, feed_key=feed_key, feed_label=feed_label, all_view=all_view)
    return parse_xml_feed(
        body, feed_key=feed_key, feed_label=feed_label, all_view=all_view, fallback_url=fallback_url
    )


def parse_feed_info(body: bytes | str) -> tuple[str, int]:
    """(feed 自述标题, 条目数)：订阅管理判断「这是不是 feed」与取默认显示名。"""
    if (feed := _json_feed(body)) is not None:
        return _clean(str(feed.get("title") or "")), len(feed["items"])
    parsed = feedparser.parse(body)
    title = _clean(parsed.feed.get("title")) if getattr(parsed, "feed", None) else ""
    return title, len(parsed.entries)


def parse_feed_discovery(html: str, base_url: str) -> str:
    """首页 HTML -> feed 地址：<link rel="alternate" type="…rss/atom…">，
    相对 href 以 base_url 绝对化；找不到回空串。"""
    for tag in _LINK_TAG.findall(html):
        low = tag.lower()
        if "alternate" not in low or not any(t in low for t in _FEED_TYPES):
            continue
        m = _HREF.search(tag)
        if m and m.group(2):
            return urljoin(base_url, str(m.group(2)))  # group() 存根是 Any，str() 收口
    return ""
