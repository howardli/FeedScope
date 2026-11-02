"""X（Twitter）源（x）：推荐/正在关注时间线 + 推文详情（正文与回复）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：httpx 直调 GraphQL + 游标翻页 Loader（搜索为何不做，见其模块头）
- parse.py  解析层：URT JSON -> FeedItem / 详情，纯函数可测
- __init__.py  编排层：Source 的能力声明（词汇表/登录）与 open_* 分派

全部能力在登录墙后：`feedscope login x` 弹有头 Chrome 手动输账号密码（+2FA），
轮询到 auth_token 即成功（login.py 源无关，零改动）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.browser import get_session, profile_cookies
from feedscope.core.const import DESKTOP_UA
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    LoginRequired,
    Source,
    require_feed_items,
)
from feedscope.sources.x.fetch import (
    XFeedLoader,
    XSession,
    XTweetDetailLoader,
    _fetch_conversation,
)


async def _x_session(what: str) -> XSession:
    """读 profile 全量 cookie 判登 + 本机 Chrome 真实 UA，打成 httpx 登录态。

    时间线与详情共用同一条判登路径（what 进提示文案，如「正在关注」时间线）。
    cookie 带全量 jar（与真浏览器请求同形，不再只挑 auth_token/ct0）；UA 用
    会话探针修过的完整版（profile_cookies 已把会话拉起，探针必已跑过），与
    登录会话逐字一致；探针不可得才退截断版 DESKTOP_UA 兜底。
    """
    cookies = await profile_cookies("https://x.com")
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise LoginRequired(f"X 需要登录后才能查看{what}")
    return XSession(cookies, get_session().ua or DESKTOP_UA)


@register
class XSource(Source):
    source_id = "x"
    label = "X"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["foryou", "following"]  # 与 X 主页页签同序：默认进「推荐」
    feed_labels: ClassVar[dict[str, str]] = {"foryou": "推荐", "following": "正在关注"}
    detail_kinds = frozenset({"tweet"})  # Enter 进终端详情屏（正文 + 回复）
    detail_unit_label = "回复"
    login_url = "https://x.com/login"
    login_cookie = "auth_token"  # feedscope login 轮询的判登 cookie（手动输密码后写入）
    logout_cookies: ClassVar[list[str]] = ["auth_token", "ct0"]  # 登出清掉判登成对 cookie

    async def open_feed(self, name: str) -> FeedLoader:
        if name not in self.feed_labels:
            raise ValueError(f"未知信息流：{name}")
        label = self.feed_labels[name]
        loader = XFeedLoader(await _x_session(f"「{label}」时间线"), name)
        await loader.load_more()  # 首批
        require_feed_items(loader.items, self.label, label)  # 0 条 = 大概率改版
        return loader

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        session = await _x_session("推文详情")
        # 转发（RT）的会话在原推那里：列表已把原推 id 放进 extra（见 parse._tweet_from_result）
        rest_id = str(item.extra.get("conversation_id") or item.id.rsplit("/", 1)[-1])
        entries, cursor = await _fetch_conversation(session, rest_id, None)
        return XTweetDetailLoader(session, rest_id, entries, cursor)
