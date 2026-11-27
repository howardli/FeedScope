"""Reddit 源（reddit）：首页/热门两个内置流 + subreddit 订阅表 + 帖子详情（评论树）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：网站自己的 HTML 通道（shreddit SSR 页面 + faceplate
  partial 翻页），由常驻 reddit.com 通道页签的真 Chrome 发出（知乎同款，
  TLS/头序/cookie 与登录会话自洽；.json 遗留端点已全线 403）
- parse.py  解析层：shreddit HTML -> FeedItem / 帖子与评论树，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派 + 订阅管理三钩子

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
匿名态 sub="loid"，见 parse.parse_login_state）——Reddit 的判登 cookie 全不可
信：reddit_session 匿名流量也发、token_v2 光看在不在更不行；解码零网络
零请求，匿名流量伪造不出。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from feedscope.core import config
from feedscope.core.config import Subscription
from feedscope.core.errors import SourceError, SubscriptionError
from feedscope.core.i18n import t
from feedscope.core.loaders import FeedLoader, SliceDetailLoader
from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import Source, require_feed_items
from feedscope.sources.reddit import fetch
from feedscope.sources.reddit.fetch import RedditFeedLoader, fetch_thread_html
from feedscope.sources.reddit.parse import (
    SOURCE_ID,
    parse_login_state,
    parse_subreddit_input,
    parse_thread_html,
)


@register
class RedditSource(Source):
    source_id = SOURCE_ID
    label = "Reddit"
    supports_detail = True
    supports_comments = True  # 详情屏 Enter 进楼层的回复树（树已全在内存）
    subscribable = True  # feeds 除内置流外还来自 [reddit.feeds] 订阅表：F2 里 → 进订阅管理
    detail_kinds = frozenset({"link", "self"})  # 全部条目 Enter 都进终端详情
    detail_unit_label = "units.comment"  # 楼层单位词：顶层评论串（同 HN）
    comment_unit_label = "units.reply"  # 楼层内的讨论（同 HN）
    login_url = "https://www.reddit.com/login"
    login_cookie = "token_v2"
    logout_cookies: ClassVar[list[str]] = ["reddit_session", "token_v2"]  # 登出两枚都清

    # 内置流：流名 -> 列表页 URL（订阅流的页面是 https://www.reddit.com/r/{key}/）
    _BUILTIN: ClassVar[dict[str, str]] = {
        "home": "https://www.reddit.com/",
        "popular": "https://www.reddit.com/r/popular/",
    }
    _BUILTIN_LABELS: ClassVar[dict[str, str]] = {
        "home": "sources.reddit.feeds.home", "popular": "sources.reddit.feeds.popular",
    }
    # 订阅 key 不得撞内置流名（/r/home、/r/popular 真实存在，撞名会遮住内置流）：
    # 从 _BUILTIN 派生，新增内置流时保留字自动跟上（subscriptions 的拒收读这里）
    reserved_feed_keys = tuple(_BUILTIN)
    # 订阅管理：添加框收 subreddit 名或站内/站点地址（文案键见 subs.*）；删空不摘
    # enabled——首页/热门内置流仍在（与 rss / telegram 的纯订阅合成不同）
    sub_placeholder = "subs.reddit_placeholder"
    sub_hint = "subs.hint_basic"
    sub_empty = "subs.reddit_empty"
    sub_added = "subs.added_subreddit"
    drop_enabled_when_empty = False

    def logged_in(self, cookies: dict[str, str]) -> bool:
        """判登：token_v2 的 JWT 解码出登录态（parse.parse_login_state）——cookie 出现不算数
        （reddit_session / token_v2 匿名流量也发）。判登门、登录轮询、账号屏同一份判据。"""
        return parse_login_state(cookies.get("token_v2"))

    def login_evidence(self) -> str:
        return t("login.success_state")  # 报喜说「检测到登录态」而不是列 cookie 名

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
        await self.require_login(t("errors.what_feed", label=label))
        loader = RedditFeedLoader(page, label)
        await loader.load_more()  # 首批（判登在上一行 require_login 已完成）
        if name == "home" and not loader.items:
            # 首页是账号在 Reddit 站内「关注版块」的聚合：账号没关注任何版块
            # 就是真空（实测新号如此），不是改版——通用 0 条守卫会误导
            raise SourceError(t("errors.reddit_home_empty"))
        require_feed_items(loader.items, self.label_text, label)  # 0 条 = 大概率改版
        return loader

    # ---- 订阅管理钩子（通用编排在 services/subscriptions；判定顺序与文案不变）----

    def subscription_input(
        self, text: str, *, key: str | None, existing: list[Subscription]
    ) -> Subscription:
        # 四形态归一成裸 subreddit 名（parse_subreddit_input：rust / r/rust /
        # /r/rust/ / 站点地址）；订阅 key 即 subreddit 名，url 存站内地址自解释
        name = parse_subreddit_input(text)
        if not name:
            raise SubscriptionError(t("subs.subreddit_unrecognized"))
        return Subscription(key=name, label="", url=f"https://www.reddit.com/r/{name}/")

    def check_subscription(self, draft: Subscription, existing: list[Subscription]) -> None:
        # /r/home、/r/popular 真实存在：订阅 key 撞内置流名会遮住内置流
        if draft.key in self.reserved_feed_keys:
            raise SubscriptionError(t("subs.subreddit_reserved", name=draft.key))
        if any(f.key == draft.key for f in existing):
            raise SubscriptionError(t("subs.subreddit_dup", name=draft.key))

    async def probe_subscription(self, draft: Subscription) -> Subscription:
        try:
            await self.require_login(f"r/{draft.key}")
            real = await fetch.fetch_subreddit(draft.key)
        except SourceError as exc:  # 含 LoginRequired：订阅管理屏没有账号动线，消息自带指引
            raise SubscriptionError(
                t("subs.subreddit_failed", name=draft.key, error=exc)
            ) from None
        return Subscription(key=draft.key, label=real, url=draft.url)

    async def open_detail(self, item: FeedItem) -> SliceDetailLoader:
        await self.require_login(t("errors.what_post_detail"))
        html = await fetch_thread_html(item.url)  # 列表给的绝对 permalink（slug 全）
        # 1.5MB 级 SSR 页的解析 ~160ms：放工作线程跑，不卡事件循环（解析是
        # 纯函数；HTML2Text 已线程本地化，见 core.media.h2t）。换篇取消的是
        # await——线程会把旧结果跑完但协程已死、结果自然丢弃（线程无法取消，
        # 白算一次是换篇的既有代价）
        detail = await asyncio.to_thread(parse_thread_html, html, fallback_title=item.title)
        return SliceDetailLoader.from_detail(detail, source_label=self.label_text)
