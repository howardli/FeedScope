"""V2EX 源测试夹具：v1 API 的主题列表与回复。"""

from __future__ import annotations

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
