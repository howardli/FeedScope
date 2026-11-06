"""X（Twitter）源（x）：推荐/正在关注时间线 + 推文详情（正文与回复）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：常驻 x.com 通道页签的页面 fetch 直调 GraphQL + 运行时
  收割请求形状 + 游标翻页 Loader（搜索为何不做，见其模块头）
- parse.py  解析层：URT JSON -> FeedItem / 详情，纯函数可测
- __init__.py 编排层：Source 的能力声明（词汇表/登录）与 open_* 分派

全部能力在登录墙后：`feedscope login x` 弹有头 Chrome 手动输账号密码（+2FA），
轮询到 auth_token 即成功（login.py 源无关，零改动）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    Source,
    login_gate,
)
from feedscope.sources.x.fetch import (
    XFeedLoader,
    XTweetDetailLoader,
    fetch_conversation,
)


async def require_login(what: str) -> None:
    """判登门：auth_token/ct0 成对在 profile 里即已登录（通道页签的 cookie 由浏览器自带）。"""
    await login_gate(
        "https://x.com",
        lambda c: bool(c.get("auth_token") and c.get("ct0")),
        label="X",
        what=what,
    )


@register
class XSource(Source):
    source_id = "x"
    label = "X"
    supports_detail = True
    feeds: list[str] = ["foryou", "following"]  # 与 X 主页页签同序：默认进「推荐」
    feed_labels: dict[str, str] = {"foryou": "推荐", "following": "正在关注"}
    detail_kinds = frozenset({"tweet"})  # Enter 进终端详情屏（正文 + 回复）
    detail_unit_label = "回复"
    login_url = "https://x.com/login"
    login_cookie = "auth_token"  # feedscope login 轮询的判登 cookie（手动输密码后写入）
    logout_cookies: ClassVar[list[str]] = ["auth_token", "ct0"]  # 登出清掉判登成对 cookie

    async def open_feed(self, name: str) -> FeedLoader:
        label = self.label_of(name)
        await require_login(f"「{label}」时间线")
        return await self.first_batch(XFeedLoader(name), label)

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        await require_login("推文详情")
        # 转发（RT）的会话在原推那里：列表已把原推 id 放进 extra（见 parse._tweet_from_result）
        rest_id = str(item.extra.get("conversation_id") or item.id.rsplit("/", 1)[-1])
        entries, cursor = await fetch_conversation(rest_id, None)
        return XTweetDetailLoader(rest_id, entries, cursor)
