"""取数层：Reddit 走通道页签（知乎同款）——常驻 reddit.com 页签的页面 fetch。

2026 年起边缘对 .json 遗留端点全线 403（第三方通道关闭的一部分），且对
非浏览器画像（TLS/头序/HTTP2 指纹）的检查随时可能收紧——取数全部改由
常驻页签里的真 Chrome 发出（fetch credentials include）：TLS、头序、
cookie 与登录会话完全自洽，httpx 侧的头画像维护整体退役。网站自己的
HTML 全放行：页面（SSR，帖子/评论树直接织在 HTML 里）与翻页 partial
（text/vnd.reddit.partial+html）页签里都拿得到。

- 通道：进程级单例页签停在 www.reddit.com 上（同源 fetch 与 cookie 的
  前提，生命周期见 core/browser.ChannelTab），evaluate 撞上在途跳转/页签
  被关都重建重试一次；判登门在编排层（token_v2 JWT 解码，见
  __init__.py），门没过到不了这层
- 列表：流页面起步（首页 / 、热门 /r/popular/ 、订阅 /r/{sub}/ ），翻页
  跟随页面/片段里 faceplate-partial slot="load-after" 的 src 链（服务端
  给全参 URL，feeds/*-feed 与 community-more-posts 两族不用自己认）
- 详情：帖子 permalink 页 SSR 自带整棵评论树（1.5MB 级；深度大的树被
  服务端截断，解析层给「另有 N 条未展开」）
- x-reddit-client-version：随 Reddit 部署轮换，且浏览器不会自动带
  （SPA 自定义头）——照旧收割，但比 httpx 时代更便宜：服务端把版本织
  进每个页面 HTML，每个页面响应顺手正则一次（进程内缓存，首批 partial
  通常零额外请求），partial 403 时再专割一次自愈（版本确实轮换过才重试）
- 401/403 且重割无效时给「登录态可能已过期或被风控」的指引——绝不自动
  清 cookie（历史教训：误清即登录死循环）
- 样本落盘在 ChannelTab.fetch 里统一做（probe 巡检对着真实 HTML 修
  parse）

请求画像：刷流 1 页面 + N-1 片段（每片段 50-90 帖）、开帖 1 请求，远在
Reddit 登录态限流（约 100 次/分）之下。
"""

from __future__ import annotations

import re

from feedscope.core.browser import ChannelTab
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, SourceError, dedup_append
from feedscope.sources.reddit.parse import (
    parse_next_html,
    parse_posts_html,
    parse_subreddit_page,
)

_API = "https://www.reddit.com"

_tab = ChannelTab(
    f"{_API}/", name="reddit"
)  # 停靠 www.reddit.com/：同源 fetch（referer/cookie）的前提

# 一次 load_more 至少攒够的条数：SSR 页面只嵌 3-10 帖（站点首屏后自己
# 也会续拉一批），不足就顺着 load-after 链补齐——「一屏上下、翻页有货」
# （HN 30、X 20 的同款折中）
_MIN_BATCH = 20

# x-reddit-client-version 的形态（如 2026-11-04T13:46Z~456d6f1d）：部署时
# 轮换，服务端织进每个页面的 HTML
_VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z~[0-9a-f]{6,8}")

# partial 翻页的请求头（与 SPA 自己发的同形）；页面请求只需要 Accept html。
# referer 不手设：页签停在 www.reddit.com/ 上，同源 fetch 的自然 referer
# 就是它（2026-11 实测矩阵里 referer 在场即可，同站即同形）
_PARTIAL_ACCEPT = "text/vnd.reddit.partial+html, text/html;q=0.9"
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9"

_version: str | None = None  # 进程级缓存（页面响应顺手更新，403 自愈时重割）


async def _raw_fetch(
    url: str, accept: str, version: str | None, *, source: str
) -> tuple[int | None, str]:
    """一次通道 fetch -> (HTTP 状态码或 None, HTML 正文)；解包/报错/样本落盘见 ChannelTab.fetch。

    状态码原样上交（403 的自愈决策在 _get_html），成功时正文连同状态码
    一起返回——收割路径要 403 页不炸，取数路径才转错误。手动头只有
    Accept 与 partial 的版本头/retry 头（浏览器不会自动带的）。
    """
    headers = {"Accept": accept}
    if version:
        headers["x-reddit-client-version"] = version
        headers["x-reddit-retry"] = "attempt=0, max=2"
    status, body = await _tab.fetch(url, source=source, headers=headers, as_text=True)
    return status, (body or "")


async def _harvest_version() -> None:
    """专割一次版本头（从热门页 HTML）；失败保留旧值。

    页面响应的顺手收割（见 _get_html）让这条路通常只剩 403 自愈在走：
    partial 首用且缓存为空、或版本轮换后的重割。
    """
    global _version
    try:
        _, body = await _raw_fetch(f"{_API}/r/popular/", _HTML_ACCEPT, None, source="Reddit")
    except SourceError:
        return  # 收割失败保留旧值（可能是网络抖动，不是版本问题）
    if m := _VERSION_RE.search(body):
        _version = m.group(0)


async def _get_html(url: str, *, source: str, partial: bool = False) -> str:
    """一次通道 fetch -> HTML 文本；partial 首用前收割版本头，403 时重割自愈一次。"""
    global _version
    if partial and not _version:
        await _harvest_version()  # partial 必须带版本头：先割再用（空头发必 403）
    accept = _PARTIAL_ACCEPT if partial else _HTML_ACCEPT
    status, body = await _raw_fetch(url, accept, _version if partial else None, source=source)
    if status in (401, 403):
        if partial:  # 版本头是 partial 独有的变量：轮换过就新头原址重试一次
            saved = _version
            await _harvest_version()
            if _version and _version != saved:
                status, body = await _raw_fetch(url, accept, _version, source=source)
        if status:
            raise SourceError(
                f"{source} 请求失败（HTTP {status}）——登录态可能已过期或被风控，Ctrl+U 重新登录"
            ) from None
    if not partial and (m := _VERSION_RE.search(body)):
        _version = m.group(0)  # 服务端把版本织进每个页面：顺手收割，首批 partial 免一次专割
    return body


async def fetch_listing(url: str, *, source: str = "Reddit") -> tuple[list[FeedItem], str | None]:
    """一页列表（页面或片段 URL）-> (FeedItem, 下一页 URL)；None = 到底。"""
    html = await _get_html(url, source=source, partial="/svc/" in url)
    return parse_posts_html(html), parse_next_html(html)


async def fetch_thread_html(permalink: str, *, source: str = "Reddit 帖子") -> str:
    """帖子 permalink 页的 SSR HTML（解析见 parse.parse_thread_html）。"""
    return await _get_html(permalink, source=source)


async def fetch_subreddit(name: str) -> str:
    """r/{name}/ 页面校验存在性；返回页面自述的 subreddit 名（订阅添加用）。"""

    html = await _get_html(f"{_API}/r/{name}/", source=f"Reddit r/{name}")
    real = parse_subreddit_page(html)
    if not real:
        raise SourceError(f"r/{name} 不存在或不可访问")
    return real


class RedditFeedLoader(FeedLoader):
    """列表翻页：页面起步、跟随 load-after partial 链；链尽即到底。

    SSR 页面只内嵌 3-10 帖（站点自己也这样：首屏渲染几帖，余下靠前端
    续拉），一批不足 _MIN_BATCH 就顺着链继续拉到够——用户视角「首屏即
    一屏」，稀疏首屏只在链尽（真没更多）时出现。不持有页签（通道是
    进程级单例，随会话生灭）。
    """

    def __init__(self, page_url: str, label: str) -> None:
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
            items, nxt = await fetch_listing(url, source=f"Reddit「{self._label}」")
            self._next = nxt
            self.done = nxt is None
            added.extend(dedup_append(self.items, self._seen, items))
        return added
