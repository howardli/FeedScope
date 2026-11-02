"""V2EX 解析层测试：列表 -> FeedItem；topic + 回复 -> 详情楼层。

fixture 是 v1 线上响应形状的最小裁剪（字段名、空值形态照抄实测），覆盖：
meta 三段（节点/回复数/作者）、extra 透传、正文与回复的 HTML -> markdown
（markdown 标题、自动内联图片的 [图片] 记号、@ 提及链接绝对化）、
楼层直达锚点与时间、空壳回复跳过、野数据丢条不丢流。
"""

from __future__ import annotations

from feedscope.sources.v2ex.parse import parse_detail, parse_topics

# 列表条目：完整主题（markdown 正文）/ 零回复主题 / 野数据（非数字 id、缺 id）
TOPICS = [
    {
        "id": 1253836,
        "title": "前公司老板找我回去救场，情况比较特殊，想听听各位的建议",
        "url": "https://www.v2ex.com/t/1253836",
        "content": "# 求助：前老板邀约回去救场\r\n各位好……",
        "content_rendered": (
            "<h1>求助：前老板邀约回去救场</h1>\n<p>各位好，想咨询下大家的经验。</p>"
        ),
        "replies": 163,
        "created": 1793604456,
        "last_modified": 1793604456,
        "last_touched": 1793610000,
        "last_reply_by": "someone",
        "deleted": 0,
        "member": {"id": 1, "username": "losephsky", "url": "https://www.v2ex.com/u/losephsky"},
        "node": {
            "id": 1,
            "name": "career",
            "title": "职场话题",
            "url": "https://www.v2ex.com/go/career",
        },
    },
    {
        "id": 1253979,
        "title": "我在安卓设备上复刻了豆包手机功能",
        "url": "https://www.v2ex.com/t/1253979",
        "content": "大家~",
        "content_rendered": "<p>大家~</p>",
        "replies": 0,
        "created": 1793580000,
        "member": {"id": 2, "username": "demo"},
        "node": {"id": 2, "name": "create", "title": "分享创造"},
    },
    {"id": "not-a-number", "title": "野数据", "member": {"username": "x"}, "node": {}},
    {"title": "缺 id 整条不可寻址"},
]


def test_parse_topics_items() -> None:
    items = parse_topics(TOPICS)
    assert [it.id for it in items] == ["1253836", "1253979"]  # 野数据丢条不丢流

    a = items[0]
    assert (a.source, a.kind, a.title) == (
        "v2ex",
        "topic",
        "前公司老板找我回去救场，情况比较特殊，想听听各位的建议",
    )
    assert a.url == "https://www.v2ex.com/t/1253836"
    assert a.meta == "职场话题 · 163 回复 · losephsky"  # 节点在前好扫读
    assert a.extra["topic"]["id"] == 1253836  # 整个 topic 透传给详情

    b = items[1]
    assert b.meta == "分享创造 · demo"  # 0 回复不出「回复」段


def test_parse_topics_empty() -> None:
    assert parse_topics([]) == []


# 回复：自动内联图片（裸图链变 <a><img></a>）/ @ 提及（相对链接）/ 空壳
REPLIES = [
    {
        "id": 18244992,
        "content": "感觉建模很娘炮 https://i.imgur.com/L62ZP7V.png",
        "content_rendered": (
            '感觉建模很娘炮 <a target="_blank" href="https://i.imgur.com/L62ZP7V.png" '
            'rel="nofollow noopener"><img src="https://i.imgur.com/L62ZP7V.png" '
            'class="embedded_image" rel="noreferrer"></a>'
        ),
        "created": 1793581787,
        "last_modified": 1793581787,
        "member": {"id": 668270, "username": "Croow"},
        "member_id": 668270,
        "topic_id": 1253638,
    },
    {
        "id": 18245038,
        "content": "@z1111h 牛逼，叶师傅还是太强了",
        "content_rendered": '@<a href="/member/z1111h">z1111h</a> 牛逼，叶师傅还是太强了',
        "created": 1793582250,
        "member": {"id": 1, "username": "zuosiruan"},
        "topic_id": 1253638,
    },
    {
        "id": 18245099,
        "content": "",
        "content_rendered": "",
        "created": 1793580000,
        "member": {"id": 3, "username": "ghost"},
        "topic_id": 1253638,
    },
]


def test_parse_detail_body() -> None:
    d = parse_detail(TOPICS[0], REPLIES)
    assert d.title == "前公司老板找我回去救场，情况比较特殊，想听听各位的建议"
    assert d.meta == "职场话题 · 163 回复 · losephsky"
    assert d.time == "2026-11-02 15:27"  # unix 秒 -> 北京时间
    assert d.description_md.startswith("# 求助：前老板邀约回去救场")  # markdown 标题保层级
    assert "各位好，想咨询下大家的经验。" in d.description_md


def test_parse_detail_floors() -> None:
    floors = parse_detail(TOPICS[0], REPLIES).replies
    assert [f.author for f in floors] == ["Croow", "zuosiruan"]  # 空壳回复跳过

    img, at = floors
    assert img.votes == ""  # v1 回复不带赞数
    # 自动内联是 <a><img></a> 双层：整只塌缩成一个记号（嵌套链接 markdown
    # 解析不了，会整段字面露出），必须全等不是包含
    assert img.content_md == "感觉建模很娘炮 [图片](https://i.imgur.com/L62ZP7V.png)"
    assert img.time == "2026-11-02 09:09"
    assert img.url == "https://www.v2ex.com/t/1253638#reply18244992"  # 楼层直达锚点

    assert "[z1111h](https://www.v2ex.com/member/z1111h)" in at.content_md  # 相对 @ 链接绝对化
    assert at.url.endswith("#reply18245038")


def test_parse_detail_bare_topic() -> None:
    """零回复的主题：无楼层、正文照出（详情空态与 RSS 全文阅读同款正常路径）。"""
    d = parse_detail(TOPICS[1], [])
    assert d.replies == []
    assert d.description_md == "大家~"
    assert d.meta == "分享创造 · demo"
