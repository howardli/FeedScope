"""Telegram 解析层测试：t.me/s 预览 HTML -> FeedItem / 正文 markdown。

fixture 是 t.me/s 线上页面的最小裁剪（容器类名、data-post、tg-emoji 的
i/b 壳、photo_wrap 的 background-image、views 的格式化文本都照抄实测），
覆盖：文字/图片/视频三类帖、emoji 壳塌缩、纯媒体帖标题、服务消息跳过、
输入归一四形态。
"""

from __future__ import annotations

from feedscope.core.models import FeedItem
from feedscope.core.timefmt import fmt_iso
from feedscope.sources.telegram.parse import (
    parse_channel_html,
    parse_channel_title,
    parse_telegram_input,
)
from tests.sources.telegram.fixtures import HTML, msg

TEXT_MSG = msg(
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

VIDEO_MSG = msg(
    '<a class="tgme_widget_message_video_player js-message_video_player" href="https://t.me/durov/532">'
    '<i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn4.telesco.pe/file/thumb\')"></i></a>'
    '<div class="tgme_widget_message_text js-message_text" dir="auto">New feature demo.</div>'
    '<span class="tgme_widget_message_views">6.58M</span>'
    '<time datetime="2026-07-19T21:45:01+00:00"></time>',
    "durov/532",
)

PHOTO_MSG = msg(
    '<a class="tgme_widget_message_photo_wrap 123" href="https://t.me/durov/533" '
    "style=\"width:800px;background-image:url('https://cdn4.telesco.pe/file/photo')\">"
    "</a>"
    '<span class="tgme_widget_message_views">7.06M</span>'
    '<time datetime="2026-07-21T14:36:55+00:00"></time>',
    "durov/533",
)

# 服务消息（无 data-post）：没有正文，应整条跳过
SERVICE_MSG = '<div class="tgme_widget_message_service">Channel created</div>'


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
    assert parse_channel_title(HTML) == "Pavel Durov"
    assert parse_channel_title("<title>Telegram: @x</title>") == "Telegram: @x"  # og 缺了回 title
    assert parse_channel_title("") == ""


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


# ---- 线上真实结构的裁剪（2026-11 对 t.me/s/durov、toncoin、bbcrussian 抓样）----
# 容器 / 类名 / 嵌套照抄页面，只裁掉版式属性与无关内容。投票见文末：那是
# 2026-11-25 另抓的两种形态（预览页 / 单帖嵌入页），同样原样裁剪


def post(post: str, *inner: str, cls: str = "text") -> str:
    """一条消息的最小容器（开头与线上同形），inner 是气泡里的内容块。

    cls 是消息的类型类名：带文字的帖子是 text，投票这类文字以外的消息是
    text_not_supported_wrap。"""
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message {cls} js-widget_message" data-post="{post}">'
        + "".join(inner)
        + "</div></div>"
    )


def _owner(name: str) -> str:
    return (
        '<div class="tgme_widget_message_author accent_color">'
        '<a class="tgme_widget_message_owner_name" href="https://t.me/durov">'
        f'<span dir="auto">{name}</span></a></div>'
    )


def _text(body: str) -> str:
    return (
        '<div class="media_supported_cont"><div class="tgme_widget_message_text '
        f'js-message_text" dir="auto">{body}</div></div>'
    )


def _footer(views: str, iso: str, author: str = "", voters: str = "") -> str:
    signature = (
        '<span class="tgme_widget_message_meta"><span class="tgme_widget_message_from_author" '
        f'dir="auto">{author}</span>,</span>'
        if author
        else ""
    )
    tally = f'<span class="tgme_widget_message_voters">{voters}</span><span class="copyonly"> voters</span>' if voters else ""
    return (
        '<div class="tgme_widget_message_footer compact js-message_footer">'
        '<div class="tgme_widget_message_info short js-message_info">'
        f"{tally}"
        f'<span class="tgme_widget_message_views">{views}</span>'
        f'<span class="copyonly"> views</span>{signature}'
        f'<time datetime="{iso}" class="time">Jul 17</time>'
        "</div></div>"
    )


def _one(html: str) -> FeedItem:
    (item,) = parse_channel_html(html, channel="durov", label="Pavel Durov")
    return item


_REPLY_POST = post(
    "durov/558",
    _owner("Pavel Durov"),
    '<a class="tgme_widget_message_reply user-color-12" href="https://t.me/durov/557">'
    '<div class="tgme_widget_message_author accent_color">'
    '<span class="tgme_widget_message_author_name" dir="auto">Pavel Durov</span></div>'
    '<div class="tgme_widget_message_text js-message_reply_text" dir="auto">'
    "Unfair that CEOs who aren&#8217;t into Asian girls&#8230;</div></a>",
    _text("<b>CORRECTION:</b> Spy agencies are trying after all."),
    _footer("1.1K", "2026-07-20T10:00:00+00:00", "Pavel Durov"),
)


def test_reply_post_shows_its_own_text_and_quotes_the_replied_one() -> None:
    """回归：回复引用里的被引文字与帖子正文同用 tgme_widget_message_text 类名——
    以前取第一个匹配，带回复的帖子的标题和正文全是被回复那条的内容。"""
    item = _one(_REPLY_POST)
    assert item.id == "durov/558"
    assert item.title.startswith("CORRECTION: Spy agencies are trying")
    body = item.extra["content_md"]
    assert body.startswith("> 回复 **Pavel Durov**：Unfair that CEOs who aren’t into Asian girls…")
    assert body.endswith("**CORRECTION:** Spy agencies are trying after all.")
    assert item.extra["author"] == "Pavel Durov", "署名不受回复引用里的作者名影响"


def test_reply_without_author_still_quotes_the_text() -> None:
    html = _REPLY_POST.replace(
        '<span class="tgme_widget_message_author_name" dir="auto">Pavel Durov</span>',
        '<span class="tgme_widget_message_author_name" dir="auto"></span>',
    )
    assert _one(html).extra["content_md"].startswith("> 回复一条消息：Unfair that CEOs")


_A, _B = "https://cdn4.telesco.pe/file/AAA", "https://cdn4.telesco.pe/file/BBB"


def _photo(url: str, grouped: bool) -> str:
    extra = " grouped_media_wrap blured js-message_photo" if grouped else ""
    return (
        f'<a class="tgme_widget_message_photo_wrap{extra}" '
        'href="https://t.me/durov/510?embed=1&amp;mode=tme" '
        f'style="width:226px;height:311px;background-image:url(\'{url}\')"></a>'
    )


_ALBUM_MEDIA = (
    '<div class="media_supported_cont"><div class="tgme_widget_message_one_media js-message_media">'
    '<div class="media_supported_cont"><div class="tgme_widget_message_grouped_wrap '
    'js-message_grouped_wrap" data-margin-left="13.7306">'
    '<div class="tgme_widget_message_grouped js-message_grouped" style="padding-top:68.653%">'
    '<div class="tgme_widget_message_grouped_layer js-message_grouped_layer" '
    'style="width:453px;height:311px">'
    + _photo(_A, grouped=True)
    + _photo(_B, grouped=True)
    + '<a class="tgme_widget_message_video_player grouped_media_wrap js-message_video_player" '
      'href="https://t.me/durov/510"><i class="tgme_widget_message_video_thumb"></i></a>'
    "</div></div></div></div></div></div>"
)
_ALBUM_CAPTION = _text("🌍 With 400 validators across 6 continents, TON is one of the most decentralized…")
_ALBUM_FOOTER = _footer("3.91M", "2026-07-14T20:45:25+00:00", "Pavel Durov")


def test_album_keeps_every_photo_and_video_of_the_group() -> None:
    """回归：相册是一条消息里的多个媒体锚点——以前每条消息只取第一个，
    9 张图的相册只剩 1 个记号。"""
    body = _one(post("durov/510", _owner("Pavel Durov"), _ALBUM_MEDIA, _ALBUM_CAPTION, _ALBUM_FOOTER)).extra[
        "content_md"
    ]
    assert (body.count("[图片]"), body.count("[视频]")) == (2, 1)
    video = "[视频](https://t.me/durov/510)"
    assert body.index(_A) < body.index(_B) < body.index(video), "按页面顺序"
    assert body.startswith("🌍 With 400 validators"), "说明文字在前，记号缀尾（与单图帖同观感）"


_VIDEO = (
    '<a class="tgme_widget_message_video_player grouped_media_wrap js-message_video_player" '
    'href="https://t.me/durov/373"><i class="tgme_widget_message_video_thumb"></i></a>'
)


def test_videos_of_an_album_collapse_into_one_counted_mark() -> None:
    """视频记号都指向同一个帖子页：9 个视频的相册若各出一行，就是 9 行一模一样的链接。"""
    # 两张图换成两个视频：加上夹具原有的那个，一共 3 个视频
    media = _ALBUM_MEDIA.replace(_photo(_A, grouped=True) + _photo(_B, grouped=True), _VIDEO * 2)
    item = _one(post("durov/373", media, _ALBUM_FOOTER))
    assert item.extra["content_md"] == "[视频 3](https://t.me/durov/373)"
    assert item.title == "[视频 3]"
    single = _one(post("durov/532", _VIDEO.replace("grouped_media_wrap ", ""), _footer("6.58M", "2026-07-19T21:45:01+00:00")))
    assert single.extra["content_md"] == "[视频](https://t.me/durov/532)"


def test_album_without_caption_is_titled_by_its_media_labels() -> None:
    item = _one(post("durov/510", _ALBUM_MEDIA, _ALBUM_FOOTER))
    assert item.title == "[图片] [图片] [视频]"
    assert item.meta == "3.91M 观看 · Pavel Durov"


def test_forwarded_posts_say_where_they_come_from() -> None:
    """转发来源是正文之外的一行，以前整个丢掉。来源名与原帖署名都可能为空。"""
    line = (
        '<div class="tgme_widget_message_forwarded_from accent_color">Forwarded from&nbsp;'
        '<span class="tgme_widget_message_forwarded_from_name"><span dir="auto">{}</span></span>{}'
        "</div>"
    )
    signed = '&nbsp;(<span class="tgme_widget_message_forwarded_from_author" dir="auto">Pavel Durov</span>)'
    tail = (_text("The TON blockchain just got upgraded."), _footer("1.0K", "2026-07-18T09:00:00+00:00"))

    def forwarded(name: str, extra: str) -> str:
        return _one(post("gram/2372", line.format(name, extra), *tail)).extra["content_md"]

    assert forwarded("TON Community", signed).startswith("> 转发自 TON Community（Pavel Durov）")
    assert forwarded("", signed).startswith("> 转发自 Pavel Durov"), "来源隐藏、只剩署名"
    assert forwarded("TON Community", "").startswith("> 转发自 TON Community")
    assert forwarded("", "").startswith("> 转发消息"), "什么都隐藏：至少说明这是转发"
    assert forwarded("TON Community", "").endswith("The TON blockchain just got upgraded.")


_AUDIO_POST = post(
    "bbcrussian/101783",
    '<a class="tgme_widget_message_document_wrap" href="https://t.me/bbcrussian/101783">'
    '<div class="tgme_widget_message_document_icon accent_bg audio"></div>'
    '<div class="tgme_widget_message_document">'
    '<div class="tgme_widget_message_document_title accent_color" dir="auto">Пodcast title</div>'
    '<div class="tgme_widget_message_document_extra" dir="auto">Русская служба · 42MB</div>'
    "</div></a>",
    _footer("28.6K", "2026-11-24T12:40:51+00:00"),
)


def test_file_and_audio_posts_are_shown_with_their_title() -> None:
    """文件 / 音频以前整个丢掉：只有文件的帖子直接从列表里消失。"""
    body = _one(_AUDIO_POST).extra["content_md"]
    assert body == "[音频](https://t.me/bbcrussian/101783) Пodcast title（Русская служба · 42MB）"
    document = _one(_AUDIO_POST.replace("accent_bg audio", "accent_bg file"))
    assert document.extra["content_md"].startswith("[文件](https://t.me/bbcrussian/101783) Пodcast title")
    assert document.title == "Пodcast title", "没有文字的帖子用文件标题当标题"


_LINK_PREVIEW_POST = post(
    "bbcrussian/101778",
    _text('Read this: <a href="https://bbc.in/4tm4C5V">bbc.in/4tm4C5V</a>'),
    '<a class="tgme_widget_message_link_preview" href="https://bbc.in/4tm4C5V">'
    '<div class="link_preview_site_name accent_color" dir="auto">BBC News</div>'
    '<i class="link_preview_image"></i>'
    '<div class="link_preview_title" dir="auto">A long-list was announced</div>'
    '<div class="link_preview_description" dir="auto">Five novels made the short-list&#8230;</div>'
    "</a>",
    _footer("28.6K", "2026-11-24T12:40:51+00:00"),
)


def test_link_preview_card_is_rendered_after_the_text() -> None:
    body = _one(_LINK_PREVIEW_POST).extra["content_md"]
    assert body.endswith(
        "> **BBC News**\n>\n> A long-list was announced\n>\n> Five novels made the short-list…"
    )
    assert body.index("bbc.in/4tm4C5V") < body.index("**BBC News**")


def test_titles_decode_html_entities() -> None:
    """回归：标题去标签后没还原实体，列表里直接露出 &#036;100,400（正文是对的）。"""
    item = _one(post("durov/513", _text("<b>&#036;100,400</b> just went to the winners&#8230;")))
    assert item.title.startswith("$100,400 just went to the winners")


# ---- 投票：两种真实形态（2026-11-25 抓自 t.me/s/javascript 及其单帖嵌入页）----
# 预览页（已出结果）：选项类名 tgme_widget_message_poll_option，先给百分比再给
# 文案，整组选项包在指向帖子页的 <a> 里，票数在页脚的 voters span。嵌入页（未
# 投票）：选项另带 js-poll_option + data-value，没有百分比，票数在块里的 _poll_votes。
# 以前这里是一份按站点脚本钩子拼出来的夹具，真实页面上一个问题都取不到。


def _option(text: str, percent: str = "") -> str:
    pct = f'<div class="tgme_widget_message_poll_option_percent">{percent}</div>' if percent else ""
    return (
        '<div class="tgme_widget_message_poll_option">'
        f"{pct}"
        '<div class="tgme_widget_message_poll_option_value">'
        f'<div class="tgme_widget_message_poll_option_text">{text}</div>'
        '<div class="tgme_widget_message_poll_option_bar accent_bg" style="width:47.619%"></div>'
        "</div></div>"
    )


def _poll(question: str, kind: str, *options: str) -> str:
    return (
        '<div class="tgme_widget_message_poll js-poll">'
        f'<div class="tgme_widget_message_poll_question">{question}</div>'
        f'<div class="tgme_widget_message_poll_type">{kind}</div>'
        '<a class="tgme_widget_message_poll_options" href="https://t.me/javascript/3603">'
        + "".join(options)
        + "</a></div>"
    )


def _poll_post(poll_html: str, voters: str = "") -> str:
    """投票帖：投票块是气泡里作者行之后的兄弟节点（不在文字块里）。"""
    return post(
        "javascript/3603",
        _owner("JavaScript"),
        poll_html,
        _footer("1.64K", "2026-11-03T06:33:24+00:00", voters=voters),
        cls="text_not_supported_wrap",
    )


_LINK = "[投票](https://t.me/javascript/3603)"

_QUIZ = (  # 线上原样（t.me/s/javascript/3603）：只把相邻标签之间的空白换成换行
    '<div class="tgme_widget_message_poll js-poll">'
    '<div class="tgme_widget_message_poll_question">What is the output?</div>'
    '<div class="tgme_widget_message_poll_type">Anonymous Quiz</div>'
    '<a class="tgme_widget_message_poll_options" href="https://t.me/javascript/3603">'
    '<div class="tgme_widget_message_poll_option">'
    '<div class="tgme_widget_message_poll_option_percent">20%</div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">NaN 2</div>'
    '<div class="tgme_widget_message_poll_option_bar accent_bg" style="width:47.619%"></div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option">'
    '<div class="tgme_widget_message_poll_option_percent">42%</div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">6 3</div>'
    '<div class="tgme_widget_message_poll_option_bar accent_bg" style="width:100%"></div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option">'
    '<div class="tgme_widget_message_poll_option_percent">30%</div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">6 2</div>'
    '<div class="tgme_widget_message_poll_option_bar accent_bg" style="width:71.4286%"></div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option">'
    '<div class="tgme_widget_message_poll_option_percent">8%</div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">7 2</div>'
    '<div class="tgme_widget_message_poll_option_bar accent_bg" style="width:19.0476%"></div>'
    "</div>"
    "</div></a>"
    "</div>"
)

_UNVOTED = (  # 线上原样（同一帖子的嵌入页 ?embed=1：未投票的形态）
    '<div class="tgme_widget_message_poll js-poll">'
    '<div class="tgme_widget_message_poll_question">What is the output?</div>'
    '<div class="tgme_widget_message_poll_type">Anonymous Quiz</div>'
    '<div class="tgme_widget_message_poll_options">'
    '<div class="tgme_widget_message_poll_option '
    'tgme_widget_message_poll_option_radio_item js-poll_option" data-value="MA">'
    '<div class="tgme_widget_message_poll_option_radio"></div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">NaN 2</div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option '
    'tgme_widget_message_poll_option_radio_item js-poll_option" data-value="MQ">'
    '<div class="tgme_widget_message_poll_option_radio"></div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">6 3</div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option '
    'tgme_widget_message_poll_option_radio_item js-poll_option" data-value="Mg">'
    '<div class="tgme_widget_message_poll_option_radio"></div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">6 2</div>'
    "</div>"
    "</div>"
    '<div class="tgme_widget_message_poll_option '
    'tgme_widget_message_poll_option_radio_item js-poll_option" data-value="Mw">'
    '<div class="tgme_widget_message_poll_option_radio"></div>'
    '<div class="tgme_widget_message_poll_option_value">'
    '<div class="tgme_widget_message_poll_option_text">7 2</div>'
    "</div>"
    "</div></div>"
    '<div class="tgme_widget_message_poll_votes">100 answered</div>'
    "</div>"
)


def test_real_quiz_poll_renders_question_options_and_tally() -> None:
    """回归：以前只认脚本钩子 js-poll_option，真实页面的选项类名是
    tgme_widget_message_poll_option——投票帖标题清一色是 [投票]，问题和选项全丢。"""
    item = _one(_poll_post(_QUIZ, voters="100"))
    assert item.title == "What is the output?", "投票没有文字块：问题就是标题"
    assert item.extra["content_md"] == (
        "**What is the output?**\n\n"
        "- NaN 2 — 20%\n- 6 3 — 42%\n- 6 2 — 30%\n- 7 2 — 8%\n\n"
        "Anonymous Quiz · 100 人投票\n\n"
        + _LINK  # 终端里只能看不能投：末尾留去投的入口
    )


def test_real_unvoted_poll_lists_options_without_percentages() -> None:
    body = _one(_poll_post(_UNVOTED)).extra["content_md"]
    assert body == (
        "**What is the output?**\n\n- NaN 2\n- 6 3\n- 6 2\n- 7 2\n\n"
        "Anonymous Quiz · 100 answered\n\n"  # 嵌入页的票数是整句：页面给什么显示什么
        + _LINK
    )


def test_plain_poll_names_its_type_and_voter_count() -> None:
    poll = _poll("Which feature next?", "Anonymous Poll", _option("Option A", "45%"), _option("Option B", "55%"))
    item = _one(_poll_post(poll, voters="609"))
    assert item.title == "Which feature next?"
    assert item.extra["content_md"] == (
        "**Which feature next?**\n\n- Option A — 45%\n- Option B — 55%\n\n"
        "Anonymous Poll · 609 人投票\n\n"
        + _LINK
    )


def test_percent_comes_from_its_own_element_not_from_the_option_text() -> None:
    """票数只认选项自己的 _option_percent：文案里带「5%」不会被当成结果，
    带了百分比元素时也照常并排给出。"""
    poll = _poll("Budget?", "Poll", _option("Raise it by 5%", "45%"), _option("Keep it"))
    body = _one(_poll_post(poll, voters="12")).extra["content_md"]
    assert "- Raise it by 5% — 45%\n- Keep it\n\nPoll · 12 人投票" in body


def test_poll_without_type_or_tally_has_no_summary_line() -> None:
    bare = '<div class="tgme_widget_message_poll js-poll">' + _option("Yes", "100%") + "</div>"
    body = _one(_poll_post(bare)).extra["content_md"]
    assert body == "- Yes — 100%\n\n" + _LINK


def test_poll_text_is_plain_and_decoded() -> None:
    """问题和选项走 _text_of：实体还原、自定义表情塌缩成字符（否则满屏星号）。"""
    option = _option('<b>A</b> &amp; <tg-emoji emoji-id="1"><i class="emoji" style="x"><b>👍</b></i></tg-emoji>')
    poll = _poll("Which&nbsp;<i>one</i>?", "Poll", option)
    body = _one(_poll_post(poll)).extra["content_md"]
    assert body == "**Which one?**\n\n- A & 👍\n\nPoll\n\n" + _LINK


def test_unrecognized_poll_markup_keeps_the_post_as_a_link() -> None:
    """问题和选项都认不出（站点改了版式）：不把投票块里的东西当正文，退回一个
    指向帖子的记号——帖子不至于整个消失；认得出问题时至少拿它当标题。"""
    item = _one(_poll_post('<div class="tgme_widget_message_poll js-poll">???</div>'))
    assert item.title == "[投票]"
    assert item.extra["content_md"] == _LINK
    only_question = _one(
        _poll_post(
            '<div class="tgme_widget_message_poll js-poll">'
            '<div class="tgme_widget_message_poll_question">Q?</div></div>'
        )
    )
    assert only_question.title == "Q?"
    assert only_question.extra["content_md"] == "**Q?**\n\n" + _LINK


# ---- 普通表情（2026-11-25 抓自 t.me/s/iosdev/2143、golang、cricket）----
# 页面上 emoji 是 <i class="emoji"><b>📖</b></i>，自定义表情外面再套一层 <tg-emoji>。
# 抓的 59 页里无外壳的普通表情 2108 处、带外壳的 442 处——以前只塌缩带外壳的那种，
# 正文里每个普通表情都被 html2text 套成 _**📖**_


def _emoji(char: str, codepoint: str = "F09F9396") -> str:
    return (
        "<i class=\"emoji\" style=\"background-image:url('//telegram.org/img/emoji/40/"
        f"{codepoint}.png')\"><b>{char}</b></i>"
    )


def test_ordinary_emoji_in_the_body_lose_their_emphasis_markers() -> None:
    """回归：普通表情不带 tg-emoji 外壳，以前漏塌缩——正文里是 _**📖**_，
    嵌在加粗标题里时连强调标记都乱了。"""
    body = _text(
        f"<b>{_emoji('⚡️', 'E29AA1')} Заголовок</b><br/><br/>"
        f"{_emoji('📖')} "
        '<a href="https://developer.apple.com/news/?id=kkphp5qo">Спецификации</a>.'
    )
    item = _one(post("iosdev/2143", body))
    assert item.extra["content_md"] == (
        "**⚡️ Заголовок**  \n  \n📖 [Спецификации](https://developer.apple.com/news/?id=kkphp5qo)."
    )
    assert item.title == "⚡️ Заголовок 📖 Спецификации."  # 标题那条路本来就去标签：不变


def test_custom_emoji_still_collapse_inside_their_tg_emoji_wrapper() -> None:
    body = _text(f'<tg-emoji emoji-id="5246793797538425300">{_emoji("😃", "F09F9883")}</tg-emoji> <b>hi</b>')
    assert _one(post("iosdev/2143", body)).extra["content_md"] == "😃 **hi**"


def test_emoji_inside_nested_emphasis_keeps_the_nesting() -> None:
    """回归：加粗里套斜体、里面再一个表情壳，以前渲染成 **…_**🔥**__**。"""
    body = _text("<i>Lay Rajasthan...</i><br/><b><i>Gujarat Plus Kro..." + _emoji("🔥", "F09F94A5") + "</i></b>")
    assert _one(post("cricket/8956", body)).extra["content_md"] == (
        "_Lay Rajasthan..._  \n**_Gujarat Plus Kro...🔥_**"
    )


def test_adjacent_emoji_are_joined_in_the_body_and_spaced_in_the_title() -> None:
    """两个表情元素之间源码里没有空白：正文照原帖连着写；标题那条路把标签换成
    空格（一直如此），列表标题因此不变。"""
    item = _one(post("golang/1122", _text(_emoji("😀", "F09F9880") + _emoji("🎉", "F09F8E89") + " party")))
    assert item.extra["content_md"] == "😀🎉 party"
    assert item.title == "😀 🎉 party"


def test_reply_variants_and_empty_posts() -> None:
    """引用块的两个边角与空帖：只有作者没有文字、两者皆空；既无文字也无媒体的帖子跳条不跳流。"""
    own = _text("<b>CORRECTION:</b> Spy agencies are trying after all.")
    author = (
        '<div class="tgme_widget_message_author accent_color">'
        '<span class="tgme_widget_message_author_name" dir="auto">Pavel Durov</span></div>'
    )

    def reply(*inner: str) -> str:
        return '<a class="tgme_widget_message_reply user-color-12" href="https://t.me/durov/557">' + "".join(
            inner
        ) + "</a>"

    assert _one(post("durov/558", reply(author), own)).extra["content_md"] == (
        "> 回复 Pavel Durov\n\n**CORRECTION:** Spy agencies are trying after all."
    )
    assert _one(post("durov/558", reply(), own)).extra["content_md"] == (
        "**CORRECTION:** Spy agencies are trying after all."
    )
    empty = post("durov/1", _footer("1", "2026-07-17T09:00:00+00:00"))
    assert parse_channel_html(empty, channel="durov", label="x") == []
