"""Reddit 源（reddit）：首页/热门两个内置流 + subreddit 订阅表 + 帖子详情（评论树）。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：登录态网页 JSON 通道（www .json 端点，X 式 httpx cookie 会话）
- parse.py  解析层：Reddit JSON -> FeedItem / 帖子与评论树，纯函数可测
- __init__.py 编排层：Source 能力声明与 open_* 分派 + 内存分页 Loader

结构映射是 HN 式三层：帖子（自述帖 markdown 直传 / 链接帖原文一行 / 媒体
记号）-> 顶层评论当楼层（unit「评论」）-> 楼层内回复树当评论层（unit「回
复」）。comments 端点单请求给整树，楼层与评论翻页全在内存切片——详情打开
后评论层零额外请求（同 HN）。

feeds 是混形合成（介于知乎减法与 RSS 加法之间）：内置流 home=best /
popular=r/popular 走 hide 减法，[reddit.feeds] 订阅表（subreddit 名即 key，
url 存站内地址自解释）走加法——零订阅仍有内置流（与 RSS 的纯订阅合成不
同）。subscribable=True：F2 里 → 进订阅管理屏（与 RSS 共用一屏，按源分派
见 ui/screens/subscriptions.py 的 store）。

登录：2026 起匿名 .json 全线 403，本源必须登录。`feedscope login reddit`
弹 Chrome 手动输账号密码（+2FA），轮询 reddit_session 判登；token_v2 是
部分账号的迁移形态，判登与登出两枚都认（login.py 源无关零改动）。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core import config
from feedscope.core.browser import get_session, profile_cookies
from feedscope.core.const import DESKTOP_UA
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    CommentLoader,
    DetailLoader,
    FeedLoader,
    LoginRequired,
    Source,
    SourceError,
    require_feed_items,
)
from feedscope.sources.reddit.fetch import RedditFeedLoader, RedditSession, fetch_thread
from feedscope.sources.reddit.parse import SOURCE_ID, ThreadDetail, parse_thread

# 详情一次拿全树后，楼层/评论每页给多少（内存切片，不是请求）：与 HN 同宽
FLOOR_PAGE = 15
COMMENT_PAGE = 20


class RedditDetailLoader(DetailLoader):
    """Reddit 详情加载器：树已全在内存——楼层翻页与评论层都是切片，零额外请求。"""

    def __init__(self, thread: ThreadDetail, fallback_title: str = "") -> None:
        self._floors = thread.floors
        first = self._floors[:FLOOR_PAGE]
        self._given = len(first)
        self.doc = DetailDoc(
            title=thread.title or fallback_title,
            meta=thread.meta,
            time=thread.time,
            description_md=thread.description_md,
            answers=[f.answer for f in first],
        )

    @property
    def done(self) -> bool:
        return self._given >= len(self._floors)

    async def load_more(self) -> list[Answer]:
        if self.done:
            return []
        batch = self._floors[self._given : self._given + FLOOR_PAGE]
        self._given += len(batch)
        answers = [f.answer for f in batch]
        self.doc.answers.extend(answers)  # 「doc = 已加载全部」不变量
        return answers

    async def open_comments(self, index: int) -> RedditCommentsLoader:
        if not 0 <= index < len(self.doc.answers):
            raise SourceError("Reddit 该评论不在已加载范围内，先按 Ctrl+N 加载更多楼层")
        return RedditCommentsLoader(self._floors[index].comments)

    async def close(self) -> None:
        pass  # 树在内存，无资源


class RedditCommentsLoader(CommentLoader):
    """楼层回复树加载器：整串已在内存，翻页是本地切片（Ctrl+N 零请求）。"""

    def __init__(self, comments: list[Comment]) -> None:
        self.comments = comments[:COMMENT_PAGE]
        self._rest = comments[COMMENT_PAGE:]

    @property
    def done(self) -> bool:
        return not self._rest

    async def load_more(self) -> list[Comment]:
        if self.done:
            return []
        batch, self._rest = self._rest[:COMMENT_PAGE], self._rest[COMMENT_PAGE:]
        self.comments.extend(batch)  # 「comments = 已加载全部」不变量
        return batch

    async def close(self) -> None:
        pass


async def _reddit_session(what: str) -> RedditSession:
    """读 profile 全量 cookie 判登 + 本机 Chrome 真实 UA，打成 httpx 登录态。

    判登认两枚（账号迁移期 reddit_session / token_v2 互斥出现）：任一在即
    算已登录——比 X 的成对检查宽，漏判会把已登录用户反复弹去登录。
    """
    cookies = await profile_cookies("https://www.reddit.com")
    if not (cookies.get("reddit_session") or cookies.get("token_v2")):
        raise LoginRequired(f"Reddit 需要登录后才能查看{what}")
    return RedditSession(cookies, get_session().ua or DESKTOP_UA)


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
    login_cookie = "reddit_session"  # feedscope login 轮询的判登 cookie（token_v2 是迁移形态）
    logout_cookies: ClassVar[list[str]] = ["reddit_session", "token_v2"]  # 登出两枚都清

    # 内置流：流名 -> listing 路径（订阅流的路径是 r/{key}/hot，不在这张表里）
    _BUILTIN: ClassVar[dict[str, str]] = {"home": "best", "popular": "r/popular/hot"}
    _BUILTIN_LABELS: ClassVar[dict[str, str]] = {"home": "首页", "popular": "热门"}

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
        label = self.feed_labels.get(name)
        if label is None:
            raise ValueError(f"未知信息流：{name}")
        path = self._BUILTIN.get(name) or f"r/{name}/hot"  # 订阅流按 key 即 subreddit 名
        loader = RedditFeedLoader(await _reddit_session(f"「{label}」流"), path, label)
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

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        data = await fetch_thread(await _reddit_session("帖子详情"), item.id)
        return RedditDetailLoader(parse_thread(data, fallback_title=item.title))
