"""RSS 解析层基线：RSS 2.0 / Atom 字段映射、视图化元信息、正文清洗。

纯函数直测（fixture XML 内联），不碰网络；取数层的 MockTransport 测试见
test_rss_source.py。
"""

from __future__ import annotations

from datetime import UTC, datetime

from feedscope.core.media import html_to_md
from feedscope.sources.rss.parse import parse_feed, parse_feed_discovery, parse_feed_info
from tests.sources.rss.fixtures import ATOM_XML, RSS_XML

_EPOCH_NOV2 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC).timestamp()  # 08:00 +0800


def test_rss2_fields_mapped() -> None:
    items = parse_feed(RSS_XML, feed_key="k", feed_label="测试源", all_view=False)
    assert len(items) == 2  # 无地址条目（guid/link 双缺）整条跳过
    first = items[0]
    assert first.source == "rss"
    assert first.id == "k/post-1"  # id = 订阅 key/guid，跨订阅不撞
    assert first.url == "https://example.com/1"
    assert first.title == "第一篇 & 含标签"  # 实体还原 + 标签剥掉
    assert first.kind == ""
    assert first.extra["author"] == "张三"
    assert first.extra["published"] == _EPOCH_NOV2
    assert "全文 1" in first.extra["content_html"]  # content 优先于 summary


def test_meta_by_view() -> None:
    """聚合视图订阅名走源名列（认出处且不成噪音），单订阅视图不占列。"""
    merged = parse_feed(RSS_XML, feed_key="k", feed_label="测试源", all_view=True)
    single = parse_feed(RSS_XML, feed_key="k", feed_label="测试源", all_view=False)
    # 订阅名进 tag（源名列），元信息只剩作者——拼进元信息列会让右对齐列左缘参差
    assert merged[0].meta == "张三"
    assert merged[0].tag == "测试源"
    assert single[0].meta == "张三"
    assert single[0].tag == ""  # 单订阅视图整屏同名，不占列
    assert merged[1].meta == ""  # 无作者的条目：空元信息（订阅名不垫底）
    assert merged[1].tag == "测试源"
    assert single[1].meta == ""


def test_atom_and_guid_fallback() -> None:
    items = parse_feed(ATOM_XML, feed_key="a", feed_label="Atom", all_view=False)
    assert items[0].id == "a/tag:example.com,2026:1"
    assert items[0].url == "https://example.com/a1"
    assert items[0].extra["published"] == datetime(
        2026, 11, 1, 10, 0, tzinfo=UTC
    ).timestamp()
    assert items[0].extra["content_html"] == "atom 摘要"  # 无 content 退 summary


def test_fallback_url_used_when_entry_linkless() -> None:
    xml = RSS_XML.replace("<link>https://example.com/2</link>", "")
    items = parse_feed(xml, feed_key="k", feed_label="l", all_view=False, fallback_url="https://f.test")
    assert items[1].url == "https://f.test"
    assert items[1].id == "k/https://f.test"  # guid 也缺时链接顶上


def test_html_to_md_converts_image_links() -> None:
    md = html_to_md('<p>正文</p><img src="https://example.com/i.png" alt="图">')
    assert "正文" in md
    # 钉住形态（不能只做子串包含——[[图片]](url) 双层括号也含 [图片] 与 URL，曾漏网）：
    # 单层 [图片](url)，渲染成带下划线的可点击「图」
    assert "[图片](https://example.com/i.png)" in md
    assert "[[图片]]" not in md and "](http" not in md.replace("[图片](http", "")
    assert "<img" not in md


def test_html_to_md_keeps_query_string_intact() -> None:
    """URL 绕过 html2text（stash 记号）：& 不被实体转义弄坏。"""
    md = html_to_md('<img src="https://example.com/i.png?x=1&amp;y=2">')
    assert "[图片](https://example.com/i.png?x=1&y=2)" in md
    assert "&amp;" not in md and ";" not in md.split("y=2")[0].split("?")[1]


def test_html_to_md_empty() -> None:
    assert html_to_md("") == ""


def test_html_to_md_unwraps_anchored_image() -> None:
    """lightbox 的 <a 原图><img 缩略图></a> 塌缩成一个记号，取 <a> 的原图直链。

    不塌会生成 [[图片](缩略图)](原图) 嵌套链接——markdown 不允许嵌套，
    整段按字面露出（V2EX 自动内联同形态，靠这条管线兜住）。"""
    md = html_to_md(
        '<a href="https://example.com/full.png"><img src="https://example.com/thumb.png"></a>'
    )
    assert md == "[图片](https://example.com/full.png)"


def test_html_to_md_keeps_text_links() -> None:
    """普通文字链接照常转 markdown 链接（只包图的锚点才塌缩）。"""
    md = html_to_md('<p>看<a href="https://example.com/post">这篇</a></p>')
    assert md == "看[这篇](https://example.com/post)"


def test_probe_title_and_entry_count() -> None:
    title, count = parse_feed_info(RSS_XML)
    assert title == "测试源" and count == 3
    assert parse_feed_info("<html><body>不是 feed</body></html>") == ("", 0)  # 普通网页两空


def test_discover_feed_link() -> None:
    head = '<link rel="alternate" type="application/atom+xml" href="{}">'
    # 相对 href 以页面地址绝对化；绝对 href 原样保留
    assert parse_feed_discovery(head.format("feed.xml"), "https://a.test/blog/") == (
        "https://a.test/blog/feed.xml"
    )
    assert parse_feed_discovery(head.format("https://b.test/atom"), "https://x.test/") == (
        "https://b.test/atom"
    )
    # 非 alternate、非 feed 类型、无 href 都不算
    assert parse_feed_discovery('<link rel="stylesheet" href="/f">', "https://a.test/") == ""
    assert parse_feed_discovery('<link rel="alternate" type="text/html" href="/h">', "https://a.test/") == ""
    assert parse_feed_discovery('<link rel="alternate" type="application/rss+xml">', "https://a.test/") == ""


# ---- JSON Feed：首页自动发现认 feed+json，解析也得认（以前订阅进去就打不开）----

JSON_FEED = """{
  "version": "https://jsonfeed.org/version/1.1",
  "title": "My <b>Blog</b>",
  "items": [
    {"id": "1", "url": "https://blog.example.com/posts/1", "title": "Hello <i>world</i>",
     "content_html": "<p>body with <a href=\\"/about/\\">a relative link</a> and an <img src=\\"/static/a.png\\"></p>",
     "date_published": "2026-11-02T08:00:00+08:00",
     "authors": [{"name": "Ada"}, {"name": "Grace"}],
     "attachments": [{"url": "https://cdn.example.com/ep1.mp3", "mime_type": "audio/mpeg"},
                     {"url": "https://cdn.example.com/show.pdf", "mime_type": "application/pdf"}]},
    {"id": "2", "url": "https://blog.example.com/posts/2", "title": "Plain notes",
     "content_text": "first line\\nsecond line\\n\\nnew paragraph <not a tag>"}
  ]
}"""


def test_json_feed_is_recognised_and_parsed() -> None:
    """顶层是带 items 的 JSON 对象 = JSON Feed：probe 认它（订阅取名用），parse 映射成同形的条目。"""
    import asyncio

    assert parse_feed_info(JSON_FEED) == ("My Blog", 2)
    assert parse_feed_info(b"\xef\xbb\xbf" + JSON_FEED.encode()) == ("My Blog", 2)  # 带 BOM 的字节体
    items = parse_feed(JSON_FEED, feed_key="blog", feed_label="My Blog", all_view=False)
    assert [it.id for it in items] == ["blog/1", "blog/2"]
    first, plain = items
    assert first.url == "https://blog.example.com/posts/1"
    assert first.title == "Hello world"  # 标题里的标签剥掉
    assert first.extra["author"] == "Ada" and first.extra["published"] == _EPOCH_NOV2
    assert first.extra["base"] == first.url  # 正文相对地址的绝对化基准
    assert first.extra["enclosures"] == [
        {"url": "https://cdn.example.com/ep1.mp3", "type": "audio/mpeg"},
        {"url": "https://cdn.example.com/show.pdf", "type": "application/pdf"},
    ]
    doc = asyncio.run(_detail(first)).doc
    body = doc.description_md
    assert "[a relative link](https://blog.example.com/about/)" in body  # 相对链接绝对化
    assert "[图片](https://blog.example.com/static/a.png)" in body
    assert "[音频](https://cdn.example.com/ep1.mp3)" in body
    assert "[附件](https://cdn.example.com/show.pdf)" in body
    assert "<p>" not in body and "not a tag" in asyncio.run(_detail(plain)).doc.description_md


def _detail(item):
    from feedscope.core.registry import get_source

    return get_source("rss").open_detail(item)


def test_not_json_feed_shapes_fall_through() -> None:
    """普通 JSON / 坏 JSON / 数组顶层都不是 feed：probe 两空、parse 不炸。"""
    assert parse_feed_info('{"hello": "world"}') == ("", 0)
    assert parse_feed_info("{ not json") == ("", 0)
    assert parse_feed_info("[1, 2]") == ("", 0)
    assert parse_feed("{ not json", feed_key="k", feed_label="L", all_view=False) == []


# ---- enclosure（播客 / 视频源）：正文后追加记号链接 ----

PODCAST_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>Pod</title>
<item><title>Episode 7</title><link>https://pod.example/7</link><guid>7</guid>
<description>Shownotes</description>
<enclosure url="https://cdn.example/7.mp3" length="123" type="audio/mpeg"/></item>
<item><title>With video</title><link>https://pod.example/8</link><guid>8</guid>
<description>Notes</description>
<enclosure url="https://cdn.example/8.mp4" type="video/mp4"/></item>
</channel></rss>"""


def test_enclosures_become_marker_links_after_the_body() -> None:
    """播客条目以前只剩 shownotes（enclosure 被整个忽略）：现在正文后给出媒体直链。"""
    import asyncio

    items = parse_feed(PODCAST_XML, feed_key="pod", feed_label="Pod", all_view=False)
    bodies = [asyncio.run(_detail(it)).doc.description_md for it in items]
    assert bodies[0] == "Shownotes\n\n[音频](https://cdn.example/7.mp3)"
    assert bodies[1] == "Notes\n\n[视频](https://cdn.example/8.mp4)"


# ---- 正文相对地址绝对化 + 懒加载图片取真实地址 ----


def test_html_to_md_resolves_relative_urls_against_base() -> None:
    html = '<p><a href="/about/?a=1&amp;b=2">about</a> <img src="../img/a.png"> <a href="#fn">fn</a> <a href="mailto:x@y.z">mail</a></p>'
    assert html_to_md(html, base="https://blog.example.com/posts/1/") == (
        "[about](https://blog.example.com/about/?a=1&b=2) "
        "[图片](https://blog.example.com/posts/img/a.png) fn [mail](mailto:x@y.z)"  # 纯锚点链接不成链（html2text 既有行为）
    )
    # 不给 base：行为同前（相对地址原样）
    assert "[about](/about/?a=1&b=2)" in html_to_md(html)


def test_html_to_md_prefers_lazy_load_attributes_over_placeholder_src() -> None:
    """懒加载站的 src 是占位图：真实地址在 data-src / data-original 里。"""
    assert html_to_md('<img src="https://cdn.example/1x1.gif" data-src="https://cdn.example/real.jpg">') == (
        "[图片](https://cdn.example/real.jpg)"
    )
    assert html_to_md('<img data-original="https://cdn.example/real2.jpg" src="data:image/gif;base64,x">') == (
        "[图片](https://cdn.example/real2.jpg)"
    )
    assert html_to_md('<img src="https://cdn.example/plain.png">') == "[图片](https://cdn.example/plain.png)"
