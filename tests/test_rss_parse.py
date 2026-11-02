"""RSS 解析层基线：RSS 2.0 / Atom 字段映射、视图化元信息、正文清洗。

纯函数直测（fixture XML 内联），不碰网络；取数层的 MockTransport 测试见
test_rss_source.py。
"""

from __future__ import annotations

from datetime import UTC, datetime

from feedscope.core.media import html_to_md
from feedscope.sources.rss.parse import discover_feed_link, parse_feed, probe

_EPOCH_NOV2 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC).timestamp()  # 08:00 +0800

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>测试源</title><link>https://example.com/</link>
<item>
  <title>第一篇 &amp; 含&lt;b&gt;标签&lt;/b&gt;</title>
  <link>https://example.com/1</link>
  <guid isPermaLink="false">post-1</guid>
  <dc:creator>张三</dc:creator>
  <pubDate>Mon, 02 Nov 2026 08:00:00 +0800</pubDate>
  <description>摘要 1</description>
  <content:encoded><![CDATA[<p>全文 1 <img src="https://example.com/i.png" alt="图"/></p>]]></content:encoded>
</item>
<item>
  <title>第二篇</title>
  <link>https://example.com/2</link>
  <pubDate>Tue, 27 Oct 2026 09:30:00 +0800</pubDate>
  <description>摘要 2</description>
</item>
<item><title>无地址条目</title></item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom 源</title>
<entry>
  <title>Atom 篇</title>
  <id>tag:example.com,2026:1</id>
  <link href="https://example.com/a1"/>
  <updated>2026-11-01T10:00:00Z</updated>
  <summary>atom 摘要</summary>
</entry>
</feed>"""


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
    """聚合视图订阅名打头（认出处），单订阅视图只留作者（订阅名是噪音）。"""
    merged = parse_feed(RSS_XML, feed_key="k", feed_label="测试源", all_view=True)
    single = parse_feed(RSS_XML, feed_key="k", feed_label="测试源", all_view=False)
    assert merged[0].meta == "测试源 · 张三"
    assert single[0].meta == "张三"
    assert merged[1].meta == "测试源"  # 无作者的条目只剩订阅名
    assert single[1].meta == ""  # 单订阅视图无作者 = 空元信息


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
    title, count = probe(RSS_XML)
    assert title == "测试源" and count == 3
    assert probe("<html><body>不是 feed</body></html>") == ("", 0)  # 普通网页两空


def test_discover_feed_link() -> None:
    head = '<link rel="alternate" type="application/atom+xml" href="{}">'
    # 相对 href 以页面地址绝对化；绝对 href 原样保留
    assert discover_feed_link(head.format("feed.xml"), "https://a.test/blog/") == (
        "https://a.test/blog/feed.xml"
    )
    assert discover_feed_link(head.format("https://b.test/atom"), "https://x.test/") == (
        "https://b.test/atom"
    )
    # 非 alternate、非 feed 类型、无 href 都不算
    assert discover_feed_link('<link rel="stylesheet" href="/f">', "https://a.test/") == ""
    assert discover_feed_link('<link rel="alternate" type="text/html" href="/h">', "https://a.test/") == ""
    assert discover_feed_link('<link rel="alternate" type="application/rss+xml">', "https://a.test/") == ""
