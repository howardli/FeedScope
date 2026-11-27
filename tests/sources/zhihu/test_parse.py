"""知乎解析层：API JSON -> FeedItem / Answer / Comment / DetailDoc，手写最小 fixture，不碰网络。"""

from __future__ import annotations

import re

from feedscope.core.models import Answer
from feedscope.sources.zhihu.parse import _md as _md_zhihu
from feedscope.sources.zhihu.parse import (
    parse_answers,
    parse_comments,
    parse_feed_item,
    parse_hot_list,
    parse_question_doc,
)

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
    items = parse_hot_list(data)
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
    a = parse_feed_item(
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
    q = parse_feed_item(
        {
            "verb": "MEMBER_ASK_QUESTION",
            "target": {"id": 2100191570838434341, "type": "question", "title": "提问", "author": {"name": "提问人"}},
        }
    )
    assert q is not None and q.title == "提问" and q.id == "question/2100191570838434341"
    # 丢弃形态：feed_group 分组卡（无 target）、文章、缺所属问题的 answer
    assert parse_feed_item({"type": "feed_group", "verb": None}) is None
    assert parse_feed_item({"target": {"id": 1, "type": "article", "title": "文章"}}) is None
    assert parse_feed_item({"target": {"type": "answer", "author": {"name": "a"}}}) is None


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
    ans = parse_answers(data, "123")
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
    zero = parse_answers(
        {"data": [{"target": {"id": 1, "type": "answer", "author": {"name": "a"},
                              "voteup_count": 0, "comment_count": 0, "created_time": 0,
                              "content": "<p>x</p>", "question": {"id": 9}}}]}, "9"
    )
    assert zero[0].votes == "" and zero[0].comment_count == "" and zero[0].time == ""


def test_zhihu_wan_format() -> None:
    """计数折「万」走 core/display（见 test_display.py）；这里验证 feeds 接入同形。"""
    ans = parse_answers(
        {"data": [
            {"target": {"type": "answer", "id": "1", "voteup_count": 10584, "comment_count": 0,
                        "author": {"name": "答主"}, "content": "", "created_time": 0}}
        ]},
        "123",
    )
    assert ans[0].votes == "赞 1.1 万"


def test_zhihu_question_doc_asker_and_time() -> None:
    """问题元信息 API（v4/questions?include=…）-> DetailDoc：计数拼 meta 行，unix 秒转北京时间。"""

    doc = parse_question_doc(
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
    doc = parse_question_doc({"title": "问题", "author": {"name": "阿财"}}, [])
    assert doc.meta == "阿财 提问"
    assert doc.time == ""
    # 提问人缺失回退匿名；小浏览量不折万
    doc2 = parse_question_doc({"title": "问题", "visit_count": 999}, [])
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
    """comment_v5 JSON -> Comment：赞数/绝对时间/子回复/删除过滤。"""
    from feedscope.core.timefmt import fmt_unix

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
    (top,) = parse_comments(data)
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
    assert parse_comments({"data": []}) == []  # 空页不炸


