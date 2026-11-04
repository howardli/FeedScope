"""取数层：Reddit 走网站自己的 HTML 通道（shreddit SSR 页面 + faceplate
partial 翻页），httpx 直调。

2026 年起边缘对 .json 遗留端点全线 403（第三方通道关闭的一部分），但
网站自己的 HTML 全放行——页面（SSR，帖子/评论树直接织在 HTML 里）与
翻页 partial（text/vnd.reddit.partial+html）httpx 都拿得到，只要请求头
同形（2026-11 实测矩阵：UA/cookie/accept/x-reddit-client-version/referer
齐了 200，缺了或 UA 带 Headless 一律 403 封锁页）。

- 列表：流页面起步（首页 / 、热门 /r/popular/ 、订阅 /r/{sub}/ ），翻页
  跟随页面/片段里 faceplate-partial slot="load-after" 的 src 链（服务端
  给全参 URL，feeds/*-feed 与 community-more-posts 两族不用自己认）
- 详情：帖子 permalink 页 SSR 自带整棵评论树（1.5MB 级；深度大的树被
  服务端截断，解析层给「另有 N 条未展开」）
- x-reddit-client-version：随 Reddit 部署轮换，不硬编码——从任意页面
  HTML 收割、进程内缓存；403 时重割一次再试（自愈）
- 判登在编排层（token_v2 JWT 解码，见 __init__.py）；403 且重割无效时
  给「登录态可能已过期或被风控」的指引——绝不自动清 cookie（历史教训：
  误清即登录死循环）

请求画像：刷流 1 页面 + N-1 片段（每片段 50-90 帖）、开帖 1 请求，远在
Reddit 登录态限流（约 100 次/分）之下。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from feedscope.core import http
from feedscope.core.const import DESKTOP_UA
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, SourceError, dedup_append
from feedscope.sources.reddit.parse import parse_next_html, parse_posts_html

_API = "https://www.reddit.com"

# 一次 load_more 至少攒够的条数：SSR 页面只嵌 3-10 帖（站点首屏后自己
# 也会续拉一批），不足就顺着 load-after 链补齐——「一屏上下、翻页有货」
# （HN 30、X 20 的同款折中）
_MIN_BATCH = 20

# x-reddit-client-version 的形态（如 2026-11-04T13:46Z~456d6f1d）：部署时
# 轮换，从页面 HTML 收割
_VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z~[0-9a-f]{6,8}")

# partial 翻页的请求头（与 SPA 自己发的同形，2026-11 抓包）；页面请求只
# 需要 UA/cookie/Accept html
_PARTIAL_ACCEPT = "text/vnd.reddit.partial+html, text/html;q=0.9"
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9"

_version: str | None = None  # 进程级缓存（403 时重割）


@dataclass
class RedditSession:
    """httpx 直调的登录态：profile 全量 cookie + 本机 Chrome 真实 UA（X 同款）。

    cookie 与 UA 由编排层 _reddit_session 注入（判登门 token_v2 JWT 解码
    在那层做）；Cookie 头按 jar 原顺序整串拼。
    """

    cookies: dict[str, str]
    ua: str

    def headers(self, *, accept: str, referer: str, partial: bool) -> dict[str, str]:
        h = {
            "User-Agent": self.ua,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
        }
        if partial:
            h["x-reddit-client-version"] = _version or ""
            h["x-reddit-retry"] = "attempt=0, max=2"
        return h


async def _harvest_version(session: RedditSession) -> None:
    """从首页 HTML 收割 x-reddit-client-version（进程缓存；403 自愈时重割）。"""
    global _version
    try:
        r = await http.get(
            f"{_API}/r/popular/",
            source="Reddit",
            headers=session.headers(accept=_HTML_ACCEPT, referer=f"{_API}/", partial=False),
        )
    except SourceError:
        return  # 收割失败保留旧值（可能是网络抖动，不是版本问题）
    if m := _VERSION_RE.search(r.text):
        _version = m.group(0)


async def _get_html(
    session: RedditSession, url: str, *, source: str, referer: str, partial: bool = False
) -> str:
    """一次 GET -> HTML 文本；partial 首用前收割版本头，403 时重割自愈一次。"""
    global _version
    if partial and not _version:
        await _harvest_version(session)  # partial 必须带版本头：先割再用（空头发必 403）
    accept = _PARTIAL_ACCEPT if partial else _HTML_ACCEPT
    try:
        r = await http.get(url, source=source, headers=session.headers(accept=accept, referer=referer, partial=partial))
        return r.text
    except SourceError as exc:
        if "（HTTP 401）" not in str(exc) and "（HTTP 403）" not in str(exc):
            raise
        saved = _version
        await _harvest_version(session)
        if _version and _version != saved:  # 版本确实换过：旧头导致的 403，重试
            r = await http.get(
                url, source=source, headers=session.headers(accept=accept, referer=referer, partial=partial)
            )
            return r.text
        raise SourceError(f"{exc}——登录态可能已过期或被风控，Ctrl+U 重新登录") from None


async def fetch_listing(
    session: RedditSession, url: str, *, source: str = "Reddit"
) -> tuple[list[FeedItem], str | None]:
    """一页列表（页面或片段 URL）-> (FeedItem, 下一页 URL)；None = 到底。"""
    referer = url if url.startswith(f"{_API}/r/") else f"{_API}/"
    html = await _get_html(
        session, url, source=source, referer=referer, partial="/svc/" in url
    )
    return parse_posts_html(html), parse_next_html(html)


async def fetch_thread_html(session: RedditSession, permalink: str, *, source: str = "Reddit 帖子") -> str:
    """帖子 permalink 页的 SSR HTML（解析见 parse.parse_thread_html）。"""
    return await _get_html(session, permalink, source=source, referer=f"{_API}/")


async def fetch_about(session: RedditSession, name: str) -> dict:
    """r/{name} 页面校验存在性；返回 {"display_name": 名}（订阅添加用）。"""
    from feedscope.sources.reddit.parse import parse_subreddit_page

    html = await _get_html(
        session, f"{_API}/r/{name}/", source=f"Reddit r/{name}", referer=f"{_API}/"
    )
    real = parse_subreddit_page(html)
    if not real:
        raise SourceError(f"r/{name} 不存在或不可访问")
    return {"display_name": real}


class RedditFeedLoader(FeedLoader):
    """列表翻页：页面起步、跟随 load-after partial 链；链尽即到底。

    SSR 页面只内嵌 3-10 帖（站点自己也这样：首屏渲染几帖，余下靠前端
    续拉），一批不足 _MIN_BATCH 就顺着链继续拉到够——用户视角「首屏即
    一屏」，稀疏首屏只在链尽（真没更多）时出现。
    """

    def __init__(self, session: RedditSession, page_url: str, label: str) -> None:
        self._session = session
        self._page_url = page_url
        self._label = label
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._next: str | None = None
        self._started = False
        self.done = False

    async def load_more(self) -> list[FeedItem]:
        if self.done:
            return []
        added: list[FeedItem] = []
        while not self.done and len(added) < _MIN_BATCH:
            url = self._page_url if not self._started else (self._next or "")
            if not url:
                self.done = True
                break
            self._started = True
            items, nxt = await fetch_listing(self._session, url, source=f"Reddit「{self._label}」")
            self._next = nxt
            self.done = nxt is None
            added.extend(dedup_append(self.items, self._seen, items))
        return added

    async def close(self) -> None:
        pass  # 连接池由 core/http 进程级持有，无源内资源


def fallback_ua() -> str:
    """编排层兜底 UA（探针不可得时；正常路径用 BrowserSession 探针版）。"""
    return DESKTOP_UA
