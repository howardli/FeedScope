"""Reddit 源（reddit）：首页/热门两个内置流 + subreddit 订阅表 + 帖子详情（评论树）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：网站自己的 HTML 通道（shreddit SSR 页面 + faceplate
  partial 翻页），由常驻 reddit.com 通道页签的真 Chrome 发出（知乎同款，
  TLS/头序/cookie 与登录会话自洽；.json 遗留端点已全线 403）
- parse.py  解析层：shreddit HTML -> FeedItem / 帖子与评论树，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派 + 内存分页 Loader

结构映射是 HN 式三层：帖子（自述帖 HTML 转 markdown / 链接帖原文一行 /
媒体记号）-> 顶层评论当楼层（unit「评论」）-> 楼层内回复树当评论层
（unit「回复」）。permalink 页单请求 SSR 自带整树，楼层与评论翻页全在
内存切片——详情打开后评论层零额外请求（同 HN）。

feeds 是混形合成（介于知乎减法与 RSS 加法之间）：内置流 home=首页 /
popular=r/popular 走 hide 减法，[reddit.feeds] 订阅表（subreddit 名即
key，url 存站内地址自解释）走加法——零订阅仍有内置流（与 RSS 的纯订阅
合成不同）。subscribable=True：F2 里 → 进订阅管理屏（与 RSS 共用一屏，
按源分派见 ui/screens/subscriptions.py 的 store）。

登录：本源必须登录。`feedscope login reddit` 弹 Chrome 手动输账号密码
（+2FA）。判据一律是 token_v2 的 JWT 本地解码（登录态 sub="user"、
匿名态 sub="loid"，见 _token_logged_in）——Reddit 的判登 cookie 全不可
信：reddit_session 匿名流量也发、token_v2 光看在不在更不行；解码零网络
零请求，匿名流量伪造不出。
"""

from __future__ import annotations

import asyncio
import base64
import json as _json
from typing import ClassVar

from feedscope.core import config
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    FeedLoader,
    SliceDetailLoader,
    Source,
    SourceError,
    login_gate,
    require_feed_items,
)
from feedscope.sources.reddit.fetch import RedditFeedLoader, fetch_thread_html
from feedscope.sources.reddit.parse import SOURCE_ID, parse_thread_html


class RedditDetailLoader(SliceDetailLoader):
    """Reddit 详情加载器：树已全在内存——楼层翻页与评论层都是切片，零额外请求。

    全部结构在 SliceDetailLoader.from_detail（楼层/评论树成对进切片），
    本类只声明报错文案里的源标签（楼层页宽用基类默认）。"""

    source_label = "Reddit"


def _token_logged_in(token_v2: str | None) -> bool:
    """token_v2 的 JWT claims 是登录的本地铁证：登录态 sub="user"（匿名态 sub="loid"）。

    Reddit 的判登 cookie 全都不可信：reddit_session 匿名流量也发（实测打开
    登录页即出现）、token_v2 光看在不在更不行——但 token_v2 本体是 JWT，
    登录后 Reddit 重签的版本 claims 里 sub 从 "loid"（Logged-Out ID）变成
    "user" 且带 at=1/aid=账号id（2026-11 实测两种形态各解码一次）。本地
    解码零请求、匿名流量伪造不出，是判登/登录轮询的地面真值。
    """
    if not token_v2:
        return False
    try:
        payload = token_v2.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("sub") == "user"
    except (IndexError, ValueError):
        return False


async def _login_verified(ctx) -> bool:
    """登录成功的浏览器级判据（login.py 轮询 login_verify）：token_v2 解码出登录态。

    读登录窗口上下文的 cookie、本地解码 JWT（见 _token_logged_in）——零网络
    （这台网络 www 的 me.json 连登录会话都 403，问不了服务端）、零页面
    依赖（登录中途跳 accounts 域也不影响）。登录完成 Reddit 重签 token_v2
    的瞬间即可检出。
    """
    cookies = await ctx.cookies("https://www.reddit.com")
    jar = {c["name"]: c["value"] for c in cookies}
    return _token_logged_in(jar.get("token_v2"))


async def require_login(what: str) -> None:
    """判登门：token_v2 JWT 过门（通道页签的 cookie 由浏览器自带，无需另建会话）。

    公共 API：subscriptions 的 subreddit 添加入口也过这道门（订阅管理屏
    没有账号动线，LoginRequired 的消息自带指引）。cookie 读取与消息模板
    在 core.source.login_gate，判据（JWT 解码）是本源差异。
    """
    await login_gate(
        "https://www.reddit.com",
        lambda c: _token_logged_in(c.get("token_v2")),
        label="Reddit",
        what=what,
    )


@register
class RedditSource(Source):
    source_id = SOURCE_ID
    label = "Reddit"
    supports_detail = True
    supports_comments = True  # 详情屏 Enter 进楼层的回复树（树已全在内存）
    subscribable = True  # feeds 除内置流外还来自 [reddit.feeds] 订阅表：F2 里 → 进订阅管理
    detail_kinds = frozenset({"link", "self"})  # 全部条目 Enter 都进终端详情
    detail_unit_label = "评论"  # 楼层单位词：顶层评论串（同 HN）
    comment_unit_label = "回复"  # 楼层内的讨论（同 HN）
    login_url = "https://www.reddit.com/login"
    login_cookie = "token_v2"  # 兜底显示用（真实判据是 login_verify 的 JWT 解码）
    login_verify = staticmethod(
        _login_verified
    )  # 登录成功 = token_v2 解码出登录态（cookie 出现不算数）
    logout_cookies: ClassVar[list[str]] = ["reddit_session", "token_v2"]  # 登出两枚都清

    # 内置流：流名 -> 列表页 URL（订阅流的页面是 https://www.reddit.com/r/{key}/）
    _BUILTIN: ClassVar[dict[str, str]] = {
        "home": "https://www.reddit.com/",
        "popular": "https://www.reddit.com/r/popular/",
    }
    _BUILTIN_LABELS: ClassVar[dict[str, str]] = {"home": "首页", "popular": "热门"}
    # 订阅 key 不得撞内置流名（/r/home、/r/popular 真实存在，撞名会遮住内置流）：
    # 从 _BUILTIN 派生，新增内置流时保留字自动跟上（subscriptions 的拒收读这里）
    reserved_feed_keys = tuple(_BUILTIN)

    def __init__(self) -> None:
        """混形合成：内置流剔 hide（减法）+ 订阅表各成一流（加法，RSS 同款）。

        不调 super().__init__()——它的合成规则只对类默认流做减法；这里 feeds
        还要拼 [reddit.feeds] 的订阅 key。订阅 key 与内置流撞名（/r/home、
        /r/popular 真实存在）在添加入口已拦，手编配置漏网的在这里静默跳过。
        """
        hidden = set(config.hidden_feeds(self.source_id))
        subs = [
            f
            for f in config.feeds_for(self.source_id)
            if f.key not in hidden and f.key not in self._BUILTIN
        ]
        self._subs = {f.key: f for f in subs}
        self.feeds = [f for f in self._BUILTIN if f not in hidden] + list(self._subs)
        self.feed_labels = {f: self._BUILTIN_LABELS[f] for f in self._BUILTIN if f not in hidden}
        self.feed_labels.update({k: f.label for k, f in self._subs.items()})

    async def open_feed(self, name: str) -> FeedLoader:
        label = self.label_of(name)
        page = (
            self._BUILTIN.get(name) or f"https://www.reddit.com/r/{name}/"
        )  # 订阅 key 即 subreddit 名
        await require_login(f"「{label}」流")
        loader = RedditFeedLoader(page, label)
        await loader.load_more()  # 首批（判登在此抛 LoginRequired）
        if name == "home" and not loader.items:
            # 首页是账号在 Reddit 站内「关注版块」的聚合：账号没关注任何版块
            # 就是真空（实测新号如此），不是改版——通用 0 条守卫会误导
            raise SourceError(
                "Reddit「首页」是你账号在站内关注版块的聚合，当前为空——"
                "去 reddit.com 关注一些版块，或 F2 → Reddit 添加 subreddit 订阅（单独成流）"
            )
        require_feed_items(loader.items, self.label, label)  # 0 条 = 大概率改版
        return loader

    async def open_detail(self, item: FeedItem) -> SliceDetailLoader:
        await require_login("帖子详情")
        html = await fetch_thread_html(item.url)  # 列表给的绝对 permalink（slug 全）
        # 1.5MB 级 SSR 页的解析 ~160ms：放工作线程跑，不卡事件循环（解析是
        # 纯函数；HTML2Text 已线程本地化，见 core.media.h2t）。换篇取消的是
        # await——线程会把旧结果跑完但协程已死、结果自然丢弃（线程无法取消，
        # 白算一次是换篇的既有代价）
        detail = await asyncio.to_thread(parse_thread_html, html, fallback_title=item.title)
        return RedditDetailLoader.from_detail(detail)
