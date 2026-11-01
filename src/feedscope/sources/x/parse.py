"""X（Twitter）解析层：GraphQL URT 响应 -> FeedItem / 详情，纯函数，不碰网络与浏览器。

合同（fetch 层只产这几类 dict / 调这几个函数）：
- _tweet_from_result(result)  单条推文 result（rest_id / legacy / core / note_tweet）
- _entries_and_cursor(data) + _iter_tweet_results(entries)  从 instructions 摊平条目
- _detail_entries_and_cursor(data)  TweetDetail 响应摊平（详情首批 + 回复翻页共用）
- _focal_and_replies(entries, rest_id) / _detail_doc / _reply_answer  详情三件套
- _grok_translation(result)  Grok 译文块（TweetDetail 响应内嵌，外文推才有）

三类推文形态（2026-11 实弹抓包确认，字段都从这两种嵌套里出）：
- 转发（RT）：外壳 legacy.full_text 只是 X 截断的 140 字预览（带 …），媒体
  entities / note_tweet / 被引推文全在内层 retweeted_status_result.result；
  外壳的 display_text_range 还不排除媒体尾链（内层的排），故列表一律读内层
- 引用（quote）：被引推文整卡嵌在外层 quoted_status_result.result（含作者与
  全文），外层 full_text 本身干净；被引已删时是 TweetTombstone（无 legacy）

URT 条目三类：
- TimelineTimelineItem：推文（itemContent.promotedMetadata 有值 = 推广，丢弃）
- TimelineTimelineModule：conversationthread-* / home-conversation-* 内嵌 items[] 是推文；who-to-follow-* 丢弃
- TimelineTimelineCursor：cursor-bottom-* / cursor-showmorethread-* 是下一页游标
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from feedscope.core.display import fmt_count, signature, votes_text
from feedscope.core.models import Answer, FeedItem
from feedscope.core.text import strip_zwsp, truncate_display
from feedscope.core.timefmt import fmt_twitter

BASE = "https://x.com"

# 长文（蓝 V note_tweet，可达几千字）在列表截到 280 显示格；普通推文（≤280 字）
# 全文上屏不截断——完整内容才是列表的卖点，超长文 Enter 进详情看全文
_TITLE_MAX_CELLS = 280

# 引用片段缀尾预算：列表里提示「引了什么」够用，被引全文进详情看
_QUOTE_SNIPPET_CELLS = 80


def _rt_result(r: dict) -> dict:
    """转发（RT）的内层原推 result；非转发或残卡返回空 dict。"""
    return ((r.get("legacy") or {}).get("retweeted_status_result") or {}).get("result") or {}


def _quote_result(r: dict) -> dict:
    """被引推文 result（quoted_status_result）；非引用或已删（tombstone 无 legacy）返回空 dict。"""
    q = _unwrap((r.get("quoted_status_result") or {}).get("result") or {})
    return q if q.get("legacy") else {}


def _tweet_text(r: dict) -> str:
    """推文全文（保留换行）：长推取 note_tweet 全文，否则 legacy.full_text。"""
    legacy = r.get("legacy") or {}
    text = strip_zwsp(legacy.get("full_text", ""))
    note = ((r.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    if note.get("text"):
        text = strip_zwsp(note.get("text", ""))
    return text


def _is_long_form(r: dict) -> bool:
    """蓝 V 长文（note_tweet，可达几千字）：列表截断只针对它。"""
    note = ((r.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    return bool(note.get("text"))


def _media_markers(r: dict) -> str:
    """媒体记号（[图 N] / [视频]）：扫描面用——列表缀尾、引用片段、详情标题回退。

    只报数量不附地址（列表一行是扫的不是读的，知乎列表同样不带）；地址
    形态见 _media_links（详情正文/回复楼层）。
    """
    media = ((r.get("legacy") or {}).get("entities") or {}).get("media") or []
    n_img = sum(1 for m in media if m.get("type") == "photo")
    has_video = any(m.get("type") in ("video", "animated_gif") for m in media)
    markers = ""
    if n_img:
        markers += f" [图 {n_img}]"
    if has_video:
        markers += " [视频]"
    return markers


def _best_video_url(m: dict) -> str:
    """视频媒体 -> 直链：最高码率 mp4（m3u8 是流清单不是播放地址，仅兜底）。"""
    variants = ((m.get("video_info") or {}).get("variants")) or []
    mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if mp4:
        return str(max(mp4, key=lambda v: v.get("bitrate") or 0)["url"])
    for v in variants:
        if v.get("url"):
            return str(v["url"])
    return ""


def _media_links(r: dict) -> str:
    """媒体尾注（详情正文/回复楼层）：每个媒体「[图] 地址 / [视频] 地址」一个 token。

    地址取 pbs.twimg.com 原图 / 最高码率 mp4——贴浏览器直达媒体本体；正文里
    嵌的 entities.media[].url 是 t.co 跳板（不指向媒体）不用。残卡无地址退
    纯记号（[图]/[视频]）。与知乎「[图] 地址内联」同词汇：TUI 显示不了媒体，
    地址留在正文里自己贴浏览器看。
    """
    media = ((r.get("legacy") or {}).get("entities") or {}).get("media") or []
    tokens: list[str] = []
    for m in media:
        if m.get("type") == "photo":
            url = str(m.get("media_url_https") or m.get("media_url") or "")
            tokens.append(f"[图] {url}" if url else "[图]")
        elif m.get("type") in ("video", "animated_gif"):
            url = _best_video_url(m)
            tokens.append(f"[视频] {url}" if url else "[视频]")
    return f" {' '.join(tokens)}" if tokens else ""


# Grok 译文源语言码 -> 中文名，括注在「译文」后（网页「翻译自 英语」的紧凑形）；
# 查无此码退原码
_LANG_NAMES = {
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "ru": "俄语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "it": "意大利语",
    "ar": "阿拉伯语",
    "tr": "土耳其语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "hi": "印地语",
    "uk": "乌克兰语",
    "nl": "荷兰语",
    "pl": "波兰语",
    "sv": "瑞典语",
    "da": "丹麦语",
    "fi": "芬兰语",
    "no": "挪威语",
    "el": "希腊语",
    "he": "希伯来语",
    "cs": "捷克语",
    "hu": "匈牙利语",
    "ro": "罗马尼亚语",
    "ms": "马来语",
    "fa": "波斯语",
    "ur": "乌尔都语",
    "bn": "孟加拉语",
    "tl": "菲律宾语",
}


def _grok_translation(r: dict) -> str:
    """Grok 自动译文块：`> **译文**（英语）：内容`；无译文返回空串。

    数据随 TweetDetail 响应内嵌（grok_translated_post_with_availability，
    X 网页版自动翻译同源，零额外请求）——服务端只给账号显示语言需要的外文
    推译文，is_available + 非空译文双闸。译文自带 entities（t.co 短链与
    display_url，坐标按译文文本），复用 _expand_urls 展开；换行压成单段
    （与引用块压缩同形）。署名「译文」+ 括注源语种（语种名查无退原码、
    缺省不括注），出处（Grok）不赘述。
    """
    g = r.get("grok_translated_post_with_availability") or {}
    data = g.get("data") or {}
    text = str(data.get("translation") or "").strip()
    if not g.get("is_available") or not text:
        return ""
    text = re.sub(r"\s+", " ", _expand_urls(text, data)).strip()
    lang = _LANG_NAMES.get(str(data.get("source_language") or ""), str(data.get("source_language") or ""))
    label = f"**译文**（{lang}）" if lang else "**译文**"
    return f"> {label}：{text}"


def _author(r: dict) -> tuple[str, str]:
    """推文作者 -> (昵称, handle)；署名格式化 _who 与判残卡共用。

    用户对象新旧两态：HomeLatestTimeline 还是 legacy 形态，TweetDetail（2026）
    已迁到 core 形态（user.core.name / screen_name），两处都认。
    """
    user = ((r.get("core") or {}).get("user_results") or {}).get("result") or {}
    ul = user.get("legacy") or {}
    uc = user.get("core") or {}
    name = str(ul.get("name") or uc.get("name") or "")
    handle = str(ul.get("screen_name") or uc.get("screen_name") or "")
    return strip_zwsp(name), strip_zwsp(handle)


def _who(r: dict) -> str:
    """作者署名「名字 @handle」（X 网页同形）：取作者字段，格式化归 core/display。

    昵称常有装饰字符认不出人，handle 才是身份；单露一个（旧版列表只露
    昵称、RT 前缀只露 handle）都拼不全作者是谁，全项目统一走这里。
    """
    name, handle = _author(r)
    return signature(name, handle)


def _entries_and_cursor(data: dict) -> tuple[list[dict], str | None]:
    """从 GraphQL 响应提取全部 entries 与下一页游标（cursor-bottom 条目的 value）。

    For You（HomeTimeline）与 Following（HomeLatestTimeline）同容器
    （data.home.home_timeline_urt，2026-11 实测）。instructions 里可能有多个
    TimelineAddEntries（首批只有一个），全部摊平；游标取最后一条 cursor-bottom
    （顶部的 cursor-top 是「回到顶部」，不算下一页）。
    """
    urt = ((data.get("data") or {}).get("home") or {}).get("home_timeline_urt") or {}
    entries: list[dict] = []
    for ins in urt.get("instructions") or []:
        if ins.get("type") == "TimelineAddEntries":
            entries.extend(ins.get("entries") or [])
    cursor: str | None = None
    for e in entries:
        if (e.get("entryId") or "").startswith("cursor-bottom"):
            cursor = (e.get("content") or {}).get("value")
    return entries, cursor


def _iter_tweet_results(entries: list[dict]) -> Iterator[dict]:
    """把 entries 里的推文摊平成 result dict 流：跳广告与关注推荐模块。"""
    for e in entries:
        content = e.get("content") or {}
        etype = content.get("entryType")
        if etype == "TimelineTimelineItem":
            item = content.get("itemContent") or {}
            if "promotedMetadata" in item:  # 推广广告
                continue
            res = (item.get("tweet_results") or {}).get("result")
            if res:
                yield res
        elif etype == "TimelineTimelineModule":
            if (e.get("entryId") or "").startswith("who-to-follow"):  # 关注推荐模块
                continue
            for sub in content.get("items") or []:
                item = (sub.get("item") or {}).get("itemContent") or {}
                if "promotedMetadata" in item:
                    continue
                res = (item.get("tweet_results") or {}).get("result")
                if res:
                    yield res


def _quote_snippet(r: dict) -> str:
    """引用片段（列表与回复楼层缀尾）：「 ▸ 引用 名字 @handle：内容（≤80 格）」。

    被引推文常是引用推文的实质内容（评论可能只有一句），不缀则完全不可见；
    被引残卡（已删 tombstone）返回空，宁缺毋滥。
    """
    q = _quote_result(r)
    _, qhandle = _author(q)
    if not qhandle:
        return ""
    body = re.sub(r"\s+", " ", _visible_text(q))
    body = truncate_display((body + _media_markers(q)).strip(), _QUOTE_SNIPPET_CELLS)
    if not body:
        return ""
    return f" ▸ 引用 {_who(q)}：{body}"


def _tweet_from_result(r: dict) -> FeedItem | None:
    """单条推文 result -> FeedItem；拿不到 id 或作者的残卡返回 None（调用方丢弃）。

    换行压成空格（列表标题是扫描用的单段文本）；图片/视频在列表只换 [图 N]/
    [视频] 计数记号缀尾（列表是扫描面不附地址，知乎列表同；详情/楼层附直接
    地址见 _media_links），记号不参与截断；正文按 display_text_range 裁（藏
    开头 @ 提及与结尾媒体 t.co 短链，与 X 网页同形）。
    普通推文（≤280 字）全文上屏，只有长文（note_tweet）才截断。

    转发（RT）正文/媒体/长文/引用一律读内层原推（外壳只是 140 字截断预览，
    见模块头抓包结论），标题加「RT 原作者 @handle: 」前缀（名字与账号同显），
    元信息列仍是转发者；详情按 conversation_id 跳原推会话。引用推文缀
    「▸ 引用」片段。
    """
    rest_id = str(r.get("rest_id") or "").strip()
    if not rest_id:
        return None
    name, handle = _author(r)
    if not handle:
        return None
    rt = _rt_result(r)
    src = rt or r  # 转发的实质内容全在内层
    text = re.sub(r"\s+", " ", _visible_text(src))
    if rt:
        orig_who = _who(rt)
        if orig_who:
            text = f"RT {orig_who}: {text}"
    body = truncate_display(text, _TITLE_MAX_CELLS) if _is_long_form(src) else text
    title = (body + _media_markers(src) + _quote_snippet(src)).strip()
    if not title:
        return None  # 正文与媒体全无 = 残卡
    # 转发（RT）本身没有回复串：会话在原推那里，详情按 conversation_id 跳到原推
    conversation_id = str(rt.get("rest_id") or "")
    return FeedItem(
        source="x",
        id=f"status/{rest_id}",
        title=title,
        url=f"{BASE}/{handle}/status/{rest_id}",
        kind="tweet",
        meta=f"{name} @{handle}".strip(),  # 署名「名字 @handle」；截断统一在 UI
        extra={"conversation_id": conversation_id} if conversation_id else {},
    )


# ---- 详情（TweetDetail 响应：threaded_conversation_with_injections_v2）----


def _detail_entries_and_cursor(data: dict) -> tuple[list[dict], str | None]:
    """TweetDetail 响应 -> (entries, 回复翻页 cursor)。

    与 _entries_and_cursor 同构：instructions 摊平，游标取最后一条 cursor-*
    （ShowMore 回复）。焦点推文是 tweet-{id} 条目，回复在 conversationthread-*
    模块里，条目序即阅读序（祖先推文混在最前，v1 当上下文读，不单独标注）。
    """
    urt = ((data.get("data") or {}).get("threaded_conversation_with_injections_v2") or {})
    entries: list[dict] = []
    for ins in urt.get("instructions") or []:
        if ins.get("type") == "TimelineAddEntries":
            entries.extend(ins.get("entries") or [])
    cursor: str | None = None
    for e in entries:
        if (e.get("entryId") or "").startswith("cursor-"):
            item = (e.get("content") or {}).get("itemContent") or {}
            value = item.get("value") or (e.get("content") or {}).get("value")
            if value:
                cursor = value
    return entries, cursor


def _unwrap(res: dict | None) -> dict:
    """TweetWithVisibilityResults 拆包：受限推文的 result 套一层 .tweet。"""
    if not res:
        return {}
    if res.get("__typename") == "TweetWithVisibilityResults":
        return res.get("tweet") or {}
    return res


def _ordered_results(entries: list[dict], skip: set[str]) -> list[dict]:
    """entries 里的推文按条目序摊平去重，跳过 skip 里的 rest_id。

    tweetdetailrelatedtweets-* 模块是「相关推文」推荐（详情页尾部），不是回复，
    整个丢弃。同一条回复可在顶层与 conversationthread 模块里各出现一次；
    翻页页也用它，skip 装焦点与已输出过的回复 id。
    """
    clean = [
        e for e in entries if not (e.get("entryId") or "").startswith("tweetdetailrelatedtweets")
    ]
    seen: set[str] = set(skip)
    out: list[dict] = []
    for r in (_unwrap(res) for res in _iter_tweet_results(clean)):
        rid = str(r.get("rest_id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            out.append(r)
    return out


def _focal_and_replies(entries: list[dict], rest_id: str) -> tuple[dict, dict | None, list[dict]]:
    """首批 entries -> (焦点推文, 父推「原推」, 回复列表)。

    焦点按 rest_id 匹配，匹配不到时退第一条（接口异常形态，保底不空屏）。
    回复型推文的父推（focal.in_reply_to_status_id_str 指向的原推）从回复列表
    拆出来单独展示，不混进楼层；匹配不到（父推被删/不在首屏）则 None。
    """
    results = _ordered_results(entries, set())
    focal = next((r for r in results if str(r.get("rest_id") or "") == rest_id), None)
    if focal is None:
        focal = results[0] if results else {}
    parent_id = str((focal.get("legacy") or {}).get("in_reply_to_status_id_str") or "")
    parent = None
    if parent_id:
        parent = next((r for r in results if str(r.get("rest_id") or "") == parent_id), None)
    replies = [r for r in results if r is not focal and r is not parent]
    return focal, parent, replies


def _quote_context(r: dict, label: str) -> str:
    """引用块（详情）：label「原推」= 回复的父推（置正文上方），「被引推文」= quote（置正文下方）。

    换行压成单段（块引用逐行加 > 不值当），带作者署名（名字 @handle）与媒体
    记号；外文推的 Grok 译文作第二段附在块内（与正文同词汇）。
    """
    who = _who(r) or "?"
    body = (re.sub(r"\s+", " ", _visible_text(r)) + _media_markers(r)).strip()
    out = f"> **{label}** · {who}：{body}"
    tr = _grok_translation(r)
    if tr:
        out += "\n>\n" + tr
    return out


def _detail_doc(focal: dict, parent: dict | None) -> tuple[str, str, str, str]:
    """焦点推文 -> (title, meta, description_md, time)。

    title 取正文首行截 60 格（详情屏标题行与 Header 副标题共用，标题保持
    原文——扫描面）；正文全文（含换行，开头的 @ 提及已藏）放 description_md，
    媒体附直接地址（_media_links，与知乎同形），外文推的 Grok 译文块接在
    正文与媒体之后（阅读面才有译文，列表/标题不带），单行短文与标题重复时
    不再二刷（有译文时不折叠，译文不能丢）。回复型推文（有父推）时，父推
    作为「原推」引用块放在正文上方；引用推文（quote）的被引块放在正文下方
    （与 X 网页的阅读序一致）；引用块是压缩摘要，媒体只记号不附地址，译文
    随块附（原推/被引是外文时）。
    """
    legacy = focal.get("legacy") or {}
    name, handle = _author(focal)
    body = _visible_text(focal)
    title = truncate_display(body.split("\n", 1)[0].strip(), 60) or _media_markers(focal).strip() or "推文"
    meta = f"{name} @{handle}".strip()  # 署名「名字 @handle」（详情行全宽，不再截 8 格）
    if legacy.get("reply_count"):
        meta += f" · {fmt_count(legacy['reply_count'])} 回复"
    if legacy.get("favorite_count"):
        meta += f" · {fmt_count(legacy['favorite_count'])} 赞"
    desc = (body + _media_links(focal)).strip()
    tr = _grok_translation(focal)
    if tr:
        desc = f"{desc}\n\n{tr}" if desc else tr
    if parent:
        desc = _quote_context(parent, "原推") + "\n\n" + desc
    quoted = _quote_result(focal)
    if quoted:
        desc += "\n\n" + _quote_context(quoted, "被引推文")
    return title, meta, "" if desc == title else desc, fmt_twitter(legacy.get("created_at"))


def _visible_text(r: dict) -> str:
    """推文可见文本：用 display_text_range 藏掉开头的 @ 提及与结尾的媒体
    t.co 短链（详情里焦点与回复楼层都要；索引按 UTF-16 码元，先转码再切，
    emoji 不偏移），再把正文里的内联 t.co 短链展开成 X 网页同款 display_url。
    """
    text = _tweet_text(r)
    legacy = r.get("legacy") or {}
    rng = legacy.get("display_text_range")
    offset = 0
    if rng and len(rng) == 2 and not _is_long_form(r):
        try:
            u16 = text.encode("utf-16-le")
            clipped = u16[rng[0] * 2 : rng[1] * 2].decode("utf-16-le", errors="ignore")
            if clipped:
                text = clipped
                offset = rng[0]  # url 实体索引仍是原文坐标，裁剪后要平移
        except UnicodeError:
            pass  # 转码失败（异常形态）就保留原文，别丢内容
    return _strip_trailing_media(_expand_urls(text, legacy, offset), legacy)


def _strip_trailing_media(text: str, legacy: dict) -> str:
    """剥结尾的媒体 t.co 短链（媒体已有 [图 N]/[视频] 记号代偿，X 网页也不显示它）。

    display_text_range 通常已把它排除，但实测个别推文会把它划进显示区（索引
    不可靠）——按「结尾恰好是某条媒体 url」剥，只剥尾不碰中段。
    """
    media_urls = {str(m.get("url")) for m in (legacy.get("entities") or {}).get("media") or [] if m.get("url")}
    for _ in range(len(media_urls)):
        t = text.rstrip()
        hit = next((u for u in media_urls if t.endswith(u)), None)
        if not hit:
            break
        text = t[: -len(hit)].rstrip()
    return text


def _expand_urls(text: str, legacy: dict, offset: int = 0) -> str:
    """正文内联 t.co 短链 -> display_url（如 wapo.st/4zmXHM1，X 网页同款）。

    索引按 UTF-16 码元（与 display_text_range 同规则），offset 是裁剪掉的
    前缀码元数；只换完整落在当前文本内的实体（越界/重叠的不碰，截断残余
    原样保留）。长文（note_tweet）不走这里——其 url 实体坐标在 note 侧，
    legacy 里没有，硬套会错位。
    """
    urls = (legacy.get("entities") or {}).get("urls") or []
    if not urls:
        return text
    try:
        u16 = text.encode("utf-16-le")
    except UnicodeError:
        return text
    total = len(u16) // 2
    out: list[bytes] = []
    pos = 0  # 已拷贝到的码元游标
    for u in sorted(urls, key=lambda u: (u.get("indices") or [0, 0])[0]):
        rng = u.get("indices") or []
        disp = str(u.get("display_url") or u.get("url") or "")
        if len(rng) != 2 or not disp:
            continue
        a, b = rng[0] - offset, rng[1] - offset
        if a < pos or b > total or a >= b:  # 越界 / 重叠 / 倒序：不碰
            continue
        out.append(u16[pos * 2 : a * 2])
        out.append(disp.encode("utf-16-le"))
        pos = b
    if not out:
        return text
    out.append(u16[pos * 2 :])
    try:
        return b"".join(out).decode("utf-16-le", errors="ignore")
    except UnicodeError:
        return text


def _reply_answer(r: dict) -> Answer | None:
    """一条回复推文 -> Answer；残卡（无 id 或作者）返回 None（调用方丢弃）。

    内容序：正文（换行保留）→ 媒体地址 → 引用片段 → Grok 译文块（外文回复，
    与详情正文同词汇）。译文块必须缀尾——它是 blockquote，引用片段若接在其后
    会粘进引用行里，看起来像译文的一部分。
    """
    rest_id = str(r.get("rest_id") or "").strip()
    _, handle = _author(r)
    if not rest_id or not handle:
        return None
    legacy = r.get("legacy") or {}
    content = (_visible_text(r) + _media_links(r) + _quote_snippet(r)).strip()
    tr = _grok_translation(r)
    if tr:
        content = f"{content}\n\n{tr}" if content else tr
    return Answer(
        author=_who(r),
        votes=votes_text(legacy.get("favorite_count") or 0),
        content_md=content,
        time=fmt_twitter(legacy.get("created_at")),
    )
