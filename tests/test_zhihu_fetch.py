"""知乎取数层离线测试：FakePage/FakeTab 替代 Playwright（评论/通道，不碰
浏览器）+ 游标 API Loader（monkeypatch _api_json，不碰网络）。

全部数据走免签 API（请求由常驻通道页签的页面 fetch 发出，带 x-zse-93 版本
头）：详情 = 元信息 API + 回答 feeds；评论 = comment_v5（FakePage 替身驱动
页面 fetch/子回复补全）。通道页签生命周期（单例/在途跳转重建）归
test_channel.py 集中测，这里只验知乎专属的登录墙开页校验钩子。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeTab

import feedscope.sources.zhihu.fetch as zhihu_fetch
from feedscope.core.browser import ChannelTab
from feedscope.core.source import LoginRequired
from feedscope.sources.zhihu.fetch import (
    ZhihuApiFeedLoader,
    ZhihuQuestionLoader,
    _api_json,
    _fetch_hot_list,
    _open_question,
)

_FEEDS_NEXT = "https://www.zhihu.com/api/v4/questions/123/feeds?cursor=n2"


class FakePage:
    """评论 API 状态机（首页/翻页计数、子回复缺口），由 _tab_for 包成通道替身。"""

    _NEXT = "https://www.zhihu.com/api/v4/comment_v5/answers/456/root_comment?limit=10&offset=x"

    def __init__(self) -> None:
        self.url = "https://www.zhihu.com/question/123"
        self.closed = False
        self._cm_pages = 0  # 评论 API 已 fetch 的页数（首页 + 翻页）
        self._child_reqs = 0  # 子回复补全被调用的次数
        self._cm_gap = 2  # 首页根评论的子回复缺口条数（可覆写）

    def _cm_api_page(self) -> dict:
        """评论 API 两页：首页 1 条根评论（带 1 条子回复 + 1 条隐藏待补全），
        翻页 1 条后 is_end；首页 data 空可由 _cm_empty 模拟 0 评论。"""
        self._cm_pages += 1
        if self._cm_pages == 1:
            if getattr(self, "_cm_empty", False):
                return {"data": [], "paging": {"is_end": True, "next": ""}}
            return {
                "data": [
                    {
                        "id": 11608,
                        "author": {"name": "评论人"},
                        "content": "评",
                        "created_time": 1793491084,
                        "like_count": 3,
                        "child_comment_count": 1 + self._cm_gap,  # 带出 1 + 缺口
                        "child_comment_next_offset": "o1",
                        "child_comments": [
                            {
                                "id": 11609,
                                "author": {"name": "楼中楼"},
                                "content": "回",
                                "created_time": 1793491704,
                                "like_count": 0,
                                "child_comments": [],
                            }
                        ],
                    },
                    {
                        "id": 11699,
                        "is_delete": True,
                        "author": {"name": "已删"},
                        "content": "x",
                    },  # 已删的不算数
                ],
                "paging": {"is_end": False, "next": self._NEXT},
            }
        return {
            "data": [
                {
                    "id": 11610,
                    "author": {"name": "翻页评论"},
                    "content": "新",
                    "created_time": 1793500000,
                    "like_count": 1,
                    "child_comments": [],
                }
            ],
            "paging": {"is_end": True, "next": ""},
        }

    async def goto(self, url: str, **kw) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    def _open_question(self, _arg) -> dict:
        """开题并行 JS 的返回（metaUrl 标记路由；_open_payload 可覆写）。"""
        return getattr(self, "_open_payload", None) or {
            "meta": dict(_Q_META),
            "feeds": _feeds_top(),
        }

    def _complete_children(self, roots) -> dict:
        """隐藏子回复并发补全的返回（Promise.all 标记；按缺口条数返回，拉齐为止）。"""
        self._child_reqs += 1
        return {
            str(roots[0]["id"]): [
                {
                    "id": 11610 + i,
                    "author": {"name": f"补全子回复{i}"},
                    "content": f"齐{i}",
                    "created_time": 1793492000 + i,
                    "like_count": i,
                    "child_comments": [],
                }
                for i in range(1, self._cm_gap + 1)
            ]
            for roots in [roots or []]
        }


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def new_page(self) -> FakePage:
        return self._page


def _tab_for(page: FakePage) -> FakeTab:
    """conftest 共享 FakeTab 包 FakePage 状态机：单请求走 _cm_api_page（评论翻页
    计数），复合脚本（开题/子回复补全）按 markers 路由到 FakePage 方法。"""
    return FakeTab(
        on_fetch=lambda req: (None, page._cm_api_page()),
        markers=[("metaUrl", page._open_question), ("Promise.all", page._complete_children)],
    )


# ---- 问题详情（纯 API：monkeypatch _api_json + 假通道页签，不碰网络/浏览器）----

_Q_META = {  # 元信息 API 响应（实测形态裁剪）
    "title": "问题",
    "created": 1735689600,
    "author": {"name": "提问人甲"},
    "detail": "",
    "answer_count": 66,
    "visit_count": 12340000,
    "follower_count": 9,
}


def _feeds_answer_dict(aid: int, cc: int = 12, votes: int = 1) -> dict:
    """回答 feeds API 条目（实测形态裁剪）。"""
    return {
        "target": {
            "id": aid,
            "type": "answer",
            "author": {"name": "答主"},
            "voteup_count": votes,
            "comment_count": cc,
            "created_time": 1735689900,
            "content": "<p>答</p>",
            "question": {"id": 123},
        }
    }


def _feeds_top(next_url: str = _FEEDS_NEXT, aid: int = 456) -> dict:
    return {"data": [_feeds_answer_dict(aid)], "paging": {"is_end": False, "next": next_url}}


def _open_loader(
    monkeypatch,
    page: FakePage | None = None,
    *,
    meta: dict | None = None,
    feeds: dict | None = None,
) -> ZhihuQuestionLoader:
    """monkeypatch 出纯 API 的 _open_question（并行开题 payload 走 FakePage），
    评论层与后续 load_more 翻页走各自通道。"""
    page = page or FakePage()
    page._open_payload = {
        "meta": dict(_Q_META) if meta is None else meta,
        "feeds": _feeds_top() if feeds is None else feeds,
    }

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._tab", _tab_for(page))
    return asyncio.run(_open_question("123"))


def test_open_question_doc_from_meta_api(monkeypatch) -> None:
    """详情纯 API：元信息 API 拼 doc（提问人/计数/时间），不加载任何页面。"""
    loader = _open_loader(monkeypatch)
    assert loader.doc.title == "问题"
    assert loader.doc.meta == "提问人甲 提问 · 66 个回答 · 1234 万浏览"
    assert loader.doc.time == "2025-01-01 08:00"  # 裸时间（各源详情 meta 行同形）
    assert not loader.done  # feeds 首批带游标
    asyncio.run(loader.close())


def test_open_question_first_batch_answers_from_feeds(monkeypatch) -> None:
    """首批回答来自 feeds：楼层带 url 与评论数文本（评论层靠 url 拿 aid）。"""
    loader = _open_loader(monkeypatch)
    ans = loader.doc.answers[0]
    assert ans.url == "https://www.zhihu.com/question/123/answer/456"
    assert ans.comment_count == "12 条评论"
    assert ans.content_md == "答"
    asyncio.run(loader.close())


def test_open_comments_api_first_page(monkeypatch) -> None:
    """按需进评论层（通道 fetch）：首页解析 + 隐藏子回复自动补全 + 已删过滤。"""
    from feedscope.core.timefmt import fmt_unix

    page = FakePage()
    loader = _open_loader(monkeypatch, page)
    cm = asyncio.run(loader.open_comments(0))
    top = cm.comments[0]
    assert top.author == "评论人"
    assert top.votes == "赞 3"
    assert top.raw_id == "11608"
    assert top.time == fmt_unix(1793491084)  # unix 秒 -> 北京时间绝对时间
    assert [r.author for r in top.replies] == [
        "楼中楼",
        "补全子回复1",
        "补全子回复2",
    ]  # 隐藏子回复已拉齐
    assert top.replies[0].votes == ""  # like_count 0 = 无赞为空
    assert len(cm.comments) == 1  # is_delete 的不进列表
    assert not cm.done  # 有下一页
    assert page._child_reqs == 1  # 有缺口（3-1=2）：补全请求发了
    asyncio.run(cm.close())
    asyncio.run(loader.close())
    assert not page.closed  # 全程无内容页面可关


def test_open_comments_single_gap_also_completed(monkeypatch) -> None:
    """缺口哪怕 1 条也拉全：不留「另有 1 条回复」的尾巴（阅读完整性优先）。"""
    page = FakePage()
    loader = _open_loader(monkeypatch, page)
    page._cm_gap = 1  # 带出 1 + 缺口 1
    cm = asyncio.run(loader.open_comments(0))
    top = cm.comments[0]
    assert page._child_reqs == 1, "缺口 1 也要拉齐"
    assert [r.author for r in top.replies] == ["楼中楼", "补全子回复1"]
    asyncio.run(cm.close())
    asyncio.run(loader.close())


def test_open_comments_api_paging_to_end(monkeypatch) -> None:
    """游标翻页：n 取下一页，is_end 后 done、再 n 空列表。"""
    loader = _open_loader(monkeypatch, FakePage())
    cm = asyncio.run(loader.open_comments(0))
    new = asyncio.run(cm.load_more())
    assert [c.author for c in new] == ["翻页评论"] and len(cm.comments) == 2
    assert cm.done  # is_end=True 且 next 空
    assert asyncio.run(cm.load_more()) == []
    asyncio.run(cm.close())
    asyncio.run(loader.close())


def test_open_comments_zero_comments(monkeypatch) -> None:
    """0 评论回答：首页空 + is_end，进评论层即到头，不炸。"""
    page = FakePage()
    loader = _open_loader(monkeypatch, page)
    page._cm_empty = True
    cm = asyncio.run(loader.open_comments(0))
    assert cm.comments == [] and cm.done
    asyncio.run(cm.close())
    asyncio.run(loader.close())


def test_open_comments_http_error_is_source_error(monkeypatch) -> None:
    """接口非 2xx：open_comments 转 SourceError（出口给 UI 提示）。"""
    from feedscope.core.source import SourceError
    from feedscope.sources.zhihu import fetch as zf

    loader = _open_loader(monkeypatch, FakePage())
    zf._tab._on_fetch = lambda req: (403, None)  # 开题已装好替身：就地注入接口 403
    with pytest.raises(SourceError, match="403"):
        asyncio.run(loader.open_comments(0))
    asyncio.run(loader.close())


def test_open_comments_out_of_range_is_source_error(monkeypatch) -> None:
    """越界（未加载的回答）给 SourceError 出口，不裸炸。"""
    from feedscope.core.source import SourceError

    loader = _open_loader(monkeypatch)
    with pytest.raises(SourceError, match="加载更多"):
        asyncio.run(loader.open_comments(99))
    asyncio.run(loader.close())


def test_question_loader_load_more_via_feeds_api(monkeypatch) -> None:
    """回答翻页：游标推进、按 url 去重、is_end 后 done，doc 维持「已加载全部」。"""
    loader = _open_loader(monkeypatch)
    assert not loader.done and len(loader.doc.answers) == 1  # 首批 1 条，有游标
    calls: list[str] = []

    async def _fake(url: str, referer: str) -> dict:
        calls.append(url)
        if len(calls) == 1:
            return {
                "data": [_feeds_answer_dict(789)],
                "paging": {
                    "is_end": False,
                    "next": "https://www.zhihu.com/api/v4/questions/123/feeds?cursor=n3",
                },
            }
        return {
            "data": [_feeds_answer_dict(789)],
            "paging": {"is_end": True, "next": ""},
        }  # 重复 789

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._api_json", _fake)
    new = asyncio.run(loader.load_more())
    assert [a.url for a in new] == ["https://www.zhihu.com/question/123/answer/789"]
    assert len(loader.doc.answers) == 2 and not loader.done
    assert asyncio.run(loader.load_more()) == []  # 重复回答被 url 去重
    assert loader.done  # is_end 判死
    assert calls[0].endswith("cursor=n2")  # 首翻页接 feeds 首批给的游标
    assert calls[1].endswith("cursor=n3")  # 之后跟服务端游标
    asyncio.run(loader.close())


def test_question_deleted_404_is_friendly(monkeypatch) -> None:
    """问题被删/不存在：元信息 404 转友好文案，不让用户看 HTTP 码猜。"""
    from feedscope.core.source import SourceError

    page = FakePage()
    page._open_payload = {"meta": {"__status": 404}, "feeds": {}}

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._tab", _tab_for(page))
    with pytest.raises(SourceError, match="已被删除或不存在"):
        asyncio.run(_open_question("999999999999999999"))


def test_api_json_401_is_login_required(monkeypatch) -> None:
    """会话过期（401）：转 LoginRequired 触发 UI 登录引导（403 可能是风控，不并入）。"""

    tab = _tab_for(FakePage())
    tab._on_fetch = lambda req: (401, None)  # 会话过期
    monkeypatch.setattr("feedscope.sources.zhihu.fetch._tab", tab)
    with pytest.raises(LoginRequired, match="重新登录"):
        asyncio.run(_api_json("https://www.zhihu.com/api/v4/questions/1", "https://www.zhihu.com/"))


def test_question_loader_without_next_done_at_open(monkeypatch) -> None:
    """feeds 首批即到底（无 next）：首批可用，loader 即 done。"""
    loader = _open_loader(
        monkeypatch,
        feeds={"data": [_feeds_answer_dict(456)], "paging": {"is_end": True, "next": ""}},
    )
    assert len(loader.doc.answers) == 1
    assert loader.done
    assert asyncio.run(loader.load_more()) == []
    asyncio.run(loader.close())


# ---- 信息流 API（热榜/推荐/关注：monkeypatch _api_json，不碰网络/浏览器）----


def _rec_page(items: list[dict], *, next_url: str = "", is_end: bool = False) -> dict:
    return {"data": items, "paging": {"is_end": is_end, "next": next_url}}


def _answer_item(qid: int, aid: int, title: str = "问题标题", author: str = "作者") -> dict:
    return {
        "verb": "MEMBER_VOTEUP_ANSWER",
        "target": {
            "id": aid,
            "type": "answer",
            "author": {"name": author},
            "question": {"id": qid, "title": title},
        },
    }


def test_hot_list_from_api(monkeypatch) -> None:
    """热榜一次 API 调用：URL 原样、referer 跟随热榜页，解析出 FeedItem。"""
    seen: dict = {}

    async def _fake(url: str, referer: str) -> dict:
        seen.update(url=url, referer=referer)
        return {
            "data": [
                {
                    "detail_text": "2606 万热度",
                    "target": {
                        "id": 2100,
                        "type": "question",
                        "title": "热题",
                        "url": "https://api.zhihu.com/questions/2100",
                    },
                }
            ]
        }

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._api_json", _fake)
    items = asyncio.run(_fetch_hot_list("https://www.zhihu.com/hot"))
    assert "topstory/hot-list" in seen["url"] and seen["referer"] == "https://www.zhihu.com/hot"
    assert [it.id for it in items] == ["question/2100"]
    assert items[0].url == "https://www.zhihu.com/question/2100"
    assert items[0].meta == "2606 万热度"


def test_api_feed_loader_pages_and_dedup(monkeypatch) -> None:
    """游标翻页（paging.next）：跨页按问题 id 去重，噪音卡不进列表。"""
    calls: list[str] = []

    async def _fake(url: str, referer: str) -> dict:
        calls.append(url)
        if url == "first":
            return _rec_page(
                [
                    _answer_item(1, 11),
                    {"type": "feed_group", "verb": None},
                    _answer_item(2, 22, author="乙"),
                ],
                next_url="second",
            )
        return _rec_page(
            [_answer_item(2, 99, title="重复问题（另一回答）"), _answer_item(3, 33)],
            next_url="third",
            is_end=True,
        )

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._api_json", _fake)
    loader = ZhihuApiFeedLoader("first", "https://www.zhihu.com/")
    new = asyncio.run(loader.load_more())
    assert [it.id for it in new] == ["question/1", "question/2"]  # feed_group 被丢弃
    assert [it.meta for it in new] == ["作者", "乙"]
    assert not loader.done  # 还有下一页
    new = asyncio.run(loader.load_more())
    assert [it.id for it in new] == ["question/3"]  # 同问题的另一回答不重复进列表
    assert loader.done  # is_end 且 next 空
    assert asyncio.run(loader.load_more()) == []  # done 后不再请求
    assert calls == ["first", "second"]  # 翻页把 next 游标传回
    asyncio.run(loader.close())  # 通道页签不归单个 Loader 关，幂等


def test_api_feed_loader_empty_page_keeps_going(monkeypatch) -> None:
    """整页噪音（解析全空）但有游标：不判死（拉空可能是抖动），n 可再试。"""

    async def _fake(url: str, referer: str) -> dict:
        return _rec_page([{"type": "feed_group"}], next_url="next")

    monkeypatch.setattr("feedscope.sources.zhihu.fetch._api_json", _fake)
    loader = ZhihuApiFeedLoader("first", "https://www.zhihu.com/")
    assert asyncio.run(loader.load_more()) == []
    assert not loader.done and loader.items == []


# ---- 常驻通道页签（生命周期归 test_channel.py；这里只验知乎专属的登录墙钩子）----


class _FakeCtx2:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def new_page(self) -> FakePage:
        return self._page


class _FakeSession2:
    def __init__(self, page: FakePage) -> None:
        self._ctx = _FakeCtx2(page)

    async def run(self, op, *args, **kwargs):
        return await op(self._ctx, *args, **kwargs)


def test_channel_login_wall_is_login_required(monkeypatch) -> None:
    """通道页签撞登录墙（goto 落到 signin）：ChannelTab 开页校验钩子抛 LoginRequired。"""
    page = FakePage()

    async def goto_signin(url: str, **kw) -> None:
        page.url = "https://www.zhihu.com/signin?next=%2F"

    page.goto = goto_signin
    monkeypatch.setattr("feedscope.core.browser.get_session", lambda: _FakeSession2(page))
    monkeypatch.setattr(
        zhihu_fetch,
        "_tab",
        ChannelTab("https://www.zhihu.com/", check=zhihu_fetch._login_wall, name="zhihu"),
    )
    with pytest.raises(LoginRequired):
        asyncio.run(zhihu_fetch._tab.get())
