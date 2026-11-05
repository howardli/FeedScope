"""知乎 / X 两个源的纯函数解析器基线测试：手写最小 HTML/JSON fixture，不碰网络。

覆盖各源的 dict/HTML -> FeedItem/Answer/DetailDoc 转换与清洗路径，
以及替换掉网络调用后 Loader 的翻页语义（同样不碰网络）。
"""

import asyncio
import re

import pytest

from feedscope.core.models import FeedItem
from feedscope.core.source import SourceError, dedup_append, merge_feeds
from feedscope.sources.x.fetch import (
    XFeedLoader,
    XTweetDetailLoader,
)
from feedscope.sources.x.parse import (
    _detail_doc,
    _detail_entries_and_cursor,
    _entries_and_cursor,
    _focal_and_replies,
    _iter_tweet_results,
    _reply_answer,
    _tweet_from_result,
)
from feedscope.sources.zhihu.parse import (
    _api_question_doc,
    _feed_api_item,
    _feeds_answers,
    _hot_api_items,
)
from feedscope.sources.zhihu.parse import _md as _md_zhihu
from feedscope.ui.wrap import display_width

_TIME_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

# ---- 知乎 ----


def test_zhihu_hot_api_items() -> None:
    """热榜 API（2026-11 实测响应形态裁剪）：url 重写为 www 问题页，meta 放热度。"""
    data = {
        "data": [
            {
                "card_id": "Q_2100108531160687738",
                "detail_text": "2606 万热度",
                "target": {
                    "id": 2100108531160687738,
                    "type": "question",
                    "title": " 标题\u200b ",
                    "url": "https://api.zhihu.com/questions/2100108531160687738",  # 内部形态，浏览器不可达
                },
            },
            # id 精度丢失 case：target.id 是未加引号大整数，浏览器 r.json() 解析
            # 会四舍五入（…1699522 -> …1699500）——id 必须取自字符串形态的 url
            {
                "detail_text": "2276 万热度",
                "target": {
                    "id": 2098799350503699500,  # 浏览器解析丢精度后的坏 id
                    "type": "question",
                    "title": "精度题",
                    "url": "https://api.zhihu.com/questions/2098799350503699522",  # 字符串，精确
                },
            },
            {"detail_text": "x", "target": {"id": 1, "type": "article", "title": "非问题"}},  # 非问题卡丢弃
            {"detail_text": "y", "target": {"type": "question", "title": "缺 id"}},  # 缺 id 丢弃
        ],
        "display_num": 15,
    }
    items = _hot_api_items(data)
    assert len(items) == 2
    it = items[0]
    assert it.id == "question/2100108531160687738"
    assert it.kind == "question"
    assert it.title == "标题"  # 零宽字符被清掉
    assert items[1].id == "question/2098799350503699522"  # 从 url 提取，不吃 id 的精度丢失
    assert it.meta == "2606 万热度"
    assert it.url == "https://www.zhihu.com/question/2100108531160687738"  # api.zhihu.com 重写


def test_zhihu_feed_api_item_variants() -> None:
    """推荐/关注 feed API 条目（实测形态裁剪）：answer/question 卡保留，其余丢弃。"""
    # answer 卡（关注 MEMBER_VOTEUP_ANSWER / 推荐 TOPIC_ACKNOWLEDGED_* 同形）
    a = _feed_api_item(
        {
            "verb": "MEMBER_VOTEUP_ANSWER",
            "target": {
                "id": 2011798106161840829,
                "type": "answer",
                "author": {"name": "作者"},
                "question": {"id": 1951716962645288920, "title": "问题标题"},
            },
        }
    )
    assert a is not None
    assert a.id == "question/1951716962645288920"  # 去重键用所属问题（与 DOM 版一致）
    assert a.url == "https://www.zhihu.com/question/1951716962645288920"
    assert a.meta == "作者"  # 元信息列放回答作者（与 DOM 版 data-zop 同源信息）
    # question 卡（关注 MEMBER_ASK_QUESTION）
    q = _feed_api_item(
        {
            "verb": "MEMBER_ASK_QUESTION",
            "target": {"id": 2100191570838434341, "type": "question", "title": "提问", "author": {"name": "提问人"}},
        }
    )
    assert q is not None and q.title == "提问" and q.id == "question/2100191570838434341"
    # 丢弃形态：feed_group 分组卡（无 target）、文章、缺所属问题的 answer
    assert _feed_api_item({"type": "feed_group", "verb": None}) is None
    assert _feed_api_item({"target": {"id": 1, "type": "article", "title": "文章"}}) is None
    assert _feed_api_item({"target": {"type": "answer", "author": {"name": "a"}}}) is None


def test_zhihu_feeds_answers() -> None:
    """回答 feeds API（2026-11 实测形态裁剪）-> Answer：格式对齐 DOM 首批，非回答卡跳过。"""
    from feedscope.core.timefmt import fmt_unix

    data = {
        "data": [
            {
                "target": {
                    "id": 789,
                    "type": "answer",
                    "author": {"name": "答主甲"},
                    "voteup_count": 10584,
                    "comment_count": 33,
                    "created_time": 1793500000,
                    "content": "<p>答</p>",
                    "question": {"id": 123},
                }
            },
            {"target": {"id": 1, "type": "article", "title": "非回答卡"}},  # 罕见混入，跳过
        ],
        "paging": {"is_end": False, "next": "n"},
    }
    ans = _feeds_answers(data, "123")
    assert len(ans) == 1
    a = ans[0]
    assert a.author == "答主甲"
    assert a.votes == "赞 1.1 万"  # 楼层赞数用词与 X/评论层统一（「赞 N」）；过万折「万」
    assert a.comment_count == "33 条评论"
    assert a.url == "https://www.zhihu.com/question/123/answer/789"
    assert a.time == fmt_unix(1793500000)  # 裸北京时间（与 X 楼层同形，不带「发布于」前缀）
    assert a.content_md == "答"


def test_zhihu_feeds_answer_zero_votes_empty() -> None:
    """0 赞回退空文本（站方按钮无数字，与评论层「无赞为空」一致）。"""
    zero = _feeds_answers(
        {"data": [{"target": {"id": 1, "type": "answer", "author": {"name": "a"},
                              "voteup_count": 0, "comment_count": 0, "created_time": 0,
                              "content": "<p>x</p>", "question": {"id": 9}}}]}, "9"
    )
    assert zero[0].votes == "" and zero[0].comment_count == "" and zero[0].time == ""


def test_zhihu_wan_format() -> None:
    """计数折「万」走 core/display（见 test_display.py）；这里验证 feeds 接入同形。"""
    ans = _feeds_answers(
        {"data": [
            {"target": {"type": "answer", "id": "1", "voteup_count": 10584, "comment_count": 0,
                        "author": {"name": "答主"}, "content": "", "created_time": 0}}
        ]},
        "123",
    )
    assert ans[0].votes == "赞 1.1 万"


def test_x_counts_fold_wan_like_zhihu() -> None:
    """X 的大计数与知乎同形折「万」（core/display 统一）：热门推文不再是裸 45000。"""
    r = _tweet_result("101", text="回复", new_user=True)
    r["legacy"]["favorite_count"] = 45000
    assert _reply_answer(r).votes == "赞 4.5 万"

    focal = _tweet_result("100", text="主推", new_user=True)
    focal["legacy"]["reply_count"] = 12345
    focal["legacy"]["favorite_count"] = 45678
    _title, meta, _desc, _time = _detail_doc(focal, None)
    assert meta == "作者 @user · 1.2 万 回复 · 4.6 万 赞"


def test_zhihu_question_doc_asker_and_time() -> None:
    """问题元信息 API（v4/questions?include=…）-> DetailDoc：计数拼 meta 行，unix 秒转北京时间。"""
    from feedscope.core.models import Answer

    doc = _api_question_doc(
        {
            "title": "问题\u200b",
            "detail": "<p>描述</p>",
            "author": {"name": "阿财"},
            "created": 1735689600,
            "answer_count": 66,
            "visit_count": 12340000,
        },
        [Answer(author="答主", votes="赞 1", content_md="答")],
    )
    assert doc.title == "问题"
    assert doc.meta == "阿财 提问 · 66 个回答 · 1234 万浏览"  # 提问人并入 meta 头部
    assert doc.time == "2025-01-01 08:00"  # 裸北京时间（与 X 详情同形，不带「提问于」前缀）
    assert "描述" in doc.description_md
    assert doc.answers[0].author == "答主"


def test_zhihu_question_doc_missing_fields_not_forced() -> None:
    # 计数/时间缺失：不硬凑，meta 只保留提问人
    doc = _api_question_doc({"title": "问题", "author": {"name": "阿财"}}, [])
    assert doc.meta == "阿财 提问"
    assert doc.time == ""
    # 提问人缺失回退匿名；小浏览量不折万
    doc2 = _api_question_doc({"title": "问题", "visit_count": 999}, [])
    assert doc2.meta.startswith("匿名用户 提问") and doc2.meta.endswith("999 浏览")


def test_zhihu_md_marks_image_and_video() -> None:
    out = _md_zhihu('<p>你好</p><img src="https://a/1.png"><video><source src="https://v/1.mp4"></video>')
    assert "你好" in out
    assert "[图片](https://a/1.png)" in out  # 可点击的 markdown 链接（同 RSS）
    assert "[视频](https://v/1.mp4)" in out


def test_zhihu_md_merges_adjacent_same_image() -> None:
    out = _md_zhihu('<img src="https://a/1.png"><img src="https://a/1.png">')
    assert out == "[图片](https://a/1.png)"  # 懒加载双标记只留一张


# 2026-11 实测：content 里没有 <video> 标签，视频是 a.video-box 卡片（包封面缩略图）
_VIDEO_BOX_HTML = (
    '<p>看视频</p><a class="video-box" '
    'href="https://link.zhihu.com/?target=https%3A//www.zhihu.com/video/2100583640916926727" '
    'target="_blank" data-video-playable="true" data-lens-id="2100583640916926727" '
    'data-poster="https://picx.zhimg.com/poster.jpg">'
    '<img class="thumbnail" src="https://picx.zhimg.com/v2-thumb_720w.jpg"/></a>'
)


def test_zhihu_md_video_box_becomes_video_link() -> None:
    out = _md_zhihu(_VIDEO_BOX_HTML)
    assert "[视频](https://www.zhihu.com/video/2100583640916926727)" in out
    # 封面缩略图与 link.zhihu.com 跳板都不再见（此前会搅成嵌套畸形链接）
    assert "thumbnail" not in out and "link.zhihu.com" not in out and "poster" not in out


def test_zhihu_md_video_box_without_lens_id_decodes_jump_href() -> None:
    html = (
        '<a class="video-box" href="https://link.zhihu.com/?target=https%3A//www.zhihu.com/video/123">'
        '<img class="thumbnail" src="https://p/x.jpg"/></a>'
    )
    assert "[视频](https://www.zhihu.com/video/123)" in _md_zhihu(html)


def test_zhihu_md_strips_thumbnail_proxy_prefix() -> None:
    out = _md_zhihu('<img src="https://pica.zhimg.com/50/v2-abc123_720w.jpg?source=1def8aca">')
    assert out == "[图片](https://pica.zhimg.com/v2-abc123_720w.jpg?source=1def8aca)"  # /50/ 剥掉


def test_zhihu_md_sticker_becomes_alt_text() -> None:
    """表情贴纸不是内容图：换成 alt 文本而非 [图片]+地址。"""
    out = _md_zhihu('好激动<img src="https://pic/a.png" class="sticker" alt="[大哭]">')
    assert "[大哭]" in out
    assert "[图片]" not in out


def test_zhihu_api_comment() -> None:
    """comment_v5 JSON -> Comment：赞数/绝对时间/子回复/more_replies/删除过滤。"""
    from feedscope.core.timefmt import fmt_unix
    from feedscope.sources.zhihu.parse import _api_comments

    data = {
        "data": [
            {
                "id": 11608,
                "author": {"name": "李四"},
                "content": "写得真好[赞]",
                "created_time": 1793491084,
                "like_count": 45,
                "child_comment_count": 3,
                "child_comments": [
                    {"id": 11609, "author": {"name": "王五"}, "content": "回复 @李四：确实",
                     "created_time": 1793491704, "like_count": 0, "child_comments": []}
                ],
            },
            {"id": 11699, "is_delete": True, "author": {"name": "已删"}, "content": "x"},  # 已删过滤
            {"id": 11698, "is_visible_only_to_myself": True, "author": {"name": "仅自见"}, "content": "y"},  # 仅自见过滤
        ],
        "paging": {"is_end": False, "next": "https://…/root_comment?offset=x"},
    }
    (top,) = _api_comments(data)
    assert top.author == "李四"
    assert top.votes == "赞 45"
    assert top.content_md == "写得真好[赞]"
    assert top.time == fmt_unix(1793491084)  # unix 秒 -> 北京时间
    assert top.raw_id == "11608"
    sub = top.replies[0]
    assert sub.author == "王五"
    assert sub.votes == ""  # 0 赞为空
    assert sub.content_md == "回复 @李四：确实"
    assert sub.replies == []
    assert top.more_replies == 2  # 3 条子回复 - 带出 1 条
    assert _api_comments({"data": []}) == []  # 空页不炸


# ---- merge_feeds：「全部」聚合的共享编排（core.source，RSS/Telegram 共用）----


def _m_item(id_: str, url: str = "", published: float = 0.0) -> FeedItem:
    return FeedItem(source="rss", id=id_, title=id_, url=url, extra={"published": published})


def test_merge_feeds_sorts_desc_and_isolates_failures() -> None:
    """倒序合并（稳定排序：同键保持订阅书写顺序）+ 单订阅失败隔离。"""

    async def one(feed) -> list[FeedItem]:
        if feed == "dead":
            raise RuntimeError("boom")
        if feed == "empty":
            return []  # 空订阅不是失败
        return [_m_item(f"{feed}-a", published=2.0), _m_item(f"{feed}-b", published=1.0)]

    merged, errors = asyncio.run(
        merge_feeds(
            ["dead", "first", "empty", "second"],
            one,
            error_label=lambda f: f"订阅{f}",
            sort_key=lambda it: float(it.extra.get("published") or 0.0),
        )
    )
    assert [it.id for it in merged] == ["first-a", "second-a", "first-b", "second-b"]  # 2.0 > 1.0，同键按书写序
    assert errors == ["订阅dead：boom"]  # 死源只损失自己


def test_merge_feeds_dedup_urls_keeps_first_written() -> None:
    """dedup_urls：同一 URL 被两个订阅覆盖只留书写顺序靠前的一份。"""

    async def one(feed) -> list[FeedItem]:
        return [_m_item(f"{feed}-x", url="https://same.example/a")] if feed == "one" else [
            _m_item(f"{feed}-x", url="https://same.example/a"),
            _m_item(f"{feed}-y", url="https://other.example/b"),
        ]

    merged, errors = asyncio.run(
        merge_feeds(["one", "two"], one, error_label=str, sort_key=lambda it: 0.0, dedup_urls=True)
    )
    assert [it.id for it in merged] == ["one-x", "two-y"]  # two 的重复 URL 被剔
    assert errors == []


def test_dedup_append_drops_none_and_repeats() -> None:
    a = FeedItem(source="zhihu", id="x", title="t", url="u")
    b = FeedItem(source="zhihu", id="x", title="t2", url="u2")
    c = FeedItem(source="zhihu", id="y", title="t3", url="u3")
    items: list[FeedItem] = [a]
    seen = {a.id}
    new = dedup_append(items, seen, [a, None, b, c])
    assert items == [a, c]  # 重复/噪音不追加，新 id 追加
    assert new == [c]  # 返回值 = 新增部分（翻页 UI 只挂它）


# ---- X（Twitter，GraphQL URT -> FeedItem）----


def _tweet_result(
    rest_id: str,
    *,
    text: str = "推文",
    handle: str = "user",
    name: str = "作者",
    media: list[dict] | None = None,
    note: str | None = None,
    new_user: bool = False,
) -> dict:
    """一条 URT 推文 result（fetch 与 parse 的合同数据）。

    new_user=True 用 2026 新用户形态（TweetDetail 已迁移：name/screen_name 在
    user.core；HomeLatestTimeline 仍是旧 legacy 形态）。
    """
    legacy: dict = {"full_text": text, "entities": {"media": media or []}}
    if new_user:
        user_result: dict = {"core": {"screen_name": handle, "name": name}}
    else:
        user_result = {"legacy": {"screen_name": handle, "name": name}}
    r: dict = {
        "rest_id": rest_id,
        "legacy": legacy,
        "core": {"user_results": {"result": user_result}},
    }
    if note is not None:
        r["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    return r


def _entry(
    *,
    entry_id: str,
    item_content: dict | None = None,
    cursor_value: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """构造一条 URT entry：item / cursor / module 三选一。"""
    if cursor_value is not None:
        return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineCursor", "value": cursor_value}}
    if items is not None:
        return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineModule", "items": items}}
    return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineItem", "itemContent": item_content or {}}}


def _item_content(result: dict | None = None, promoted: bool = False) -> dict:
    it: dict = {}
    if promoted:
        it["promotedMetadata"] = {"adMetadataContainer": {}}
    if result is not None:
        it["tweet_results"] = {"result": result}
    return it


def test_x_tweet_basic() -> None:
    it = _tweet_from_result(_tweet_result("1001", text="  Hello\u200b world  ", handle="user1", name="宝\u200b玉"))
    assert it is not None
    assert it.source == "x"
    assert it.id == "status/1001"
    assert it.url == "https://x.com/user1/status/1001"
    assert it.kind == "tweet"
    assert it.title == "Hello world"  # 零宽字符与首尾空白被清
    assert it.meta == "宝玉 @user1"  # 署名「名字 @handle」（名字与账号同显），零宽清除


def test_x_tweet_meta_falls_back_to_handle() -> None:
    it = _tweet_from_result(_tweet_result("1001", name=""))
    assert it is not None
    assert it.meta == "@user"  # 作者名缺失时只剩账号（带 @，署名词汇一致）


def test_x_tweet_meta_keeps_full_name() -> None:
    it = _tweet_from_result(_tweet_result("1001", name="一个特别长的推主昵称超过八字"))
    assert it is not None
    assert it.meta == "一个特别长的推主昵称超过八字 @user"  # 全名 + 账号；列宽截断统一在 UI（meta_col_text）


def test_x_tweet_note_overrides_full_text() -> None:
    it = _tweet_from_result(_tweet_result("1001", text="预览截断…", note="完整长推文"))
    assert it is not None
    assert it.title == "完整长推文"  # 长推取 note_tweet 全文，不是 legacy 预览


def test_x_tweet_media_markers_and_newline_collapse() -> None:
    media = [{"type": "photo"}, {"type": "photo"}, {"type": "video"}]
    it = _tweet_from_result(_tweet_result("1001", text="第一行\n\n第二行", media=media))
    assert it is not None
    assert it.title == "第一行 第二行 [图片 2] [视频]"  # 换行压成空格；媒体换记号缀尾


def test_x_tweet_video_only() -> None:
    it = _tweet_from_result(_tweet_result("1001", text="", media=[{"type": "animated_gif"}]))
    assert it is not None
    assert it.title == "[视频]"  # 纯媒体推文以记号为题，不是噪音


def test_x_tweet_normal_text_not_truncated() -> None:
    # 普通推文（无 note_tweet）全文上屏：超过旧截断上限也不截
    it = _tweet_from_result(_tweet_result("1001", text="字" * 100 + "a" * 100))  # 300 显示格
    assert it is not None
    assert it.title == "字" * 100 + "a" * 100


def test_x_tweet_long_form_truncated_by_display_width() -> None:
    # 长文（蓝 V note_tweet，可达几千字）才截断：280 格上限 + 省略号
    it = _tweet_from_result(_tweet_result("1001", text="预览…", note="字" * 100 + "a" * 100))
    assert it is not None
    assert display_width(it.title) <= 281
    assert it.title.startswith("字")
    assert it.title.endswith("…")


def test_x_tweet_noise_dropped() -> None:
    assert _tweet_from_result({}) is None  # 无 rest_id 的残卡
    assert _tweet_from_result(_tweet_result("", text="x")) is None  # 空 id
    assert _tweet_from_result(_tweet_result("1001", text="")) is None  # 正文与媒体全无


def test_x_tweet_retweet_reads_inner_not_wrapper_preview() -> None:
    # 转发读内层原推（外壳 full_text 只是 X 截断的 140 字预览，媒体/长文都在内层，
    # 2026-11 实弹抓包确认）：前缀「RT 原作者 @handle: 」，元信息列仍是转发者；
    # extra 带上原推 id，详情据此跳原推会话（RT 本身没有回复串）
    r = _tweet_result("1001", text="RT @orig: 原文内容… https://t.co/leak")
    inner = _tweet_result("2002", text="原文内容", handle="orig", name="原作者", media=[{"type": "video"}])
    r["legacy"]["retweeted_status_result"] = {"result": inner}
    it = _tweet_from_result(r)
    assert it is not None
    assert it.id == "status/1001"  # 列表条目仍是这条 RT
    assert it.url == "https://x.com/user/status/1001"
    assert it.title == "RT 原作者 @orig: 原文内容 [视频]"  # 内层全文 + 内层媒体记号；预览省略号与 t.co 都不见
    assert it.meta == "作者 @user"  # 元信息列是转发者（你关注的人），原作者在前缀里
    assert it.extra["conversation_id"] == "2002"


def test_x_tweet_retweet_inner_long_form_and_quote() -> None:
    # 转发的内层是长文（note_tweet）→ 列表截 280 格；内层自己还是引用推文 → 片段也从内层出
    r = _tweet_result("1001", text="RT @orig: 预览…")
    inner = _tweet_result("2002", text="预览…", handle="orig", note="字" * 100 + "a" * 100)
    inner["quoted_status_result"] = {"result": _tweet_result("3003", text="被引推文", handle="q")}
    r["legacy"]["retweeted_status_result"] = {"result": inner}
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title.startswith("RT 作者 @orig: " + "字" * 100)  # 长文取 note 全文再截，不是外壳预览
    assert "▸ 引用 作者 @q：被引推文" in it.title


def test_x_tweet_media_tco_clipped_in_list() -> None:
    # 带媒体推文的 full_text 结尾是 t.co 短链：列表同详情一样按 display_text_range 裁掉
    r = _tweet_result("1001", text="看这个 https://t.co/abc123", media=[{"type": "photo"}])
    r["legacy"]["display_text_range"] = [0, 3]  # 「看这个」3 个码元，尾链不在显示区
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "看这个 [图片 1]"  # raw 短链不漏，媒体记号缀尾


def test_x_tweet_media_tco_inside_display_range_still_stripped() -> None:
    # 实测个别推文把媒体尾链划进 display_text_range（索引不可靠）：按结尾匹配兜底剥
    r = _tweet_result(
        "1001", text="正文 https://t.co/m", media=[{"type": "photo", "url": "https://t.co/m"}]
    )
    r["legacy"]["display_text_range"] = [0, 17]  # 尾链 14 码元被划进显示区（异常形态）
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "正文 [图片 1]"  # 记号代偿，raw 短链不漏


def test_x_tweet_inline_url_expanded() -> None:
    # 正文内联 t.co 短链（用户分享的网址，在显示区内）换成 X 网页同款 display_url
    text = "看好这篇 wapo.st 短评 https://t.co/abc123"
    r = _tweet_result("1001", text=text)
    r["legacy"]["entities"]["urls"] = [
        {"url": "https://t.co/abc123", "display_url": "wapo.st/xyz", "indices": [16, 35]}
    ]
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "看好这篇 wapo.st 短评 wapo.st/xyz"


def test_x_tweet_inline_url_out_of_range_left_as_is() -> None:
    # url 实体越界（异常形态/截断残余）：不硬换，原文保留不崩
    r = _tweet_result("1001", text="短文 https://t.co/x")
    r["legacy"]["entities"]["urls"] = [
        {"url": "https://t.co/x", "display_url": "x.y/z", "indices": [500, 520]}
    ]
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "短文 https://t.co/x"


def test_x_tweet_plain_has_no_conversation_id() -> None:
    assert _tweet_from_result(_tweet_result("1001", text="普通推文")).extra == {}


def test_x_tweet_quote_snippet_appended() -> None:
    # 引用推文：被引内容不缀则完全不可见，「▸ 引用 名字 @handle：片段」缀尾（换行压空格）
    r = _tweet_result("1001", text="我的评论")
    r["quoted_status_result"] = {"result": _tweet_result("3003", text="被引\n推文", handle="who", name="被引者")}
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "我的评论 ▸ 引用 被引者 @who：被引 推文"


def test_x_tweet_quote_tombstone_no_snippet() -> None:
    # 被引已删（TweetTombstone 无 legacy）：宁缺毋滥，不缀片段
    r = _tweet_result("1001", text="我的评论")
    r["quoted_status_result"] = {"result": {"__typename": "TweetTombstone", "text": "…"}}
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "我的评论"


def test_x_tweet_quote_only_survives() -> None:
    # 不带评论的引用推文（正文空）：以片段为题，不当残卡丢
    r = _tweet_result("1001", text="")
    r["quoted_status_result"] = {"result": _tweet_result("3003", text="被引内容", handle="who")}
    it = _tweet_from_result(r)
    assert it is not None
    assert it.title == "▸ 引用 作者 @who：被引内容"


def test_x_iter_tweet_results_skips_promoted_and_who_to_follow() -> None:
    entries = [
        _entry(entry_id="tweet-1", item_content=_item_content(_tweet_result("1"))),
        _entry(entry_id="promoted-tweet-2", item_content=_item_content(_tweet_result("2"), promoted=True)),
        _entry(entry_id="who-to-follow-3", items=[{"item": {"itemContent": _item_content(_tweet_result("3"))}}]),
        _entry(entry_id="home-conversation-4", items=[{"item": {"itemContent": _item_content(_tweet_result("4"))}}]),
    ]
    got = [str(r["rest_id"]) for r in _iter_tweet_results(entries)]
    assert got == ["1", "4"]  # 广告、关注推荐模块丢弃，conversation 内嵌推文保留


def test_x_entries_and_cursor() -> None:
    data = {"data": {"home": {"home_timeline_urt": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [
            _entry(entry_id="cursor-top-1", cursor_value="top"),
            _entry(entry_id="tweet-1", item_content=_item_content(_tweet_result("1"))),
            _entry(entry_id="cursor-bottom-2", cursor_value="next-cursor"),
        ]},
    ]}}}}
    entries, cursor = _entries_and_cursor(data)
    assert len(entries) == 3
    assert cursor == "next-cursor"  # 只认 cursor-bottom，不认 cursor-top


def test_x_entries_and_cursor_no_cursor() -> None:
    data = {"data": {"home": {"home_timeline_urt": {"instructions": []}}}}
    entries, cursor = _entries_and_cursor(data)
    assert entries == []
    assert cursor is None


def _timeline(entries: list[dict], cursor: str | None) -> tuple[list[dict], str | None]:
    return entries, cursor


def test_x_feed_loader_dedup_and_cursor(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    async def _fake_fetch(feed: str, cursor: str | None):
        calls.append((feed, cursor))
        if cursor is None:
            return _timeline(
                [_entry(entry_id="tweet-1", item_content=_item_content(_tweet_result("1"))),
                 _entry(entry_id="tweet-2", item_content=_item_content(_tweet_result("2")))],
                "next",
            )
        return _timeline(
            [_entry(entry_id="tweet-2", item_content=_item_content(_tweet_result("2"))),
             _entry(entry_id="tweet-3", item_content=_item_content(_tweet_result("3")))],
            None,
        )

    monkeypatch.setattr("feedscope.sources.x.fetch._fetch_entries", _fake_fetch)
    loader = XFeedLoader("following")
    new = asyncio.run(loader.load_more())
    assert [it.id for it in new] == ["status/1", "status/2"]
    assert not loader.done
    new = asyncio.run(loader.load_more())
    assert [it.id for it in new] == ["status/3"]  # 重复的 2 不再追加
    assert loader.done  # 无 cursor-bottom = 真到头
    assert [it.id for it in loader.items] == ["status/1", "status/2", "status/3"]
    assert calls == [("following", None), ("following", "next")]  # feed 名随翻页传入


def test_x_feed_loader_done_stops(monkeypatch) -> None:
    async def _fake_fetch(feed: str, cursor: str | None):
        return _timeline([], None)

    monkeypatch.setattr("feedscope.sources.x.fetch._fetch_entries", _fake_fetch)
    loader = XFeedLoader("following")
    assert asyncio.run(loader.load_more()) == []
    assert loader.done
    assert asyncio.run(loader.load_more()) == []  # done 后不再请求


# ---- X 推文详情（TweetDetail：threaded_conversation_with_injections_v2）----


def _showmore_cursor(value: str) -> dict:
    """ShowMore 回复游标条目（itemContent 形态；线上主形态是 cursor-bottom 的 content.value）。"""
    return {
        "entryId": "cursor-showmorethread-0",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {"itemType": "TimelineTimelineCursor", "value": value},
        },
    }


def _conv_data(entries: list[dict], cursor: str | None = None) -> dict:
    """TweetDetail 响应 fixture（游标用线上实测的 cursor-bottom + content.value 形态）。"""
    es = entries + ([_entry(entry_id="cursor-bottom-0", cursor_value=cursor)] if cursor else [])
    return {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
        {"type": "TimelineAddEntries", "entries": es}
    ]}}}


def _wrapped(result: dict) -> dict:
    """TweetWithVisibilityResults 拆包形态：result 套一层 .tweet。"""
    return {"__typename": "TweetWithVisibilityResults", "tweet": result}


def test_x_detail_entries_and_cursor() -> None:
    entries = [
        _entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100"))),
    ]
    entries, cursor = _detail_entries_and_cursor(_conv_data(entries, cursor="more-1"))
    assert len(entries) == 2
    assert cursor == "more-1"


def test_x_detail_entries_no_cursor() -> None:
    entries, cursor = _detail_entries_and_cursor(_conv_data([], cursor=None))
    assert entries == []
    assert cursor is None


def test_x_focal_unwraps_visibility_and_dedups() -> None:
    entries = [
        _entry(entry_id="tweet-100", item_content=_item_content(_wrapped(_tweet_result("100", text="焦点", new_user=True)))),
        _entry(
            entry_id="conversationthread-1",
            items=[
                {"item": {"itemContent": _item_content(_tweet_result("101", text="回复", new_user=True))}},
                {"item": {"itemContent": _item_content(_tweet_result("101", text="重复条目", new_user=True))}},
            ],
        ),
    ]
    focal, parent, replies = _focal_and_replies(entries, "100")
    assert focal["rest_id"] == "100"  # 受限推文拆包后拿到本体
    assert parent is None  # 非回复型推文无父推
    assert [r["rest_id"] for r in replies] == ["101"]  # 同一条回复出现两次只留一次


def test_x_focal_missing_falls_back_to_first() -> None:
    entries = [_entry(entry_id="tweet-101", item_content=_item_content(_tweet_result("101", text="回复", new_user=True)))]
    focal, parent, replies = _focal_and_replies(entries, "999")  # 焦点不在（接口异常形态）
    assert focal["rest_id"] == "101"  # 保底取第一条，不空屏
    assert parent is None
    assert replies == []


def test_x_focal_splits_parent_from_replies() -> None:
    # 回复型推文：父推（原推）从回复列表拆出来单独展示
    parent = _tweet_result("200", text="原推正文", name="原作者", new_user=True)
    reply = _tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    child = _tweet_result("101", text="楼中回复", new_user=True)
    entries = [
        _entry(entry_id="tweet-200", item_content=_item_content(parent)),
        _entry(entry_id="tweet-100", item_content=_item_content(reply)),
        _entry(entry_id="conversationthread-1", items=[{"item": {"itemContent": _item_content(child)}}]),
    ]
    focal, got_parent, replies = _focal_and_replies(entries, "100")
    assert focal["rest_id"] == "100"
    assert got_parent["rest_id"] == "200"  # 原推拆出来
    assert [r["rest_id"] for r in replies] == ["101"]  # 只剩真正的回复


def test_x_detail_drops_related_tweets_module() -> None:
    # tweetdetailrelatedtweets 模块是「相关推文」推荐，不是回复，丢弃
    entries = [
        _entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100", text="焦点", new_user=True))),
        _entry(
            entry_id="tweetdetailrelatedtweets-1",
            items=[{"item": {"itemContent": _item_content(_tweet_result("999", text="相关推荐", new_user=True))}}],
        ),
        _entry(
            entry_id="conversationthread-2",
            items=[{"item": {"itemContent": _item_content(_tweet_result("101", text="真回复", new_user=True))}}],
        ),
    ]
    _focal, _parent, replies = _focal_and_replies(entries, "100")
    assert [r["rest_id"] for r in replies] == ["101"]


def test_x_detail_cursor_itemcontent_form() -> None:
    # ShowMore 线索形态的游标（itemContent 里带 value）也要认
    data = {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [_showmore_cursor("abc")]}
    ]}}}
    _entries, cursor = _detail_entries_and_cursor(data)
    assert cursor == "abc"


def test_x_detail_doc_title_meta_desc() -> None:
    r = _tweet_result("100", text="主推首行\n主推第二行", name="宝玉", new_user=True)
    r["legacy"]["reply_count"] = 12
    r["legacy"]["favorite_count"] = 148
    r["legacy"]["created_at"] = "Wed Oct 15 08:23:11 +0000 2025"
    title, meta, desc, time = _detail_doc(r, None)
    assert title == "主推首行"  # 标题取正文首行
    assert meta == "宝玉 @user · 12 回复 · 148 赞"  # 署名不再截 8 格（名字与账号同显）
    assert desc == "主推首行\n主推第二行"  # 正文全文（含换行）
    assert _TIME_SHAPE.fullmatch(time)  # Twitter 串 -> 北京时间展示文本


def test_x_detail_doc_single_line_no_dup_desc() -> None:
    title, _meta, desc, _time = _detail_doc(_tweet_result("100", text="一句话", name="宝玉", new_user=True), None)
    assert title == "一句话"
    assert desc == ""  # 单行短文与标题重复，正文不再二刷


def test_x_detail_doc_media_only() -> None:
    # 纯媒体推文：标题回退计数记号，正文就是媒体地址（知乎同形，贴浏览器直看）
    r = _tweet_result(
        "100",
        text="",
        media=[
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/a.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/b.jpg"},
        ],
        new_user=True,
    )
    title, _meta, desc, _time = _detail_doc(r, None)
    assert title == "[图片 2]"  # 标题回退仍是计数记号（扫描面词汇）
    assert desc == "[图片](https://pbs.twimg.com/media/a.jpg) [图片](https://pbs.twimg.com/media/b.jpg)"


def test_x_detail_media_no_url_falls_back_to_marker() -> None:
    # 残卡无地址：退纯记号，不硬凑
    r = _tweet_result("100", text="", media=[{"type": "photo"}], new_user=True)
    title, _meta, desc, _time = _detail_doc(r, None)
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
    r = _tweet_result("100", text="看视频", media=[video], new_user=True)
    title, _meta, desc, _time = _detail_doc(r, None)
    assert title == "看视频"  # 有正文标题即正文首行（计数记号只在无正文时回退）
    assert desc == "看视频 [视频](https://video.twimg.com/hi.mp4)"


def test_x_detail_doc_prepends_parent_quote() -> None:
    # 回复型推文：原推引用块放在正文上方，焦点正文开头的 @ 提及已藏
    parent = _tweet_result("200", text="原推正文", name="原作者", new_user=True)
    reply = _tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    reply["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "（5 个码元）
    title, _meta, desc, _time = _detail_doc(reply, parent)
    assert title == "我的回复"
    assert desc.startswith("> **原推** · 原作者 @user：原推正文")
    assert "我的回复" in desc
    assert desc.index("原推") < desc.index("我的回复")  # 原推在上、正文在下


def test_x_detail_doc_appends_quoted_block_below_body() -> None:
    # 引用推文详情：被引块放正文下方（与 X 网页阅读序一致），标注被引作者
    focal = _tweet_result("100", text="我的评论", name="评论者", new_user=True)
    focal["quoted_status_result"] = {"result": _tweet_result("300", text="被引正文", name="被引者", handle="q", new_user=True)}
    title, _meta, desc, _time = _detail_doc(focal, None)
    assert title == "我的评论"
    assert "> **被引推文** · 被引者 @q：被引正文" in desc
    assert desc.index("我的评论") < desc.index("被引推文")  # 正文在上、被引块在下


def test_x_detail_doc_parent_above_body_and_quote_below() -> None:
    # 回复 + 引用同推（对某条引用推文的回复）：原推块在上、正文居中、被引块在下
    parent = _tweet_result("200", text="原推正文", name="原作者", new_user=True)
    focal = _tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    focal["legacy"]["in_reply_to_status_id_str"] = "200"
    focal["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "
    focal["quoted_status_result"] = {"result": _tweet_result("300", text="被引正文", name="被引者", new_user=True)}
    _title, _meta, desc, _time = _detail_doc(focal, parent)
    assert desc.index("原推") < desc.index("我的回复") < desc.index("被引推文")


def test_x_reply_answer_votes_and_markers() -> None:
    r = _tweet_result("101", text="回复\n两行", media=[{"type": "video"}], new_user=True)
    r["legacy"]["favorite_count"] = 3
    r["legacy"]["created_at"] = "Wed Oct 15 08:23:11 +0000 2025"
    a = _reply_answer(r)
    assert a is not None
    assert a.author == "作者 @user"  # 楼层头同样署名「名字 @handle」
    assert a.votes == "赞 3"
    assert a.content_md == "回复\n两行 [视频]"  # 换行保留；残卡无地址退纯记号


def test_x_reply_answer_media_link() -> None:
    # 楼层媒体附直接地址（与详情正文、知乎内联同形）
    r = _tweet_result(
        "101", text="回复", media=[{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/c.jpg"}], new_user=True
    )
    a = _reply_answer(r)
    assert a is not None
    assert a.content_md == "回复 [图片](https://pbs.twimg.com/media/c.jpg)"


def test_x_reply_answer_quote_snippet() -> None:
    # 回复楼层同样缀引用片段（回复可以是引用推文），与列表同词汇
    r = _tweet_result("101", text="这条回复带引用", new_user=True)
    r["quoted_status_result"] = {"result": _tweet_result("300", text="被引内容", handle="who", new_user=True)}
    a = _reply_answer(r)
    assert a is not None
    assert a.content_md == "这条回复带引用 ▸ 引用 作者 @who：被引内容"


def test_x_reply_strips_leading_mentions() -> None:
    # 回复开头的 @ 提及是噪音（X 自己也藏）：display_text_range 按码元切掉前缀
    r = _tweet_result("101", text="@楼主 回复正文", new_user=True)
    r["legacy"]["display_text_range"] = [4, 9]  # "@楼主 " 4 个 UTF-16 码元
    a = _reply_answer(r)
    assert a is not None
    assert a.content_md == "回复正文"


def test_x_reply_mention_strip_emoji_safe() -> None:
    # 索引按 UTF-16 码元计（表情是代理对占 2）：转码后切，emoji 不偏移
    r = _tweet_result("101", text="@a 😅正文", new_user=True)
    r["legacy"]["display_text_range"] = [3, 7]  # "@a " 3 码元；😅 2 码元 + 正文 2
    a = _reply_answer(r)
    assert a is not None
    assert a.content_md == "😅正文"


def test_x_reply_answer_noise_dropped() -> None:
    assert _reply_answer(_tweet_result("", text="无 id", new_user=True)) is None
    r = _tweet_result("101", text="无作者", new_user=True)
    del r["core"]
    assert _reply_answer(r) is None


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
    r = _tweet_result("100", text="Hello\nsecond line", new_user=True)
    _grok(r, translation="你好\n第二行")
    title, _meta, desc, _time = _detail_doc(r, None)
    assert title == "Hello"
    assert desc == "Hello\nsecond line\n\n> **译文**（英语）：你好 第二行"  # 换行压单段；语种括注


def test_x_grok_translation_gates() -> None:
    # is_available 关 / 译文空 = 无译文不产块；单行短文维持「不二刷」折叠
    r = _tweet_result("100", text="Hello", new_user=True)
    _grok(r, translation="你好", available=False)
    _title, _meta, desc, _time = _detail_doc(r, None)
    assert desc == ""
    r2 = _tweet_result("100", text="Hello", new_user=True)
    _grok(r2, translation="   ")
    _title, _meta, desc2, _time = _detail_doc(r2, None)
    assert desc2 == ""


def test_x_grok_translation_lang_name_and_fallback() -> None:
    r = _tweet_result("100", text="こんにちは", new_user=True)
    _grok(r, translation="你好", source="ja")
    _title, _meta, desc, _time = _detail_doc(r, None)
    assert "**译文**（日语）：" in desc  # 语种码 -> 中文名括注
    r2 = _tweet_result("100", text="?", new_user=True)
    _grok(r2, translation="…", source="qnt")
    _title, _meta, desc2, _time = _detail_doc(r2, None)
    assert "**译文**（qnt）：" in desc2  # 查无此码退原码


def test_x_grok_translation_expands_tco_by_own_entities() -> None:
    # 译文自带 entities（坐标按译文文本计）：t.co 短链展开 display_url，与正文同规则
    r = _tweet_result("100", text="See this", new_user=True)
    _grok(r, translation="看这个 https://t.co/abc")
    r["grok_translated_post_with_availability"]["data"]["entities"]["urls"] = [
        {"indices": [4, 20], "display_url": "example.com/x", "url": "https://t.co/abc"}
    ]
    _title, _meta, desc, _time = _detail_doc(r, None)
    assert "> **译文**（英语）：看这个 example.com/x" in desc


def test_x_reply_answer_translation_after_media() -> None:
    # 楼层序：正文 → 媒体地址 → 引用片段 → 译文块缀尾（译文是 blockquote，
    # 片段接其后会粘进引用行里像译文的一部分）
    r = _tweet_result(
        "101",
        text="Nice shot",
        media=[{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/c.jpg"}],
        new_user=True,
    )
    _grok(r, translation="拍得好")
    r["quoted_status_result"] = {"result": _tweet_result("300", text="被引", handle="who", new_user=True)}
    a = _reply_answer(r)
    assert a is not None
    assert a.content_md == (
        "Nice shot [图片](https://pbs.twimg.com/media/c.jpg) ▸ 引用 作者 @who：被引"
        "\n\n> **译文**（英语）：拍得好"
    )


def test_x_quote_context_translation_second_paragraph() -> None:
    # 引用块内译文作第二段（> 空行分隔）：原推/被引是外文时同样带译文
    parent = _tweet_result("200", text="Parent post", name="原作者", new_user=True)
    _grok(parent, translation="原推译文")
    reply = _tweet_result("100", text="@原作者 我的回复", name="回复者", new_user=True)
    reply["legacy"]["in_reply_to_status_id_str"] = "200"
    reply["legacy"]["display_text_range"] = [5, 9]  # 藏 "@原作者 "
    _title, _meta, desc, _time = _detail_doc(reply, parent)
    assert "> **原推** · 原作者 @user：Parent post\n>\n> **译文**（英语）：原推译文" in desc


def test_x_tweet_author_new_user_core_shape() -> None:
    # 列表路径同样认新用户形态（HomeLatestTimeline 若迁移，列表不炸）
    it = _tweet_from_result(_tweet_result("1001", text="推文", new_user=True))
    assert it is not None
    assert it.meta == "作者 @user"
    assert it.url == "https://x.com/user/status/1001"


def test_x_tweet_detail_loader_first_batch_and_paging(monkeypatch) -> None:
    first = [
        _entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100", text="主推\n第二行", name="作者", new_user=True))),
        _entry(
            entry_id="conversationthread-200",
            items=[{"item": {"itemContent": _item_content(_tweet_result("101", text="回复一", new_user=True))}}],
        ),
    ]
    page2 = [
        _entry(
            entry_id="conversationthread-300",
            items=[
                {"item": {"itemContent": _item_content(_tweet_result("102", text="回复二", new_user=True))}},
                {"item": {"itemContent": _item_content(_tweet_result("100", text="焦点再现", new_user=True))}},  # 翻页页焦点重现
            ],
        ),
    ]
    calls: list[str | None] = []

    async def _fake_conv(rest_id: str, cursor: str | None):
        calls.append(cursor)
        assert rest_id == "100"
        return (first, "more") if cursor is None else (page2, None)

    monkeypatch.setattr("feedscope.sources.x.fetch._fetch_conversation", _fake_conv)
    loader = XTweetDetailLoader("100", first, "more")
    assert loader.doc.title == "主推"
    assert loader.doc.description_md == "主推\n第二行"
    assert [a.content_md for a in loader.doc.answers] == ["回复一"]
    assert not loader.done

    new = asyncio.run(loader.load_more())
    assert [a.content_md for a in new] == ["回复二"]  # 焦点再现被去重
    assert loader.done
    assert [a.content_md for a in loader.doc.answers] == ["回复一", "回复二"]
    assert asyncio.run(loader.load_more()) == []  # done 后不再请求
    assert calls == ["more"]


def test_x_tweet_detail_loader_empty_raises() -> None:
    with pytest.raises(SourceError):
        XTweetDetailLoader("100", [], None)


def test_x_tweet_detail_loader_no_replies_done_immediately() -> None:
    # 0 回复推文也带游标：首批无回复即判 done，不再无休空翻
    first = [_entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100", text="主推", new_user=True)))]
    loader = XTweetDetailLoader("100", first, "more")
    assert loader.done
    assert loader.doc.answers == []
    assert asyncio.run(loader.load_more()) == []


def test_x_tweet_detail_loader_empty_page_marks_done(monkeypatch) -> None:
    # 翻页页只有焦点重现（无新回复）：空页判死，不空转
    first = [
        _entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100", text="主推", new_user=True))),
        _entry(
            entry_id="conversationthread-1",
            items=[{"item": {"itemContent": _item_content(_tweet_result("101", text="回复一", new_user=True))}}],
        ),
    ]

    async def _fake_conv(rest_id: str, cursor: str | None):
        return [_entry(entry_id="tweet-100", item_content=_item_content(_tweet_result("100", text="主推", new_user=True)))], "still-more"

    monkeypatch.setattr("feedscope.sources.x.fetch._fetch_conversation", _fake_conv)
    loader = XTweetDetailLoader("100", first, "more")
    assert not loader.done
    assert asyncio.run(loader.load_more()) == []
    assert loader.done  # 空页判死
