"""X（Twitter）解析层：GraphQL URT 响应 -> FeedItem / 详情，手写最小 JSON fixture，不碰网络。

列表（parse_tweet / parse_entries_and_cursor）与详情（parse_focal_and_replies /
parse_detail_doc / parse_reply / Grok 译文）两族；夹具构造件在同目录 fixtures.py。
"""

from __future__ import annotations

import re

from feedscope.core.text import display_width
from feedscope.sources.x.parse import (
    parse_detail_doc,
    parse_detail_entries_and_cursor,
    parse_entries_and_cursor,
    parse_focal_and_replies,
    parse_reply,
    parse_tweet,
    parse_tweet_results,
)
from tests.sources.x.fixtures import (
    conv_data,
    entry,
    item_content,
    showmore_cursor,
    tweet_result,
    wrapped_result,
)

_TIME_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

# ---- X（Twitter，GraphQL URT -> FeedItem）----


def test_x_tweet_basic() -> None:
    it = parse_tweet(tweet_result("1001", text="  Hello\u200b world  ", handle="user1", name="宝\u200b玉"))
    assert it is not None
    assert it.source == "x"
    assert it.id == "status/1001"
    assert it.url == "https://x.com/user1/status/1001"
    assert it.kind == "tweet"
    assert it.title == "Hello world"  # 零宽字符与首尾空白被清
    assert it.meta == "宝玉 @user1"  # 署名「名字 @handle」（名字与账号同显），零宽清除


def test_x_tweet_meta_falls_back_to_handle() -> None:
    it = parse_tweet(tweet_result("1001", name=""))
    assert it is not None
    assert it.meta == "@user"  # 作者名缺失时只剩账号（带 @，署名词汇一致）


def test_x_tweet_meta_keeps_full_name() -> None:
    it = parse_tweet(tweet_result("1001", name="一个特别长的推主昵称超过八字"))
    assert it is not None
    assert it.meta == "一个特别长的推主昵称超过八字 @user"  # 全名 + 账号；列宽截断统一在 UI（meta_col_text）


def test_x_tweet_note_overrides_full_text() -> None:
    it = parse_tweet(tweet_result("1001", text="预览截断…", note="完整长推文"))
    assert it is not None
    assert it.title == "完整长推文"  # 长推取 note_tweet 全文，不是 legacy 预览


def test_x_tweet_media_markers_and_newline_collapse() -> None:
    media = [{"type": "photo"}, {"type": "photo"}, {"type": "video"}]
    it = parse_tweet(tweet_result("1001", text="第一行\n\n第二行", media=media))
    assert it is not None
    assert it.title == "第一行 第二行 [图片 2] [视频]"  # 换行压成空格；媒体换记号缀尾


def test_x_tweet_video_only() -> None:
    it = parse_tweet(tweet_result("1001", text="", media=[{"type": "animated_gif"}]))
    assert it is not None
    assert it.title == "[视频]"  # 纯媒体推文以记号为题，不是噪音


def test_x_tweet_normal_text_not_truncated() -> None:
    # 普通推文（无 note_tweet）全文上屏：超过旧截断上限也不截
    it = parse_tweet(tweet_result("1001", text="字" * 100 + "a" * 100))  # 300 显示格
    assert it is not None
    assert it.title == "字" * 100 + "a" * 100


def test_x_tweet_long_form_truncated_by_display_width() -> None:
    # 长文（蓝 V note_tweet，可达几千字）才截断：280 格上限 + 省略号
    it = parse_tweet(tweet_result("1001", text="预览…", note="字" * 100 + "a" * 100))
    assert it is not None
    assert display_width(it.title) <= 281
    assert it.title.startswith("字")
    assert it.title.endswith("…")


def test_x_tweet_long_form_does_not_apply_legacy_url_indices() -> None:
    """note_tweet 的正文索引与 legacy 不是同一坐标系：legacy URL 实体不能套到长文上。
    （旧实现会在长文首字符处错位替换成 display_url。）"""
    r = tweet_result("1001", text="预览…", note="长文正文内容")
    r["legacy"]["entities"]["urls"] = [
        {"url": "https://t.co/x", "display_url": "bad.example", "indices": [0, 2]}
    ]
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "长文正文内容"


def test_x_tweet_noise_dropped() -> None:
    assert parse_tweet({}) is None  # 无 rest_id 的残卡
    assert parse_tweet(tweet_result("", text="x")) is None  # 空 id
    assert parse_tweet(tweet_result("1001", text="")) is None  # 正文与媒体全无


def test_x_tweet_retweet_reads_inner_not_wrapper_preview() -> None:
    # 转发读内层原推（外壳 full_text 只是 X 截断的 140 字预览，媒体/长文都在内层，
    # 2026-11 实弹抓包确认）：前缀「RT 原作者 @handle: 」，元信息列仍是转发者；
    # extra 带上原推 id，详情据此跳原推会话（RT 本身没有回复串）
    r = tweet_result("1001", text="RT @orig: 原文内容… https://t.co/leak")
    inner = tweet_result("2002", text="原文内容", handle="orig", name="原作者", media=[{"type": "video"}])
    r["legacy"]["retweeted_status_result"] = {"result": inner}
    it = parse_tweet(r)
    assert it is not None
    assert it.id == "status/1001"  # 列表条目仍是这条 RT
    assert it.url == "https://x.com/user/status/1001"
    assert it.title == "RT 原作者 @orig: 原文内容 [视频]"  # 内层全文 + 内层媒体记号；预览省略号与 t.co 都不见
    assert it.meta == "作者 @user"  # 元信息列是转发者（你关注的人），原作者在前缀里
    assert it.extra["conversation_id"] == "2002"


def test_x_tweet_retweet_inner_long_form_and_quote() -> None:
    # 转发的内层是长文（note_tweet）→ 列表截 280 格；内层自己还是引用推文 → 片段也从内层出
    r = tweet_result("1001", text="RT @orig: 预览…")
    inner = tweet_result("2002", text="预览…", handle="orig", note="字" * 100 + "a" * 100)
    inner["quoted_status_result"] = {"result": tweet_result("3003", text="被引推文", handle="q")}
    r["legacy"]["retweeted_status_result"] = {"result": inner}
    it = parse_tweet(r)
    assert it is not None
    assert it.title.startswith("RT 作者 @orig: " + "字" * 100)  # 长文取 note 全文再截，不是外壳预览
    assert "▸ 引用 作者 @q：被引推文" in it.title


def test_x_tweet_media_tco_clipped_in_list() -> None:
    # 带媒体推文的 full_text 结尾是 t.co 短链：列表同详情一样按 display_text_range 裁掉
    r = tweet_result("1001", text="看这个 https://t.co/abc123", media=[{"type": "photo"}])
    r["legacy"]["display_text_range"] = [0, 3]  # 「看这个」3 个码元，尾链不在显示区
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "看这个 [图片 1]"  # raw 短链不漏，媒体记号缀尾


def test_x_tweet_media_tco_inside_display_range_still_stripped() -> None:
    # 实测个别推文把媒体尾链划进 display_text_range（索引不可靠）：按结尾匹配兜底剥
    r = tweet_result(
        "1001", text="正文 https://t.co/m", media=[{"type": "photo", "url": "https://t.co/m"}]
    )
    r["legacy"]["display_text_range"] = [0, 17]  # 尾链 14 码元被划进显示区（异常形态）
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "正文 [图片 1]"  # 记号代偿，raw 短链不漏


def test_x_tweet_inline_url_expanded() -> None:
    # 正文内联 t.co 短链（用户分享的网址，在显示区内）换成 X 网页同款 display_url
    text = "看好这篇 wapo.st 短评 https://t.co/abc123"
    r = tweet_result("1001", text=text)
    r["legacy"]["entities"]["urls"] = [
        {"url": "https://t.co/abc123", "display_url": "wapo.st/xyz", "indices": [16, 35]}
    ]
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "看好这篇 wapo.st 短评 wapo.st/xyz"


def test_x_tweet_inline_url_out_of_range_left_as_is() -> None:
    # url 实体越界（异常形态/截断残余）：不硬换，原文保留不崩
    r = tweet_result("1001", text="短文 https://t.co/x")
    r["legacy"]["entities"]["urls"] = [
        {"url": "https://t.co/x", "display_url": "x.y/z", "indices": [500, 520]}
    ]
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "短文 https://t.co/x"


def test_x_tweet_plain_has_no_conversation_id() -> None:
    assert parse_tweet(tweet_result("1001", text="普通推文")).extra == {}


def test_x_tweet_quote_snippet_appended() -> None:
    # 引用推文：被引内容不缀则完全不可见，「▸ 引用 名字 @handle：片段」缀尾（换行压空格）
    r = tweet_result("1001", text="我的评论")
    r["quoted_status_result"] = {"result": tweet_result("3003", text="被引\n推文", handle="who", name="被引者")}
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "我的评论 ▸ 引用 被引者 @who：被引 推文"


def test_x_tweet_quote_tombstone_no_snippet() -> None:
    # 被引已删（TweetTombstone 无 legacy）：宁缺毋滥，不缀片段
    r = tweet_result("1001", text="我的评论")
    r["quoted_status_result"] = {"result": {"__typename": "TweetTombstone", "text": "…"}}
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "我的评论"


def test_x_tweet_quote_only_survives() -> None:
    # 不带评论的引用推文（正文空）：以片段为题，不当残卡丢
    r = tweet_result("1001", text="")
    r["quoted_status_result"] = {"result": tweet_result("3003", text="被引内容", handle="who")}
    it = parse_tweet(r)
    assert it is not None
    assert it.title == "▸ 引用 作者 @who：被引内容"


def test_x_iter_tweet_results_skips_promoted_and_who_to_follow() -> None:
    entries = [
        entry(entry_id="tweet-1", item_content=item_content(tweet_result("1"))),
        entry(entry_id="promoted-tweet-2", item_content=item_content(tweet_result("2"), promoted=True)),
        entry(entry_id="who-to-follow-3", items=[{"item": {"itemContent": item_content(tweet_result("3"))}}]),
        entry(entry_id="home-conversation-4", items=[{"item": {"itemContent": item_content(tweet_result("4"))}}]),
    ]
    got = [str(r["rest_id"]) for r in parse_tweet_results(entries)]
    assert got == ["1", "4"]  # 广告、关注推荐模块丢弃，conversation 内嵌推文保留


def test_x_entries_and_cursor() -> None:
    data = {"data": {"home": {"home_timeline_urt": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [
            entry(entry_id="cursor-top-1", cursor_value="top"),
            entry(entry_id="tweet-1", item_content=item_content(tweet_result("1"))),
            entry(entry_id="cursor-bottom-2", cursor_value="next-cursor"),
        ]},
    ]}}}}
    entries, cursor = parse_entries_and_cursor(data)
    assert len(entries) == 3
    assert cursor == "next-cursor"  # 只认 cursor-bottom，不认 cursor-top


def test_x_entries_and_cursor_no_cursor() -> None:
    data = {"data": {"home": {"home_timeline_urt": {"instructions": []}}}}
    entries, cursor = parse_entries_and_cursor(data)
    assert entries == []
    assert cursor is None



# ---- X 推文详情（TweetDetail：threaded_conversation_with_injections_v2）----


def test_x_detail_entries_and_cursor() -> None:
    entries = [
        entry(entry_id="tweet-100", item_content=item_content(tweet_result("100"))),
    ]
    entries, cursor = parse_detail_entries_and_cursor(conv_data(entries, cursor="more-1"))
    assert len(entries) == 2
    assert cursor == "more-1"


def test_x_detail_entries_no_cursor() -> None:
    entries, cursor = parse_detail_entries_and_cursor(conv_data([], cursor=None))
    assert entries == []
    assert cursor is None


def test_x_focal_unwraps_visibility_and_dedups() -> None:
    entries = [
        entry(entry_id="tweet-100", item_content=item_content(wrapped_result(tweet_result("100", text="焦点", new_user=True)))),
        entry(
            entry_id="conversationthread-1",
            items=[
                {"item": {"itemContent": item_content(tweet_result("101", text="回复", new_user=True))}},
                {"item": {"itemContent": item_content(tweet_result("101", text="重复条目", new_user=True))}},
            ],
        ),
    ]
    focal, parent, replies = parse_focal_and_replies(entries, "100")
    assert focal["rest_id"] == "100"  # 受限推文拆包后拿到本体
    assert parent is None  # 非回复型推文无父推
    assert [r["rest_id"] for r in replies] == ["101"]  # 同一条回复出现两次只留一次


def test_x_focal_missing_falls_back_to_first() -> None:
    entries = [entry(entry_id="tweet-101", item_content=item_content(tweet_result("101", text="回复", new_user=True)))]
    focal, parent, replies = parse_focal_and_replies(entries, "999")  # 焦点不在（接口异常形态）
    assert focal["rest_id"] == "101"  # 保底取第一条，不空屏
    assert parent is None
    assert replies == []


def test_x_focal_splits_parent_from_replies() -> None:
    # 回复型推文：父推（原推）从回复列表拆出来单独展示
    parent = tweet_result("200", text="原推正文", name="原作者", new_user=True)
    reply = tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    child = tweet_result("101", text="楼中回复", new_user=True)
    entries = [
        entry(entry_id="tweet-200", item_content=item_content(parent)),
        entry(entry_id="tweet-100", item_content=item_content(reply)),
        entry(entry_id="conversationthread-1", items=[{"item": {"itemContent": item_content(child)}}]),
    ]
    focal, got_parent, replies = parse_focal_and_replies(entries, "100")
    assert focal["rest_id"] == "100"
    assert got_parent["rest_id"] == "200"  # 原推拆出来
    assert [r["rest_id"] for r in replies] == ["101"]  # 只剩真正的回复


def test_x_detail_drops_related_tweets_module() -> None:
    # tweetdetailrelatedtweets 模块是「相关推文」推荐，不是回复，丢弃
    entries = [
        entry(entry_id="tweet-100", item_content=item_content(tweet_result("100", text="焦点", new_user=True))),
        entry(
            entry_id="tweetdetailrelatedtweets-1",
            items=[{"item": {"itemContent": item_content(tweet_result("999", text="相关推荐", new_user=True))}}],
        ),
        entry(
            entry_id="conversationthread-2",
            items=[{"item": {"itemContent": item_content(tweet_result("101", text="真回复", new_user=True))}}],
        ),
    ]
    _focal, _parent, replies = parse_focal_and_replies(entries, "100")
    assert [r["rest_id"] for r in replies] == ["101"]


def test_x_detail_cursor_itemcontent_form() -> None:
    # ShowMore 线索形态的游标（itemContent 里带 value）也要认
    data = {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [showmore_cursor("abc")]}
    ]}}}
    _entries, cursor = parse_detail_entries_and_cursor(data)
    assert cursor == "abc"


def _detail_parts(focal: dict, parent: dict | None) -> tuple[str, str, str, str]:
    """parse_detail_doc 产出 DetailDoc；用例沿用 (title, meta, description_md, time) 四段断言。"""
    doc = parse_detail_doc(focal, parent)
    return doc.title, doc.meta, doc.description_md, doc.time


def test_x_detail_doc_title_meta_desc() -> None:
    r = tweet_result("100", text="主推首行\n主推第二行", name="宝玉", new_user=True)
    r["legacy"]["reply_count"] = 12
    r["legacy"]["favorite_count"] = 148
    r["legacy"]["created_at"] = "Wed Oct 15 08:23:11 +0000 2025"
    title, meta, desc, time = _detail_parts(r, None)
    assert title == "主推首行"  # 标题取正文首行
    assert meta == "宝玉 @user · 12 回复 · 148 赞"  # 署名不再截 8 格（名字与账号同显）
    assert desc == "主推首行\n主推第二行"  # 正文全文（含换行）
    assert _TIME_SHAPE.fullmatch(time)  # Twitter 串 -> 北京时间展示文本


def test_x_detail_doc_single_line_no_dup_desc() -> None:
    title, _meta, desc, _time = _detail_parts(tweet_result("100", text="一句话", name="宝玉", new_user=True), None)
    assert title == "一句话"
    assert desc == ""  # 单行短文与标题重复，正文不再二刷


def test_x_detail_doc_media_only() -> None:
    # 纯媒体推文：标题回退计数记号，正文就是媒体地址（知乎同形，贴浏览器直看）
    r = tweet_result(
        "100",
        text="",
        media=[
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/a.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/b.jpg"},
        ],
        new_user=True,
    )
    title, _meta, desc, _time = _detail_parts(r, None)
    assert title == "[图片 2]"  # 标题回退仍是计数记号（扫描面词汇）
    assert desc == "[图片](https://pbs.twimg.com/media/a.jpg) [图片](https://pbs.twimg.com/media/b.jpg)"


def test_x_detail_media_no_url_falls_back_to_marker() -> None:
    # 残卡无地址：退纯记号，不硬凑
    r = tweet_result("100", text="", media=[{"type": "photo"}], new_user=True)
    title, _meta, desc, _time = _detail_parts(r, None)
    assert title == "[图片 1]"
    assert desc == "[图片]"


def test_x_media_links_video_best_variant() -> None:
    # 视频取最高码率 mp4（m3u8 是流清单不是播放地址，仅无 mp4 时兜底）
    video = {
        "type": "video",
        "video_info": {
            "variants": [
                {"content_type": "application/x-mpeg-url", "url": "https://video.twimg.com/x.m3u8"},
                {"content_type": "video/mp4", "bitrate": 632000, "url": "https://video.twimg.com/lo.mp4"},
                {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://video.twimg.com/hi.mp4"},
            ]
        },
    }
    r = tweet_result("100", text="看视频", media=[video], new_user=True)
    title, _meta, desc, _time = _detail_parts(r, None)
    assert title == "看视频"  # 有正文标题即正文首行（计数记号只在无正文时回退）
    assert desc == "看视频 [视频](https://video.twimg.com/hi.mp4)"


def test_x_detail_doc_prepends_parent_quote() -> None:
    # 回复型推文：原推引用块放在正文上方，焦点正文开头的 @ 提及已藏
    parent = tweet_result("200", text="原推正文", name="原作者", new_user=True)
    reply = tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    reply["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "（5 个码元）
    title, _meta, desc, _time = _detail_parts(reply, parent)
    assert title == "我的回复"
    assert desc.startswith("> **原推** · 原作者 @user：原推正文")
    assert "我的回复" in desc
    assert desc.index("原推") < desc.index("我的回复")  # 原推在上、正文在下


def test_x_detail_doc_appends_quoted_block_below_body() -> None:
    # 引用推文详情：被引块放正文下方（与 X 网页阅读序一致），标注被引作者
    focal = tweet_result("100", text="我的评论", name="评论者", new_user=True)
    focal["quoted_status_result"] = {"result": tweet_result("300", text="被引正文", name="被引者", handle="q", new_user=True)}
    title, _meta, desc, _time = _detail_parts(focal, None)
    assert title == "我的评论"
    assert "> **被引推文** · 被引者 @q：被引正文" in desc
    assert desc.index("我的评论") < desc.index("被引推文")  # 正文在上、被引块在下


def test_x_detail_doc_parent_above_body_and_quote_below() -> None:
    # 回复 + 引用同推（对某条引用推文的回复）：原推块在上、正文居中、被引块在下
    parent = tweet_result("200", text="原推正文", name="原作者", new_user=True)
    focal = tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    focal["legacy"]["in_reply_to_status_id_str"] = "200"
    focal["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "
    focal["quoted_status_result"] = {"result": tweet_result("300", text="被引正文", name="被引者", new_user=True)}
    _title, _meta, desc, _time = _detail_parts(focal, parent)
    assert desc.index("原推") < desc.index("我的回复") < desc.index("被引推文")


def test_x_reply_answer_votes_and_markers() -> None:
    r = tweet_result("101", text="回复\n两行", media=[{"type": "video"}], new_user=True)
    r["legacy"]["favorite_count"] = 3
    r["legacy"]["created_at"] = "Wed Oct 15 08:23:11 +0000 2025"
    a = parse_reply(r)
    assert a is not None
    assert a.author == "作者 @user"  # 楼层头同样署名「名字 @handle」
    assert a.votes == "赞 3"
    assert a.content_md == "回复\n两行 [视频]"  # 换行保留；残卡无地址退纯记号


def test_x_reply_answer_media_link() -> None:
    # 楼层媒体附直接地址（与详情正文、知乎内联同形）
    r = tweet_result(
        "101", text="回复", media=[{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/c.jpg"}], new_user=True
    )
    a = parse_reply(r)
    assert a is not None
    assert a.content_md == "回复 [图片](https://pbs.twimg.com/media/c.jpg)"


def test_x_reply_answer_quote_snippet() -> None:
    # 回复楼层同样缀引用片段（回复可以是引用推文），与列表同词汇
    r = tweet_result("101", text="这条回复带引用", new_user=True)
    r["quoted_status_result"] = {"result": tweet_result("300", text="被引内容", handle="who", new_user=True)}
    a = parse_reply(r)
    assert a is not None
    assert a.content_md == "这条回复带引用 ▸ 引用 作者 @who：被引内容"


def test_x_reply_strips_leading_mentions() -> None:
    # 回复开头的 @ 提及是噪音（X 自己也藏）：display_text_range 按码元切掉前缀
    r = tweet_result("101", text="@楼主 回复正文", new_user=True)
    r["legacy"]["display_text_range"] = [4, 9]  # "@楼主 " 4 个 UTF-16 码元
    a = parse_reply(r)
    assert a is not None
    assert a.content_md == "回复正文"


def test_x_reply_mention_strip_emoji_safe() -> None:
    # 索引按 UTF-16 码元计（表情是代理对占 2）：转码后切，emoji 不偏移
    r = tweet_result("101", text="@a 😅正文", new_user=True)
    r["legacy"]["display_text_range"] = [3, 7]  # "@a " 3 码元；😅 2 码元 + 正文 2
    a = parse_reply(r)
    assert a is not None
    assert a.content_md == "😅正文"


def test_x_reply_answer_noise_dropped() -> None:
    assert parse_reply(tweet_result("", text="无 id", new_user=True)) is None
    r = tweet_result("101", text="无作者", new_user=True)
    del r["core"]
    assert parse_reply(r) is None


# ---- Grok 自动译文（TweetDetail 响应内嵌，外文推才有，服务端闸门）----


def _grok(r: dict, translation: str = "", source: str = "en", available: bool = True) -> None:
    """给推文 result 挂 grok_translated_post_with_availability（响应内嵌字段形态）。"""
    r["grok_translated_post_with_availability"] = {
        "is_available": available,
        "data": {
            "source_language": source,
            "destination_language": "zh",
            "translation": translation,
            "entities": {"hashtags": [], "symbols": [], "urls": [], "user_mentions": []},
        },
    }


def test_x_grok_translation_block_after_body() -> None:
    # 详情：译文块接在正文之后；标题保持原文（扫描面不翻译）
    r = tweet_result("100", text="Hello\nsecond line", new_user=True)
    _grok(r, translation="你好\n第二行")
    title, _meta, desc, _time = _detail_parts(r, None)
    assert title == "Hello"
    assert desc == "Hello\nsecond line\n\n> **译文**（英语）：你好 第二行"  # 换行压单段；语种括注


def test_x_grok_translation_gates() -> None:
    # is_available 关 / 译文空 = 无译文不产块；单行短文维持「不二刷」折叠
    r = tweet_result("100", text="Hello", new_user=True)
    _grok(r, translation="你好", available=False)
    _title, _meta, desc, _time = _detail_parts(r, None)
    assert desc == ""
    r2 = tweet_result("100", text="Hello", new_user=True)
    _grok(r2, translation="   ")
    _title, _meta, desc2, _time = _detail_parts(r2, None)
    assert desc2 == ""


def test_x_grok_translation_lang_name_and_fallback() -> None:
    r = tweet_result("100", text="こんにちは", new_user=True)
    _grok(r, translation="你好", source="ja")
    _title, _meta, desc, _time = _detail_parts(r, None)
    assert "**译文**（日语）：" in desc  # 语种码 -> 中文名括注
    r2 = tweet_result("100", text="?", new_user=True)
    _grok(r2, translation="…", source="qnt")
    _title, _meta, desc2, _time = _detail_parts(r2, None)
    assert "**译文**（qnt）：" in desc2  # 查无此码退原码


def test_x_grok_translation_expands_tco_by_own_entities() -> None:
    # 译文自带 entities（坐标按译文文本计）：t.co 短链展开 display_url，与正文同规则
    r = tweet_result("100", text="See this", new_user=True)
    _grok(r, translation="看这个 https://t.co/abc")
    r["grok_translated_post_with_availability"]["data"]["entities"]["urls"] = [
        {"indices": [4, 20], "display_url": "example.com/x", "url": "https://t.co/abc"}
    ]
    _title, _meta, desc, _time = _detail_parts(r, None)
    assert "> **译文**（英语）：看这个 example.com/x" in desc


def test_x_reply_answer_translation_after_media() -> None:
    # 楼层序：正文 → 媒体地址 → 引用片段 → 译文块缀尾（译文是 blockquote，
    # 片段接其后会粘进引用行里像译文的一部分）
    r = tweet_result(
        "101",
        text="Nice shot",
        media=[{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/c.jpg"}],
        new_user=True,
    )
    _grok(r, translation="拍得好")
    r["quoted_status_result"] = {"result": tweet_result("300", text="被引", handle="who", new_user=True)}
    a = parse_reply(r)
    assert a is not None
    assert a.content_md == (
        "Nice shot [图片](https://pbs.twimg.com/media/c.jpg) ▸ 引用 作者 @who：被引"
        "\n\n> **译文**（英语）：拍得好"
    )


def test_x_quote_context_translation_second_paragraph() -> None:
    # 引用块内译文作第二段（> 空行分隔）：原推/被引是外文时同样带译文
    parent = tweet_result("200", text="Parent post", name="原作者", new_user=True)
    _grok(parent, translation="原推译文")
    reply = tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    reply["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "
    _title, _meta, desc, _time = _detail_parts(reply, parent)
    assert "> **原推** · 原作者 @user：Parent post\n>\n> **译文**（英语）：原推译文" in desc


def test_x_tweet_author_new_user_core_shape() -> None:
    # 列表路径同样认新用户形态（HomeLatestTimeline 若迁移，列表不炸）
    it = parse_tweet(tweet_result("1001", text="推文", new_user=True))
    assert it is not None
    assert it.meta == "作者 @user"
    assert it.url == "https://x.com/user/status/1001"



def test_x_timeline_unwraps_visibility_results() -> None:
    """时间线里的受限推文（TweetWithVisibilityResults 套 .tweet）同样上屏——
    拆包不限于详情路径（同族端点同形态，不拆会被当残卡静默丢弃）。"""
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": tweet_result("9001")}
    entries = [entry(entry_id="tweet-9001", item_content=item_content(wrapped))]
    it = parse_tweet(next(parse_tweet_results(entries)))
    assert it is not None and it.id == "status/9001"


def test_x_counts_fold_wan_like_zhihu() -> None:
    """X 的大计数与知乎同形折「万」（core/display 统一）：热门推文不再是裸 45000。"""
    r = tweet_result("101", text="回复", new_user=True)
    r["legacy"]["favorite_count"] = 45000
    assert parse_reply(r).votes == "赞 4.5 万"

    focal = tweet_result("100", text="主推", new_user=True)
    focal["legacy"]["reply_count"] = 12345
    focal["legacy"]["favorite_count"] = 45678
    _title, meta, _desc, _time = _detail_parts(focal, None)
    assert meta == "作者 @user · 1.2 万 回复 · 4.6 万 赞"
