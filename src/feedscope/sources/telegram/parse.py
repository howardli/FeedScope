"""解析层：t.me/s 频道预览 HTML -> FeedItem / 正文 markdown，纯函数不碰网络。

Telegram 公开频道的 Web 预览页（t.me/s/<名>）是服务端渲染 HTML，免登录
免 JS：一页约 20 条消息，更旧一页走 ?before=<最小id>。结构映射与 RSS 同形
（纯订阅、无评论层、详情读列表自带正文零请求）：

- 每条消息（data-post="频道/编号"）-> FeedItem。一条消息里的内容块按
  Telegram 自己的展示顺序进正文：转发来源 → 回复引用 → 文字 → 投票 →
  媒体记号 → 链接预览。文字 HTML 走 html_to_md 共用管线（a/b/br 等行内
  标签全兼容），图 / 视频 / 文件换成记号链接
- 标题取正文首行截断（频道帖没有标题，同 X 拿推文当标题的思路）；没有文字
  时依次退到投票的问题、文件的标题，都没有才用媒体记号本身
- 元信息列：观看数（预览页原生就是格式化文本，如 5.87M）+ 署名（频道
  管理员签名）；「全部」聚合视图再前缀频道名

实测锚点（2026-11 对 t.me/s/durov、telegram、toncoin、bbcrussian 等抓样）：
消息容器 `tgme_widget_message … js-widget_message` 带 data-post；观看
`message_views`；图片是 photo_wrap 锚点的 background-image（不是 <img>，
走属性提取）。
- 正文 `tgme_widget_message_text js-message_text`。回复引用块
  （`tgme_widget_message_reply`）里还有一块同类名的文字（js-message_reply_text）：
  取正文必须按 js- 名排除它，否则带回复的帖子取到的是被回复那条的内容
- 相册是一条消息（一个 data-post）里的多个媒体锚点
  （`photo_wrap grouped_media_wrap` / `video_player grouped_media_wrap`）：
  每个各出一个记号
- 转发来源 `tgme_widget_message_forwarded_from`：来源名在 _from_name（可以为
  空：来源被隐藏 / 从个人转发），原帖署名在 _from_author
- 文件 / 音频 `tgme_widget_message_document_wrap`：链接指向帖子页，标题与
  附注在 _document_title / _document_extra
- 链接预览 `tgme_widget_message_link_preview`：站点名 / 标题 / 摘要
- 投票（2026-11 对 t.me/s/javascript、t.me/s/iosdev 抓样，单帖嵌入页对照未投票
  形态）：容器按站点脚本（widget-frame.js）自己用的钩子 `js-poll` 认，内部按
  类名取问题 / 类型 / 选项。已出结果时每个选项先给百分比再给文案，整组选项
  包在指向帖子页的 <a> 里，票数在页脚的 voters；未投票的形态选项另带
  `js-poll_option`、没有百分比
- 视频没有稳定直链：页面里的 <video src> 带 token 参数（时效未知），记号
  指向帖子页

频道关闭 Web 预览时页面没有任何消息节点——取数侧以「解析出 0 条」报错兜底。
"""

from __future__ import annotations

import html as html_mod
import re

from feedscope.core.i18n import t
from feedscope.core.media import html_to_md, mark
from feedscope.core.models import FeedItem
from feedscope.core.text import truncate_display
from feedscope.core.timefmt import iso_epoch

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

# 文字块：类名 tgme_widget_message_text。回复引用里的被引文字同用这个类名
# （只多一个 js-message_reply_text）——_main_text 按 js- 名把它排除
_TEXT_RE = re.compile(
    r'<div class="(tgme_widget_message_text[^"]*)"[^>]*>(.*?)</div>', re.DOTALL
)
_VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">\s*([^<]+?)\s*</span>')
_AUTHOR_RE = re.compile(r'tgme_widget_message_from_author[^>]*>([^<]*)<')
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')

# 媒体锚点（单图 / 单视频与相册里的同一套，按页面顺序逐个出记号）：
# 图片地址在 background-image，视频没有稳定直链（见模块头）
_MEDIA_RE = re.compile(r'<a class="tgme_widget_message_(photo_wrap|video_player)[^"]*"[^>]*>')
_BG_URL_RE = re.compile(r"background-image:url\('([^']+)'\)")
_HREF_RE = re.compile(r'href="([^"]*)"')

# 回复引用块（帖子正文之前的一个 <a>，内含被引作者与被引文字；没有嵌套 <a>）
_REPLY_RE = re.compile(r'<a class="tgme_widget_message_reply(?:\s[^"]*)?"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_REPLY_AUTHOR_RE = re.compile(r"tgme_widget_message_author_name[^>]*>(.*?)</span>", re.DOTALL)
_REPLY_TEXT_RE = re.compile(r'js-message_reply_text"[^>]*>(.*?)</div>', re.DOTALL)

# 转发来源行：字面「Forwarded from」+ 来源名（可空）+ 可选的原帖署名
_FWD_RE = re.compile(
    r'<div class="tgme_widget_message_forwarded_from[^"]*">(.*?)</div>', re.DOTALL
)
_FWD_NAME_RE = re.compile(
    r"tgme_widget_message_forwarded_from_name[^>]*>(.*?)"
    r"(?=tgme_widget_message_forwarded_from_author|$)",
    re.DOTALL,
)
_FWD_AUTHOR_RE = re.compile(
    r"tgme_widget_message_forwarded_from_author[^>]*>(.*?)</span>", re.DOTALL
)
# 来源名里的内层元素（<span dir="auto">名</span>，也可能是链接）：取它的内容
_INNER_EL_RE = re.compile(r"<(?:span|a)\b[^>]*>(.*?)</(?:span|a)>", re.DOTALL)

# 文件 / 音频：一个 <a>（链接指向帖子页）里是图标 + 标题 + 附注
_DOC_RE = re.compile(
    r'<a class="tgme_widget_message_document_wrap[^"]*"[^>]*>(.*?)</a>', re.DOTALL
)
_DOC_TITLE_RE = re.compile(
    r"tgme_widget_message_document_title[^>]*>(.*?)</div>", re.DOTALL
)
_DOC_EXTRA_RE = re.compile(r"tgme_widget_message_document_extra[^>]*>(.*?)</div>", re.DOTALL)
_DOC_AUDIO_RE = re.compile(r'tgme_widget_message_document_icon[^"]*\baudio\b')

# 链接预览卡（文字 / 媒体之后的一个 <a>）
_PREVIEW_RE = re.compile(r'<a class="tgme_widget_message_link_preview[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_PREVIEW_SITE_RE = re.compile(r'<div class="link_preview_site_name[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_PREVIEW_TITLE_RE = re.compile(r'<div class="link_preview_title"[^>]*>(.*?)</div>', re.DOTALL)
_PREVIEW_DESC_RE = re.compile(r'<div class="link_preview_description"[^>]*>(.*?)</div>', re.DOTALL)

# 投票：容器按站点脚本（widget-frame.js）自己用的钩子 js-poll 认，内部按类名取。
# …_poll_options（整组选项的包）与 …_poll_option_percent 等都以 …_poll_option
# 打头：选项的类名必须整词匹配（首尾不能挨着字母数字下划线连字符）
_POLL_RE = re.compile(r'<([a-z]+) class="[^"]*\bjs-poll\b[^"]*"[^>]*>')
_POLL_QUESTION_RE = re.compile(r'tgme_widget_message_poll_question"[^>]*>(.*?)</div>', re.DOTALL)
_POLL_TYPE_RE = re.compile(r'tgme_widget_message_poll_type"[^>]*>(.*?)</div>', re.DOTALL)
_POLL_OPTION_RE = re.compile(
    r'<([a-z]+) class="[^"]*(?<![\w-])(?:tgme_widget_message_poll_option|js-poll_option)'
    r'(?![\w-])[^"]*"[^>]*>'
)
_POLL_OPTION_TEXT_RE = re.compile(
    r'tgme_widget_message_poll_option_text"[^>]*>(.*?)</div>', re.DOTALL
)
_POLL_OPTION_PERCENT_RE = re.compile(
    r'tgme_widget_message_poll_option_percent"[^>]*>(.*?)</div>', re.DOTALL
)
# 票数：嵌入页在投票块里（整句，如 100 answered）；预览页在页脚（只有数字，
# 后面跟页面自己的英文 voters——文案要自己配）
_POLL_VOTES_RE = re.compile(r'tgme_widget_message_poll_votes"[^>]*>(.*?)</div>', re.DOTALL)
_VOTERS_RE = re.compile(r'<span class="tgme_widget_message_voters">\s*([^<]+?)\s*</span>')

_TITLE_RE = re.compile(r"<title>([^<]*)</title>")
_OG_TITLE_RE = re.compile(r'og:title" content="([^"]*)"')

# 预览页把 emoji 包成 <i class="emoji" style="…"><b>🙂</b></i>（i/b 壳是旧客户端
# 兜底），自定义表情外面再套一层 <tg-emoji>。正文里两种都整只塌缩成字符本身，
# 否则 html2text 给每个表情套一层 **强调**，正文满屏星号；普通表情占绝大多数
# （2026-11 抓 59 页：无外壳 2108 处、带外壳 442 处）
_EMOJI_SHELL_RE = re.compile(r'<i class="emoji"[^>]*>(?:<b>(.*?)</b>|([^<]*))</i>')
_TG_EMOJI_RE = re.compile(
    r'<tg-emoji[^>]*>(?:<i class="emoji"[^>]*>)?(?:<b>(.*?)</b>)?(?:</i>)?([^<]*)</tg-emoji>'
)


def parse_channel_title(html: str) -> str:
    """预览页的频道显示名（og:title 优先，缺了回 <title> 的尾巴）。"""
    m = _OG_TITLE_RE.search(html) or _TITLE_RE.search(html)
    if not m:
        return ""
    title = m.group(1).strip()
    return re.sub(r"\s*\(via @[\w]+\)$", "", title)  # 部分频道尾巴带 "(via @x)"，去掉归 channel


def _emoji_text(m: re.Match[str]) -> str:
    """壳里的字符：<b> 里的那个，或直接放在壳里的文本。"""
    return str(m.group(1) or m.group(2) or "")


def _strip_emoji(text_html: str) -> str:
    """自定义表情的 tg-emoji 壳（连内层的 i/b）塌缩成字符本身。"""
    return _TG_EMOJI_RE.sub(_emoji_text, text_html)


def _strip_emoji_shell(text_html: str) -> str:
    """正文用：再塌缩普通表情的 i/b 壳。

    只走正文——标题那条路（_text_of）把标签换成空格，本来就没有星号问题；
    相邻的两个表情在那里因此隔着空格，保持原样。"""
    return _EMOJI_SHELL_RE.sub(_emoji_text, text_html)


def _text_of(fragment: str) -> str:
    """HTML 片段 -> 纯文本：剥 emoji 壳、去标签、还原实体、合并空白。

    标题、引用文案、投票文案都经这里——实体必须还原（`&#036;` 是 $，
    `&nbsp;` 是空格），否则列表标题里直接露出 `&#036;100,400`。"""
    plain = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", _strip_emoji(fragment))))
    plain = plain.strip()
    return re.sub(r"\s+([,.!?;:，。！？；：])", r"\1", plain)  # 标签位换成的空格别挤到标点前


def _main_text(chunk: str) -> str:
    """帖子自己的文字块（HTML）；没有回空串。

    回复引用里的被引文字同用 tgme_widget_message_text 类名（只多一个
    js-message_reply_text）：按 js- 名跳过它，否则带回复的帖子取到的是
    被回复那条的内容。"""
    for m in _TEXT_RE.finditer(chunk):
        if "js-message_reply_text" not in m.group(1):
            return m.group(2)
    return ""


def _media_marks(chunk: str, post_url: str) -> list[str]:
    """图片 / 视频 -> 记号列表，按页面顺序。

    单图与相册里的图同一个锚点类名（photo_wrap），逐个扫过去就不用区分
    「相册」这个形态：每张图各有各的地址，各出一个记号。视频取不到稳定
    直链、记号都指向帖子页——相册里的 9 个视频若各出一行就是 9 行一模
    一样的链接，所以同一帖子的视频合并成一个带数量的记号，落在第一个
    视频的位置上。"""
    marks: list[str] = []
    videos = 0  # 本帖的视频数；0 = 还没遇到
    slot = 0  # 视频记号在 marks 里的下标（数量定了再填）
    for m in _MEDIA_RE.finditer(chunk):
        if m.group(1) == "photo_wrap":
            if u := _BG_URL_RE.search(m.group(0)):
                marks.append(mark(t("media.image"), u.group(1)))
        else:
            videos += 1
            if videos == 1:
                slot = len(marks)
                marks.append("")
    if videos:
        label = t("media.videos_n", n=videos) if videos > 1 else t("media.video")
        marks[slot] = mark(label, post_url)
    return marks


def _document_marks(chunk: str) -> tuple[list[str], list[str]]:
    """文件 / 音频 -> (记号行, 标题)。

    记号行 = 链接指向帖子页的记号 + 行内带上标题与附注（大小 / 来源）：
    文件本身在终端里打不开，标题才是可读的部分。"""
    marks: list[str] = []
    titles: list[str] = []
    for m in _DOC_RE.finditer(chunk):
        inner = m.group(1)
        href = hm.group(1) if (hm := _HREF_RE.search(m.group(0))) else ""
        title = _text_of(tm.group(1)) if (tm := _DOC_TITLE_RE.search(inner)) else ""
        extra = _text_of(em.group(1)) if (em := _DOC_EXTRA_RE.search(inner)) else ""
        kind = "media.audio" if _DOC_AUDIO_RE.search(inner) else "media.document"
        line = mark(t(kind), href) if href else t(kind)
        marks.append(line + (f" {title}" if title else "") + (f"（{extra}）" if extra else ""))
        titles.append(title)
    return marks, titles


def _pop_reply(chunk: str) -> tuple[str, str]:
    """摘出回复引用块：返回 (去掉引用块的 chunk, 引用文案)；没有引用时原样返回。

    引用块必须先摘出去，后面按类名找的东西（正文、作者）才不会被它带偏。"""
    m = _REPLY_RE.search(chunk)
    if m is None:
        return chunk, ""
    inner = m.group(1)
    author = _text_of(am.group(1)) if (am := _REPLY_AUTHOR_RE.search(inner)) else ""
    text = _text_of(tm.group(1)) if (tm := _REPLY_TEXT_RE.search(inner)) else ""
    if author and text:
        line = t("telegram.reply_to", author=author, text=text)
    elif text:
        line = t("telegram.reply_to_anon", text=text)
    elif author:
        line = t("telegram.reply_to_author", author=author)
    else:
        line = ""
    return chunk[: m.start()] + chunk[m.end() :], f"> {line}" if line else ""


def _forwarded_md(chunk: str) -> str:
    """转发来源行（引用块）；不是转发回空串。

    来源名与原帖署名都可能为空：从个人转发时名字隐藏、只剩署名（或连署名
    都没有），那时至少说明这是一条转发来的消息。"""
    m = _FWD_RE.search(chunk)
    if m is None:
        return ""
    block = m.group(1)
    name = ""
    if nm := _FWD_NAME_RE.search(block):
        source = nm.group(1)
        if el := _INNER_EL_RE.search(source):
            source = el.group(1)  # 来源名包在内层元素里；外面还挂着「(」等版式残片
        name = _text_of(source).strip("()").strip()
    author = _text_of(am.group(1)) if (am := _FWD_AUTHOR_RE.search(block)) else ""
    if name and author:
        return "> " + t("telegram.forwarded_from_signed", name=name, author=author)
    if name or author:
        return "> " + t("telegram.forwarded_from", name=name or author)
    return "> " + t("telegram.forwarded")


def _preview_md(chunk: str) -> str:
    """链接预览卡 -> 引用块（站点名 / 标题 / 摘要，缺哪行省哪行）；没有回空串。"""
    m = _PREVIEW_RE.search(chunk)
    if m is None:
        return ""
    inner = m.group(1)
    site = _text_of(sm.group(1)) if (sm := _PREVIEW_SITE_RE.search(inner)) else ""
    title = _text_of(tm.group(1)) if (tm := _PREVIEW_TITLE_RE.search(inner)) else ""
    desc = _text_of(dm.group(1)) if (dm := _PREVIEW_DESC_RE.search(inner)) else ""
    parts = [p for p in (f"**{site}**" if site else "", title, desc) if p]
    return "\n>\n".join(f"> {part}" for part in parts)


def _element_end(fragment: str, start: int, tag: str) -> int:
    """与已开标签配平的闭合标签位置（只数同名标签的嵌套）；找不到回片段末尾。"""
    depth = 1
    for m in re.finditer(rf"</?{tag}\b[^>]*>", fragment[start:]):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return start + m.start()
    return len(fragment)


def _poll(chunk: str, post_url: str) -> tuple[str, str]:
    """投票 -> (问题, markdown)；没有投票回 ("", "")。

    投票在终端里只能看不能投：正文末尾留一个指向帖子的 [投票] 记号（Ctrl+G
    可以从这里打开去投）。百分比取自选项自己的 _option_percent——选项文案里
    带「5%」不会被误读成票数。问题和选项都认不出（站点改了版式）时只留记号，
    帖子不至于从列表里消失。"""
    head = _POLL_RE.search(chunk)
    if head is None:
        return "", ""
    link = mark(t("media.poll"), post_url)
    block = chunk[head.end() : _element_end(chunk, head.end(), head.group(1))]
    question = _text_of(qm.group(1)) if (qm := _POLL_QUESTION_RE.search(block)) else ""
    rows: list[str] = []
    for m in _POLL_OPTION_RE.finditer(block):
        inner = block[m.end() : _element_end(block, m.end(), m.group(1))]
        text = _text_of(tm.group(1)) if (tm := _POLL_OPTION_TEXT_RE.search(inner)) else ""
        percent = _text_of(pm.group(1)) if (pm := _POLL_OPTION_PERCENT_RE.search(inner)) else ""
        if text:
            rows.append(f"- {text} — {percent}" if percent else f"- {text}")
    if not rows and not question:
        return "", link
    lines: list[str] = [f"**{question}**"] if question else []
    if rows:
        lines += ([""] if question else []) + rows
    if summary := _poll_summary(chunk, block):
        lines += ["", summary]
    return question, "\n".join([*lines, "", link])


def _poll_summary(chunk: str, block: str) -> str:
    """投票块下面的摘要行：类型（Anonymous Poll / Anonymous Quiz）+ 票数，缺哪个省哪个。"""
    kind = _text_of(tm.group(1)) if (tm := _POLL_TYPE_RE.search(block)) else ""
    if vm := _POLL_VOTES_RE.search(block):
        tally = _text_of(vm.group(1))
    else:
        tally = t("telegram.poll_voters", n=nm.group(1)) if (nm := _VOTERS_RE.search(chunk)) else ""
    return " · ".join(p for p in (kind, tally) if p)


def _title_of(*candidates: str) -> str:
    """标题 = 第一个非空候选按显示宽度截断（标题列不要 markdown 记号）。"""
    for text in candidates:
        if text:
            return truncate_display(text, TITLE_WIDTH)
    return ""


def _mark_labels(marks: list[str]) -> str:
    """记号行的标签文字（如「[图片]」），不带链接尾巴与行内附注。"""
    return " ".join(m[: m.index("]") + 1] for m in marks if m.startswith("[") and "]" in m)


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
        chunk, reply = _pop_reply(chunk)  # 先摘回复引用：正文才取得到帖子自己的
        forwarded = _forwarded_md(chunk)
        text_html = _strip_emoji(_main_text(chunk))
        question, poll_md = _poll(chunk, url)
        doc_marks, doc_titles = _document_marks(chunk)
        marks = doc_marks + _media_marks(chunk, url)
        md = "\n\n".join(
            block
            for block in (
                forwarded,
                reply,
                html_to_md(_strip_emoji_shell(text_html)),  # 正文：普通表情也不带星号
                poll_md,
                "\n\n".join(marks),
                _preview_md(chunk),
            )
            if block
        )
        # 投票没有问题可取（连选项都没认出）时用记号标签当标题，帖子不至于被跳掉
        poll_title = question or (t("media.poll") if poll_md else "")
        title = _title_of(
            _text_of(text_html), poll_title, " ".join(doc_titles), _mark_labels(marks)
        )
        if not title:
            continue  # 既无文字也无可展示的媒体：不可展示，跳条不跳流
        views = (m.group(1) if (m := _VIEWS_RE.search(chunk)) else "").strip()
        author = (m.group(1) if (m := _AUTHOR_RE.search(chunk)) else "").strip()
        iso = m.group(1) if (m := _TIME_RE.search(chunk)) else ""
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
                published=iso_epoch(iso),  # 列表时间列；extra 里保留 ISO 原文（详情层显示用）
                extra={
                    "content_md": md,
                    "published": iso,
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
    # 链接形态取路径里的频道名；裸名形态：整个串（去尾斜杠）就是频道名
    name = m.group(1) if (m := _LINK_RE.search(s)) else s.rstrip("/")
    return name.lower() if _NAME_RE.match(name) else ""
