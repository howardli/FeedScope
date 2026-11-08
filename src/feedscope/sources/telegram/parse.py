"""解析层：t.me/s 频道预览 HTML -> FeedItem / 正文 markdown，纯函数不碰网络。

Telegram 公开频道的 Web 预览页（t.me/s/<名>）是服务端渲染 HTML，免登录
免 JS：一页约 20 条消息，更旧一页走 ?before=<最小id>。结构映射与 RSS 同形
（纯订阅、无评论层、详情读列表自带正文零请求）：

- 每条消息（data-post="频道/编号"）-> FeedItem：正文 HTML 走 html_to_md
  共用管线（a/b/br 等行内标签全兼容），媒体（图/视频）换 [图片]/[视频] 记号
- 标题取正文首行截断（频道帖没有标题，同 X 拿推文当标题的思路）；纯媒体帖
  用记号本身当标题
- 元信息列：观看数（预览页原生就是格式化文本，如 5.87M）+ 署名（频道
  管理员签名）；「全部」聚合视图再前缀频道名

实测锚点（2026-11 对 t.me/s/durov 抓样）：消息容器 `tgme_widget_message
… js-widget_message` 带 data-post；正文 `message_text js-message_text`；
观看 `message_views`；图片是 photo_wrap 锚点的 background-image（不是
<img>，走属性提取）；视频播放器没有直链，记号指向帖子页。频道关闭 Web
预览时页面没有任何消息节点——取数侧以「解析出 0 条」报错兜底。
"""

from __future__ import annotations

import re

from feedscope.core.media import html_to_md, mark
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.text import truncate_display
from feedscope.core.timefmt import fmt_iso

SOURCE_ID = "telegram"

_SITE = "https://t.me"

# 标题截断的显示宽度：频道帖首行常常很长，列表要的是扫读不是阅读
TITLE_WIDTH = 60

# 频道名（username）域：字母开头、字母数字下划线；下限放宽到 4——校验
# 过松的代价（拉一次预览 404/无消息）由添加时的实测校验兜住
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

# 链接里的频道段：t.me/durov、t.me/s/durov、telegram.me/durov、帖子直链
# t.me/durov/531 都归一到频道名（没链接上下文时整串当裸名走 _NAME_RE）
_LINK_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)")

# 消息块切分：每个 tgme_widget_message 容器起一段（wrap 的闭合标签在下一
# 个容器之前，块内即本条消息的全部元素）；data-post 缺失（服务消息）跳过
_CHUNK_SPLIT = re.compile(r'(?=<div class="tgme_widget_message )')

_POST_RE = re.compile(r'data-post="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">\s*([^<]+?)\s*</span>')
_AUTHOR_RE = re.compile(r'tgme_widget_message_from_author[^>]*>([^<]*)<')
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_PHOTO_RE = re.compile(r'<a class="tgme_widget_message_photo_wrap[^>]*>')
_BG_URL_RE = re.compile(r"background-image:url\('([^']+)'\)")
_VIDEO_RE = re.compile(r"tgme_widget_message_video_player")
_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_OG_TITLE_RE = re.compile(r'og:title" content="([^"]*)"')

# 预览页把 emoji 包成 <tg-emoji><i class="emoji" style="…"><b>🩵</b></i></tg-emoji>
# （i/b 壳是旧客户端兜底）：整只塌缩成字符本身，否则 html2text 给每个表情
# 套一层 **强调**，正文满屏星号
_TG_EMOJI_RE = re.compile(
    r'<tg-emoji[^>]*>(?:<i class="emoji"[^>]*>)?(?:<b>(.*?)</b>)?(?:</i>)?([^<]*)</tg-emoji>'
)


def channel_title(html: str) -> str:
    """预览页的频道显示名（og:title 优先，缺了回 <title> 的尾巴）。"""
    m = _OG_TITLE_RE.search(html) or _TITLE_RE.search(html)
    if not m:
        return ""
    title = m.group(1).strip()
    return re.sub(r"\s*\(via @[\w]+\)$", "", title)  # 部分频道尾巴带 "(via @x)"，去掉归 channel


def _media_marks(chunk: str, post_url: str) -> list[str]:
    """消息块里的媒体 -> 记号列表：图片取原图直链，视频没有直链指向帖子页。"""
    marks: list[str] = []
    if (m := _PHOTO_RE.search(chunk)) and (u := _BG_URL_RE.search(m.group(0))):
        marks.append(mark(t("media.image"), u.group(1)))
    if _VIDEO_RE.search(chunk):
        marks.append(mark(t("media.video"), post_url))
    return marks


def _strip_emoji(text_html: str) -> str:
    """tg-emoji 三层壳塌缩成字符本身（否则 html2text 给每个表情套 **强调**）。"""
    return _TG_EMOJI_RE.sub(lambda m: m.group(1) or m.group(2) or "", text_html)


def _plain_text(text_html: str) -> str:
    """已剥 emoji 壳的正文 HTML -> 纯文本（标题用）：去标签 + 合并空白。"""
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text_html)).strip()
    return re.sub(r"\s+([,.!?;:，。！？；：])", r"\1", plain)  # 标签位换成的空格别挤到标点前


def _content_md(text_html: str, marks: list[str]) -> str:
    """正文：已剥 emoji 壳的行内 HTML -> markdown + 媒体记号缀尾（与 X/RSS 同观感）。"""
    blocks: list[str] = []
    if md := html_to_md(text_html):
        blocks.append(md)
    blocks.extend(marks)
    return "\n\n".join(blocks)


def _title_of(text_html: str, marks: list[str]) -> str:
    """标题 = 正文首行纯文本按显示宽度截断（标题列不要 markdown 记号）；
    纯媒体帖用记号的标签文字（如「[图片]」），不带链接尾巴。"""
    if text := _plain_text(text_html):
        return truncate_display(text, TITLE_WIDTH)
    labels = [
        m[: m.index("]") + 1] if m.startswith("[") and "]" in m else m for m in marks
    ]
    return " ".join(labels) if labels else ""


def parse_channel_html(
    html: str, *, channel: str, label: str, all_view: bool = False
) -> list[FeedItem]:
    """一页预览 HTML -> FeedItem 列表（时间正序，调用方自行倒排/合并）。

    all_view=True 是「全部」聚合视图：频道名走源名列 tag（单频道流里频道名
    已在侧栏，不重复占位）——拼进元信息列会让右对齐列左缘参差。
    """
    items: list[FeedItem] = []
    for chunk in _CHUNK_SPLIT.split(html):
        post = _POST_RE.search(chunk)
        if post is None:
            continue  # 服务消息（无 data-post）：没有正文，跳条
        post_id = post.group(1)
        url = f"{_SITE}/{post_id}"
        marks = _media_marks(chunk, url)
        text_html = _strip_emoji(m.group(1)) if (m := _TEXT_RE.search(chunk)) else ""
        md = _content_md(text_html, marks)
        title = _title_of(text_html, marks)
        if not title:
            continue  # 既无正文也无媒体：不可展示，跳条不跳流
        views = (m.group(1) if (m := _VIEWS_RE.search(chunk)) else "").strip()
        author = (m.group(1) if (m := _AUTHOR_RE.search(chunk)) else "").strip()
        parts = [t("words.views", n=views)] if views else []
        # 聚合视图频道名在源名列：署名与频道同名时不再重复占位；单频道流
        # 频道名只在侧栏，署名照常显示
        if author and author != (label if all_view else ""):
            parts.append(author)
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=post_id,
                title=title,
                url=url,
                meta=" · ".join(parts),
                tag=label if all_view else "",
                extra={
                    "content_md": md,
                    "published": m.group(1) if (m := _TIME_RE.search(chunk)) else "",
                    "author": author,
                    "channel": label,
                },
            )
        )
    return items


def parse_telegram_input(text: str) -> str:
    """输入归一成裸频道名（小写）：durov / @durov / t.me/durov / 帖子直链都认。

    认不出回空串（调用方给「没认出」反馈）。
    """
    s = (text or "").strip()
    if not s:
        return ""
    s = s.removeprefix("@")
    if m := _LINK_RE.search(s):
        name = m.group(1)
    else:
        name = s.rstrip("/")  # 裸名形态：整个串（去尾斜杠）就是频道名
    return name.lower() if _NAME_RE.match(name) else ""


def display_time(published: str) -> str:
    """extra 里存的 ISO 串 -> 展示文本（详情层时间行用）。"""
    return fmt_iso(published or None)
