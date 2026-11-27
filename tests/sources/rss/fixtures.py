"""RSS 源测试夹具：RSS 2.0 / Atom 的最小文档。"""

from __future__ import annotations

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
