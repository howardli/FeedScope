"""知乎解析层：DOM 提取结果 / API JSON / HTML -> 统一模型，纯函数，不碰网络与浏览器。

本层只做「脏数据 -> FeedItem/Answer/markdown」的转换与清洗；取数在 fetch.py，
编排在包 __init__。所有函数可直接用 fixture 测试。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from feedscope.core.media import h2t as _h2t
from feedscope.core.display import fmt_count, view_count_text, votes_text
from feedscope.core.i18n import t
from feedscope.core.media import VIDEO_RE, Tokens, attrs, mark, pick_url, video_url
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.text import strip_zwsp
from feedscope.core.timefmt import fmt_unix

# html2text 统一配置见 core/const.py（不硬换行；img/video 进它之前已换成记号）
# HTML2Text 实例经 media.h2t() 取（线程本地，见其 docstring），源内不自建

_NOSCRIPT_RE = re.compile(r"<noscript>.*?</noscript>", re.IGNORECASE | re.DOTALL)
# 表情贴纸（class="sticker"）不是内容图：换成 alt 文本（如 "[大哭]"），
# 走 _IMG_RE 会变 [图片]+地址，那是看图不是看表情
_STICKER_RE = re.compile(r'<img[^>]*class="[^"]*\bsticker\b[^"]*"[^>]*>', re.IGNORECASE)
_ALT_RE = re.compile(r'alt="([^"]*)"')
_IMG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
# img 地址按尺寸优先（2026-08 实测标记形态）：data-original 原图 > data-actualsrc
# 显示图 > src（src 常是 data:image/svg+xml 懒加载占位，pick_url 会跳过 data:）。
# 2026-11 实测 data-original 整 URL 属性已没了（只剩 data-original-token），多数落到 src
_IMG_URL_ATTRS = ("data-original", "data-actualsrc", "src")
# 视频卡（2026-11 实测）：content 里没有 <video> 标签，视频是 <a class="video-box">
# 卡片——href 是 link.zhihu.com 跳板，data-lens-id 是视频页 id，内部包封面缩略图
_VIDEO_BOX_RE = re.compile(r'<a\b[^>]*class="video-box"[^>]*>.*?</a>', re.IGNORECASE | re.DOTALL)
# zhimg 的 /50/ 是缩略代理路径（同 hash 内容更小），剥掉直达显示图
_ZHIMG_50_RE = re.compile(r"^(https://pic[\da-z]*\.zhimg\.com)/50/")


def _upgrade_img_url(url: str) -> str:
    """图片地址微升级：剥 /50/ 缩略代理（实测同 hash 直连内容更大）。

    不猜原图尺寸：_r 原尺寸版 / 80/…_1440w 都只对部分 hash 存在（2026-11
    实测多数 405），盲改写会产出死链；src 的 _720w 本就是网页显示图。
    """
    return _ZHIMG_50_RE.sub(r"\1/", url)


def _video_box_md(block: str) -> str:
    """video-box 卡片 -> [视频](视频页)。优先 data-lens-id 构造（跳板不参与，
    最稳）；缺了才 decode href 跳板的 target 参数。封面缩略图随整卡吃掉。"""
    m = re.match(r"<a\b[^>]*>", block)
    a = attrs(m.group(0)) if m else {}
    if lens := a.get("data-lens-id", ""):
        return mark(t("media.video"), f"https://www.zhihu.com/video/{lens}")
    href = a.get("href", "")
    target = parse_qs(urlparse(href).query).get("target")
    return mark(t("media.video"), target[0] if target else href)


def _md(html: str) -> str:
    """HTML -> markdown：图片/视频换 [图片]/[视频] 链接（TUI 显示不了媒体，
    链接点击直达原图 / 视频页）、清零宽字符。"""
    if not html:
        return ""
    html = _NOSCRIPT_RE.sub("", html)  # noscript 里是 img 的重复副本

    def _sticker_text(m: re.Match[str]) -> str:
        a = _ALT_RE.search(m.group(0))
        return a.group(1) if a else ""

    html = _STICKER_RE.sub(_sticker_text, html)  # 表情贴纸 -> "[大笑]" 文本
    tok = Tokens()
    html = _VIDEO_BOX_RE.sub(  # 先于 img：整卡吃掉，封面缩略图不单独出 [图片]
        lambda m: tok.stash(_video_box_md(m.group(0))), html
    )
    html = _IMG_RE.sub(
        lambda m: tok.stash(
            mark(t("media.image"), _upgrade_img_url(pick_url(m.group(0), _IMG_URL_ATTRS)))
        ),
        html,
    )
    html = VIDEO_RE.sub(lambda m: tok.stash(mark(t("media.video"), video_url(m.group(0)))), html)
    text = strip_zwsp(tok.expand(_h2t().handle(html)))
    # 相邻同图合并（懒加载双标记会把同一张图渲染两次）；不同图各自保留。
    # 链接形式 [图片](url) 整体比对；纯记号合并的 (?!\() 断言防止把带地址的
    # [图片](…) 误吞成纯 [图片] 丢掉链接
    img = re.escape(t("media.image"))  # 记号随语言：正则由当前文案生成，回读永远对得上
    text = re.sub(rf"({img}\([^)]*\))(?:[ \t]*\1)+", r"\1", text)
    return re.sub(rf"({img}(?!\())(?:[ \t]*{img}(?!\())+", r"\1", text)


def _hot_api_items(data: dict) -> list[FeedItem]:
    """热榜 API（www/api/v3/feed/topstory/hot-lists/total）响应 -> FeedItem 列表。

    target.url 是 api.zhihu.com/questions/{id}（浏览器不可达的内部形态），
    统一重写为 www 问题页；元信息列放 detail_text（「2606 万热度」），与
    页面头部形态一致。接口上限 30 条且无翻页（limit 参数无效，2026-11 实测）。
    注意：本接口的 target.id 是未加引号的大整数（超 JS 安全整数 2^53），
    浏览器 r.json() 解析会丢精度（实测 …1699522 -> …1699500，进详情即 404）；
    target.url 是字符串、精确——id 一律从 url 提取，id 字段仅作兜底。
    """
    out: list[FeedItem] = []
    for r in data.get("data") or []:
        tgt = r.get("target") or {}
        m = re.search(r"/questions?/(\d+)", tgt.get("url") or "")
        qid = m.group(1) if m else str(tgt.get("id") or "")
        title = strip_zwsp(tgt.get("title") or "")
        if tgt.get("type") != "question" or not qid or not title:
            continue
        out.append(
            FeedItem(
                source="zhihu",
                id=f"question/{qid}",
                title=title,
                url=f"https://www.zhihu.com/question/{qid}",
                kind="question",
                meta=strip_zwsp(r.get("detail_text") or ""),
            )
        )
    return out


def _feed_api_item(r: dict) -> FeedItem | None:
    """推荐/关注 feed API 条目 -> FeedItem；None = 丢弃。

    只留能进终端详情的问题卡（详情只支持问题页，文章/想法/视频等不展示）：
    - answer 卡（推荐 TOPIC_ACKNOWLEDGED_* / 关注 MEMBER_[VOTEUP_|ANSWER_]）：
      标题取所属问题，作者取回答作者（与站方卡片展示的信息同源）
    - question 卡（关注 MEMBER_ASK_QUESTION）：标题直接在 target 上
    - 其余（feed_group 分组卡、文章/想法/视频/广告）不进列表
    """
    t = r.get("target") or {}
    if t.get("type") == "answer":
        q = t.get("question") or {}
        qid, title = str(q.get("id") or ""), strip_zwsp(q.get("title") or "")
        author = strip_zwsp((t.get("author") or {}).get("name") or "")
    elif t.get("type") == "question":
        qid, title = str(t.get("id") or ""), strip_zwsp(t.get("title") or "")
        author = strip_zwsp((t.get("author") or {}).get("name") or "")
    else:
        return None
    if not qid or not title:
        return None
    return FeedItem(
        source="zhihu",
        id=f"question/{qid}",
        title=title,
        url=f"https://www.zhihu.com/question/{qid}",
        kind="question",
        meta=author,  # 信息流元信息列放作者全名；列宽截断统一在 UI
    )


def _api_question_doc(q: dict, answers: list[Answer]) -> DetailDoc:
    """问题元信息 API（v4/questions/{qid}?include=…，2026-11 实测）-> DetailDoc。

    meta 行「提问人 提问 · N 个回答 · N 万浏览」对齐页面头部形态；时间裸
    展示文本（与 X 详情同形，不带「提问于」前缀——各源元信息行/楼层头的
    时间统一裸时间，词汇归 core/timefmt 一处）；描述 detail 是 HTML，走 _md。
    """
    asker = strip_zwsp((q.get("author") or {}).get("name") or t("words.anonymous"))
    meta = t("words.zhihu_asked", asker=asker)
    if n := q.get("answer_count"):
        meta += t("words.zhihu_n_answers", n=n)
    if visit := q.get("visit_count"):
        meta += view_count_text(visit)  # 万浏览/浏览的分支拼写收在 core.display
    created = q.get("created") or 0
    return DetailDoc(
        title=strip_zwsp(q.get("title") or ""),
        meta=meta,
        time=fmt_unix(created),
        description_md=_md(q.get("detail") or ""),
        answers=answers,
    )


def _api_comment(c: dict) -> Comment:
    """comment_v5 JSON -> Comment（评论层数据全走 API，不碰 DOM）。"""
    kids = c.get("child_comments") or []
    likes = c.get("like_count") or 0
    created = c.get("created_time") or 0
    # root 接口 content 是纯文本，child 接口带 <p> 等 HTML：有标签才过 _md，
    # 纯文本直用（_md 对纯文本可能做不必要的转义）
    text = c.get("content") or ""
    content_md = _md(text) if re.search(r"<[a-zA-Z]", text) else strip_zwsp(text)
    return Comment(
        author=strip_zwsp((c.get("author") or {}).get("name") or t("words.anonymous")),
        votes=votes_text(likes),
        content_md=content_md,
        time=fmt_unix(created) if created else "",
        replies=[_api_comment(r) for r in kids],
        raw_id=str(c.get("id") or ""),
    )


def _api_comments(data: dict) -> list[Comment]:
    """root_comment 一页 -> 顶层评论列表（删掉的/仅自己可见的不算数）。"""
    roots = [
        c
        for c in data.get("data") or []
        if not c.get("is_delete") and not c.get("is_visible_only_to_myself")
    ]
    return [_api_comment(c) for c in roots]


def _feeds_answers(data: dict, qid: str) -> list[Answer]:
    """回答 feeds API（questions/{qid}/feeds，2026-11 实测免签）一页 -> Answer 批次。

    展示词汇全 app 统一（core/display 唯一出处，与 X 回复楼层同形）：
    votes「赞 N」、time 裸北京时间、comment_count「N 条评论」；非 answer 卡
    （罕见混入）跳过。
    """
    out: list[Answer] = []
    for a in data.get("data") or []:
        tgt = a.get("target") or {}
        aid = str(tgt.get("id") or "")
        if tgt.get("type") != "answer" or not aid:
            continue
        votes = tgt.get("voteup_count") or 0
        cc = tgt.get("comment_count") or 0
        created = tgt.get("created_time") or 0
        out.append(
            Answer(
                author=strip_zwsp((tgt.get("author") or {}).get("name") or t("words.anonymous")),
                votes=votes_text(votes),
                content_md=_md(tgt.get("content") or ""),
                time=fmt_unix(created),
                url=f"https://www.zhihu.com/question/{qid}/answer/{aid}",
                comment_count=t("display.comments_n", n=fmt_count(cc)) if cc else "",
            )
        )
    return out


