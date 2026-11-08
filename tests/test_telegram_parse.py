"""Telegram 解析层测试：t.me/s 预览 HTML -> FeedItem / 正文 markdown。

fixture 是 t.me/s 线上页面的最小裁剪（容器类名、data-post、tg-emoji 的
i/b 壳、photo_wrap 的 background-image、views 的格式化文本都照抄实测），
覆盖：文字/图片/视频三类帖、emoji 壳塌缩、纯媒体帖标题、服务消息跳过、
输入归一四形态。
"""

from __future__ import annotations

from feedscope.core.timefmt import fmt_iso
from feedscope.sources.telegram.parse import (
    channel_title,
    parse_channel_html,
    parse_telegram_input,
)


def _msg(body: str, post: str) -> str:
    """一条消息的容器骨架：wrap > widget（data-post）> body。"""
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text js-widget_message" data-post="{post}">'
        f"{body}"
        "</div></div>"
    )


_TEXT_MSG = _msg(
    # 实测形态：tg-emoji 包 i（背景图兜底）包 b（字符本体），正文含 b/br/a
    '<div class="tgme_widget_message_text js-message_text" dir="auto">'
    '<tg-emoji emoji-id="1"><i class="emoji" style="background-image:url(\'//telegram.org/img/emoji/40/x.png\')">'
    "<b>🩵</b></i></tg-emoji> Telegram now has a <b>Rich Text Editor</b>.<br/><br/>"
    'See <a href="https://telegram.org/blog">the blog</a>.</div>'
    '<span class="tgme_widget_message_views">5.87M</span>'
    '<span class="tgme_widget_message_from_author">Pavel Durov</span>'
    '<time datetime="2026-07-16T20:45:25+00:00"><a href="https://t.me/durov/531">Jul 17</a></time>',
    "durov/531",
)

_VIDEO_MSG = _msg(
    '<a class="tgme_widget_message_video_player js-message_video_player" href="https://t.me/durov/532">'
    '<i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn4.telesco.pe/file/thumb\')"></i></a>'
    '<div class="tgme_widget_message_text js-message_text" dir="auto">New feature demo.</div>'
    '<span class="tgme_widget_message_views">6.58M</span>'
    '<time datetime="2026-07-19T21:45:01+00:00"></time>',
    "durov/532",
)

_PHOTO_MSG = _msg(
    '<a class="tgme_widget_message_photo_wrap 123" href="https://t.me/durov/533" '
    "style=\"width:800px;background-image:url('https://cdn4.telesco.pe/file/photo')\">"
    "</a>"
    '<span class="tgme_widget_message_views">7.06M</span>'
    '<time datetime="2026-07-21T14:36:55+00:00"></time>',
    "durov/533",
)

# 服务消息（无 data-post）：没有正文，应整条跳过
_SERVICE_MSG = '<div class="tgme_widget_message_service">Channel created</div>'

HTML = (
    '<html><head><meta property="og:title" content="Pavel Durov"/></head><body>'
    + _TEXT_MSG + _VIDEO_MSG + _PHOTO_MSG + _SERVICE_MSG
    + "</body></html>"
)


def test_parse_channel_html_items() -> None:
    items = parse_channel_html(HTML, channel="durov", label="Pavel Durov")
    assert [it.id for it in items] == ["durov/531", "durov/532", "durov/533"]  # 服务消息跳过

    a = items[0]
    assert (a.source, a.url) == ("telegram", "https://t.me/durov/531")
    assert a.title.startswith("🩵 Telegram now has a Rich Text Editor")  # 无 ** 记号、标点不前挤
    assert a.meta == "5.87M 观看 · Pavel Durov"
    assert a.extra["published"] == "2026-07-16T20:45:25+00:00"
    assert a.extra["author"] == "Pavel Durov"
    # 正文保留 markdown（详情读），emoji 壳塌缩成字符
    assert "**Rich Text Editor**" in a.extra["content_md"]
    assert "🩵" in a.extra["content_md"] and "**🩵**" not in a.extra["content_md"]
    assert "[the blog](https://telegram.org/blog)" in a.extra["content_md"]


def test_parse_channel_html_media_posts() -> None:
    items = parse_channel_html(HTML, channel="durov", label="Pavel Durov")
    video = items[1]
    assert "[视频](https://t.me/durov/532)" in video.extra["content_md"]  # 视频无直链，记号指帖子页
    assert video.title.startswith("New feature demo")

    photo = items[2]
    assert "[图片](https://cdn4.telesco.pe/file/photo)" in photo.extra["content_md"]
    assert photo.title == "[图片]"  # 纯媒体帖用记号当标题
    assert photo.meta == "7.06M 观看"  # 无署名不占位


def test_parse_channel_html_all_view_label_prefix() -> None:
    items = parse_channel_html(HTML, channel="durov", label="Pavel Durov", all_view=True)
    # 聚合视图频道名走源名列 tag；署名与频道名同名时不再重复占位
    assert items[0].meta == "5.87M 观看"
    assert items[0].tag == "Pavel Durov"
    assert items[1].meta == "6.58M 观看"
    assert items[1].tag == "Pavel Durov"


def test_parse_channel_html_empty_and_title() -> None:
    assert parse_channel_html("<html></html>", channel="x", label="x") == []
    assert channel_title(HTML) == "Pavel Durov"
    assert channel_title("<title>Telegram: @x</title>") == "Telegram: @x"  # og 缺了回 title
    assert channel_title("") == ""


def test_published_iso_display() -> None:
    """extra 里存的 ISO 串直排也成立（聚合流按它字典序即时间序排）。"""
    times = ["2026-07-19T21:45:01+00:00", "2026-07-16T20:45:25+00:00", "2026-07-21T14:36:55+00:00"]
    assert max(times) == "2026-07-21T14:36:55+00:00"
    assert fmt_iso("2026-07-16T20:45:25+00:00") == "2026-07-17 04:45"  # UTC -> 北京时间


def test_parse_telegram_input_forms() -> None:
    ok = {
        "durov": "durov",
        "@Durov": "durov",  # @ 前缀 + 大小写归一
        "t.me/durov": "durov",
        "https://t.me/durov": "durov",
        "https://t.me/s/durov": "durov",  # s/ 预览形态
        "https://t.me/durov/531": "durov",  # 帖子直链也认
        "telegram.me/durov": "durov",
        "durov/": "durov",  # 尾斜杠容忍
    }
    for text, want in ok.items():
        assert parse_telegram_input(text) == want, text

    for bad in ("", "  ", "du rov", "https://x.com/a", "电报频道", "a" * 33, "1abc"):
        assert parse_telegram_input(bad) == "", bad  # 数字开头不是合法 username
