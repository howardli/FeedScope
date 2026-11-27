"""Telegram 源测试夹具：t.me/s 频道预览页 HTML（由四类消息块拼成）。"""

from __future__ import annotations


def msg(body: str, post: str) -> str:
    """一条消息的容器骨架：wrap > widget（data-post）> body。"""
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text js-widget_message" data-post="{post}">'
        f"{body}"
        "</div></div>"
    )


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


SERVICE_MSG = '<div class="tgme_widget_message_service">Channel created</div>'


HTML = (
    '<html><head><meta property="og:title" content="Pavel Durov"/></head><body>'
    + TEXT_MSG + VIDEO_MSG + PHOTO_MSG + SERVICE_MSG
    + "</body></html>"
)
