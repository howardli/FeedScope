"""取数层：Reddit 走登录态网页 JSON 通道（www.reddit.com 的 .json 端点）。

2026 年起匿名 .json 全线 403、OAuth 免费层关闭，封锁的门槛是「要求登录
会话」——本源恰好持有 Chrome profile 登录态，正好打在门槛正确一侧：httpx
带 profile 全量 cookie + 本机 Chrome 真 UA（X 同款会话模式，见 XSession）。

- 列表：/best.json（首页）/r/popular/hot.json（热门）/r/{sub}/hot.json
  （订阅），游标 after（fullname），after 为 null = 到底
- 详情：/comments/{id36}.json 单请求给整棵评论树（limit/depth 截断，more
  的 count 走「另有 N 条」提示，不续拉）
- raw_json=1：关掉响应里的 HTML 实体转义（&amp; -> &），markdown 直传的前提
- 备选通道：403 波及登录态时把 _API 换成 old.reddit.com 即整层切换（.json
  路径同构）；再不行上 zhihu 式通道页签 page-fetch（最后兜底，见 zhihu/fetch）

登录态阅读的请求画像与 HN/V2EX 同级（刷流 1 请求、开帖 1 请求），远在
Reddit 登录态限流（约 100 次/分）之下。
"""

from __future__ import annotations

from dataclasses import dataclass

from feedscope.core import http
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, SourceError, dedup_append
from feedscope.sources.reddit.parse import parse_listing

_API = "https://www.reddit.com"  # .json 通道 host（备选 old.reddit.com，见模块头）

# 列表每页宽度：Reddit 上限 100；50 是「一屏上下、翻页有货」的折中（HN 30、X 20）
LIST_PAGE = 50

# 详情树的单请求截断：limit 是评论总数上限、depth 是嵌套深度上限；被截掉
# 的部分服务端以 more 对象续页（count 挂「另有 N 条」提示，不二次请求）。
# 500/10 实测覆盖绝大多数帖子；再大会显著拖慢大帖首开
THREAD_LIMIT = 500
THREAD_DEPTH = 10


@dataclass
class RedditSession:
    """httpx 直调的登录态：profile 全量 cookie + 本机 Chrome 真实 UA（X 同款）。

    两者都来自持久 profile 所在的浏览器会话（编排层 _reddit_session 注入），
    让 httpx 流量与登录会话同形。判登 cookie（reddit_session / token_v2）
    必在 jar 里（编排层判登保证）；Cookie 头按 jar 原顺序整串拼（同 X）。
    """

    cookies: dict[str, str]
    ua: str

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.ua,
            "Accept": "application/json",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
        }


async def _get_json(session: RedditSession, url: str, *, source: str, params: dict[str, str]) -> object:
    """一次 GET -> JSON；httpx 异常由 core/http 包成 SourceError，这里只补两条人话。"""
    try:
        r = await http.get(url, source=source, params=params, headers=session.headers())
    except SourceError as exc:
        # 带 cookie 还 401/403 = 大概率登录态过期或被风控（匿名才是全线封的年代）：
        # 给出下一步动作，别让用户对着裸状态码
        if "（HTTP 401）" in str(exc) or "（HTTP 403）" in str(exc):
            raise SourceError(f"{exc}——登录态可能已过期或被风控，Ctrl+U 重新登录") from None
        raise
    try:
        return r.json()
    except ValueError:
        raise SourceError(f"{source} 返回的不是 JSON，接口可能已变") from None


async def fetch_listing(
    session: RedditSession, path: str, after: str | None, *, source: str = "Reddit"
) -> tuple[list[dict], str | None]:
    """一个信息流的一页 listing -> (children, 下一页游标)；after 为 None = 到底。"""
    params = {"raw_json": "1", "limit": str(LIST_PAGE)}
    if after:
        params["after"] = after
    data = await _get_json(session, f"{_API}/{path}.json", source=source, params=params)
    d = data.get("data") if isinstance(data, dict) else None
    children = d.get("children") if isinstance(d, dict) else None
    if not isinstance(children, list):
        raise SourceError(f"{source} 返回结构异常，接口可能已变")
    nxt = d.get("after") if isinstance(d, dict) else None
    return [c for c in children if isinstance(c, dict)], (str(nxt) if nxt else None)


async def fetch_thread(session: RedditSession, id36: str, *, source: str = "Reddit 帖子") -> object:
    """一条帖子的 [帖子 listing, 评论 listing]（原始 JSON，解析见 parse.parse_thread）。"""
    data = await _get_json(
        session,
        f"{_API}/comments/{id36}.json",
        source=source,
        params={"raw_json": "1", "limit": str(THREAD_LIMIT), "depth": str(THREAD_DEPTH)},
    )
    if not isinstance(data, list) or len(data) < 2:
        raise SourceError(f"{source} 返回结构异常，接口可能已变")
    return data


async def fetch_about(session: RedditSession, name: str) -> dict:
    """r/{name}/about.json -> data dict：订阅添加时校验存在性 + 拿 display_name。"""
    data = await _get_json(
        session, f"{_API}/r/{name}/about.json", source=f"Reddit r/{name}", params={"raw_json": "1"}
    )
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict) or not str(d.get("display_name") or ""):
        raise SourceError(f"r/{name} 返回结构异常，接口可能已变")
    return d


class RedditFeedLoader(FeedLoader):
    """列表游标翻页：Reddit 的 after fullname；after 为 None 即真到头。"""

    def __init__(self, session: RedditSession, path: str, label: str) -> None:
        self._session = session
        self._path = path
        self._label = label
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._after: str | None = None
        self.done = False

    async def load_more(self) -> list[FeedItem]:
        if self.done:
            return []
        children, after = await fetch_listing(
            self._session, self._path, self._after, source=f"Reddit「{self._label}」"
        )
        self._after = after
        self.done = after is None
        return dedup_append(self.items, self._seen, parse_listing(children))

    async def close(self) -> None:
        pass  # 连接池由 core/http 进程级持有，无源内资源
