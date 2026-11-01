"""知乎取数层：信息流走免签 API（常驻页签的页面 fetch），详情纯 API。

热榜/推荐/关注 2026-11 实测都有免签接口（只认 cookie，推荐/关注就是页面
自己在调的），请求由常驻 zhihu.com 通道页签的 fetch 发出——真 Chrome 的
TLS/自动头/cookie 与浏览器会话完全自洽（风控一致性优先，比 httpx 直调
安全）。回答翻页走 feeds API、评论走 comment_v5（同通道 fetch）。浏览器
页签约定：长活页签由 Loader 持有，close() 幂等关闭。raw dict / JSON ->
模型在 parse.py，编排在包 __init__。
"""

from __future__ import annotations

import re
from typing import Any

from feedscope.core.browser import close_page, get_session
from feedscope.core.const import PAGE_TIMEOUT_MS
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.source import (
    CommentLoader,
    DetailLoader,
    FeedLoader,
    LoginRequired,
    SourceError,
    dedup_append,
)
from feedscope.sources.zhihu.parse import (
    _api_comments,
    _api_question_doc,
    _feed_api_item,
    _feeds_answers,
    _hot_api_items,
)

# ---- 页面上下文 fetch（通道页签里执行的 JS：API 请求由真 Chrome 发出）----
# 不模拟点按钮/开浮层/滚屏：DOM 交互无法完整触发分页（2026-11 实测触底
# 不发分页请求），API 有标准游标分页（paging.next），同源 fetch 带 cookie。
# x-zse-93 是客户端版本声明（静态常量、页面每个 API 请求都带），但按端点
# 区别对待（2026-11 实测矩阵）：v4/questions 元信息端点必须带（否则 403
# 版本门禁 code 10003）；其余端点带上无害；唯独 feeds 无 cursor 入口反而
# 拒绝它——故按调用方显式传入，不全局统一。x-zse-96 签名一律不需要。
# 「N 条评论」= 根评论 + 子回复总数：root_comment 只带每根的热门子回复
# （1-2 条），其余靠 comment/{id}/child_comment 游标分页补全。
_CM_FIRST_URL = "https://www.zhihu.com/api/v4/comment_v5/answers/{aid}/root_comment?order_by=score&limit=20&offset="

_FETCH_JSON_JS = """
async ([url, referrer, zse93]) => {
  try {
    const h = { "x-requested-with": "fetch" };
    if (zse93) h["x-zse-93"] = "101_3_3.0";
    const opts = { headers: h, credentials: "include" };
    if (referrer) opts.referrer = referrer;  // 信息流按所在页面指定 referer（评论留空=自然 referer）
    const r = await fetch(url, opts);
    if (!r.ok) return { __status: r.status };
    return await r.json();
  } catch (e) { return { __error: String(e) }; }
}
"""


# 子回复补全（一条根评论一串游标翻页，根与根之间 Promise.all 并发——
# 串行 fetch 时热门楼层几十条子回复能把首页拖到 20s+）。offset 为空 =
# 从头拉（与 root 首页同语义）；seen 过滤已带出的热门子回复防重复。
_COMPLETE_CHILDREN_JS = """
async (roots) => {
  const out = {};
  await Promise.all(roots.map(async (r) => {
    const seen = new Set(r.seen || []);
    const kids = [];
    let url = `https://www.zhihu.com/api/v4/comment_v5/comment/${r.id}/child_comment?limit=50&offset=${r.offset}`;
    for (let i = 0; i < 6; i++) {  // limit=50 一页基本到底；留翻页余地
      try {
        const resp = await fetch(url, { headers: { "x-requested-with": "fetch", "x-zse-93": "101_3_3.0" }, credentials: "include" });
        if (!resp.ok) break;
        const d = await resp.json();
        kids.push(...(d.data || []).filter((k) => !k.is_delete && !seen.has(String(k.id))));
        url = d.paging && d.paging.next;
        if (!url || d.paging.is_end) break;
      } catch (e) { break; }
    }
    out[r.id] = kids;
  }));
  return out;
}
"""


def _raise_fetch_error(data: Any) -> None:
    """页面 fetch 的返回按约定自检：__status/__error 键转 SourceError。"""
    if not isinstance(data, dict):
        raise SourceError(f"接口返回异常：{type(data).__name__}")
    if status := data.get("__status"):
        raise SourceError(f"接口请求失败（HTTP {status}）")
    if err := data.get("__error"):
        raise SourceError(f"接口请求失败：{err}")


async def _fetch_json(page: Any, url: str, referrer: str | None = None, zse93: bool = False) -> dict:
    """页面上下文 fetch JSON API；非 2xx / 网络失败转 SourceError。

    referrer 不传 = 页签自然 referer（评论用：通道页 URL）；信息流传对应页面
    URL，与页面请求同形。zse93 见 _FETCH_JSON_JS 注释（按端点区别对待）。
    """
    data = await page.evaluate(_FETCH_JSON_JS, [url, referrer, zse93])
    _raise_fetch_error(data)
    return data


async def _comment_page(page: Any, url: str) -> tuple[list[Comment], str, bool]:
    """拉一页根评论（先把带隐藏子回复的根并发补全）；返回 (评论, next_url, is_end)。

    「还有没有子评论可拉」没有现成标识（2026-11 实测）：can_unfold 在
    root_comment 列表响应里恒 False（不是这个语义）；child_comment_next_offset
    只在「已带出>=1 条且后面还有」时给值，覆盖不了「一条没带但 count>0」
    的形态——缺口只能用 child_comment_count - len(child_comments) 算，
    这两个字段是服务端维护的准确记录。

    无缺口（count<=带出数：无子评论/子评论已全带出）不发请求，有缺口
    就拉全（并发下一次请求，留着「另有 1 条回复」的尾巴比多花一次请求
    更伤阅读）；more_replies 仅在补全失败（限流/风控）时兜底提示。
    """
    data = await _fetch_json(page, url)

    def _gap(r: dict) -> int:
        if r.get("is_delete"):
            return 0
        return (r.get("child_comment_count") or 0) - len(r.get("child_comments") or [])

    pending = [
        {
            "id": r.get("id"),
            "offset": r.get("child_comment_next_offset") or "",  # 空 = 从头拉
            "seen": [str(k.get("id")) for k in r.get("child_comments") or []],
        }
        for r in data.get("data") or []
        if _gap(r) >= 1
    ]
    if pending:
        try:
            filled: dict = await page.evaluate(_COMPLETE_CHILDREN_JS, pending) or {}
            for r in data.get("data") or []:
                kids = filled.get(str(r.get("id"))) or filled.get(r.get("id"))
                if kids:
                    r.setdefault("child_comments", []).extend(kids)
        except Exception:
            pass  # 补全失败：热门子集兜底
    paging = data.get("paging") or {}
    return _api_comments(data), paging.get("next") or "", bool(paging.get("is_end"))

# ---- 信息流 API（热榜/推荐/关注：v3 免签接口，只认 cookie）----
# 2026-11 实测：这几个接口不查 x-zse-96 签名（那是 v4 的要求），页面自己
# 首请求打的正是推荐/关注这两个 URL（limit=10&desktop=true 逐字相同），
# 有 paging.next 游标。热榜页是 SSR 不发列表请求——全部流量统一走 www 网关
# （api.zhihu.com/topstory/hot-list 是同一份数据的 App 域孪生门，已弃），
# 30 条上限无翻页（limit 参数无效，www/api.zhihu.com 两门实测等价）。热榜
# 元信息列放 detail_text 热度——提问人拿不到（DOM 无作者节点，API 的
# author 也是 id=0 占位），维持「放热度不放人名」的形态。
HOT_API = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50"
RECOMMEND_API = "https://www.zhihu.com/api/v3/feed/topstory/recommend?limit=10&desktop=true"
FOLLOW_API = "https://www.zhihu.com/api/v3/moments?limit=10&desktop=true"

# 各流的 referer = 页面自己发该请求时所在页面（fetch 的 referrer 选项指定）。
_CHANNEL_URL = "https://www.zhihu.com/"  # 通道页签停在 www 源上（cookie/CORS 的前提）

_channel_page: Any = None  # 进程级单例页签；随 BrowserSession 关闭，无需显式释放


async def _channel() -> Any:
    """取（必要时创建）常驻 fetch 通道页签：停在 zhihu.com 上，进程内复用。

    请求由真 Chrome 发出（TLS/自动头/最新 cookie 与浏览器会话完全自洽，
    风控一致性优先于 httpx 直调的速度差）。撞登录墙/页签被关都重建。
    """
    global _channel_page
    if _channel_page is not None and not _channel_page.is_closed():
        return _channel_page

    async def _open(ctx: Any) -> Any:
        page = await ctx.new_page()
        try:
            await _goto_logged_in(page, _CHANNEL_URL)  # goto + 登录墙检测
        except BaseException:
            await close_page(page)
            raise
        return page

    _channel_page = await get_session().run(_open)
    return _channel_page


async def _api_json(url: str, referer: str, zse93: bool = False) -> dict:
    """API 一次页面 fetch -> JSON；通道页签在途跳转时重建重试一次。"""
    for attempt in range(2):
        page = await _channel()
        try:
            return await _fetch_json(page, url, referer, zse93)
        except SourceError as exc:
            # 401 = 会话过期：转 LoginRequired 触发 UI 的登录引导（403 可能是
            # 风控不是过期，不并入）；其余接口层失败重建通道没用，原样抛
            if "HTTP 401" in str(exc):
                raise LoginRequired("知乎 登录已过期，需要重新登录") from exc
            raise
        except Exception:
            if attempt:
                raise
            global _channel_page
            _channel_page = None  # evaluate 撞上在途跳转（context destroyed）等：换页签再试


async def _fetch_hot_list(referer: str) -> list[FeedItem]:
    """热榜一次 API 调用拿全量（30 条上限，无翻页）。"""
    return _hot_api_items(await _api_json(HOT_API, referer))


# ---- 问题详情（纯 API，全程不加载页面）----
# 元信息：v4/questions/{qid}（include 把 detail/author/answer_count 等全带出，
# 2026-11 实测）。该端点的 403 是客户端版本门禁（err code 10003「请升级
# 客户端」），不是签名门禁：带 x-zse-93: 101_3_3.0（静态常量，页面每个
# API 请求都带，已在 _FETCH_JSON_JS 统一注入）即放行，x-zse-96 不需要。
# 回答：questions/{qid}/feeds 无 cursor 从头拉（与 SSR 首批一致，实测），
# 之后一路 paging.next；include 是页面请求原文（勿手改，改版失效时重新
# 抓包替换）。
QUESTION_API = (
    "https://www.zhihu.com/api/v4/questions/{qid}"
    "?include=detail%2Cexcerpt%2Canswer_count%2Cfollower_count%2Cvisit_count%2Cauthor%2Ccreated"
)
# 回答：feeds 无 cursor 从头拉（与 SSR 首批一致，实测）。注意无 cursor 入口
# 只吃这个精简 include 集——页面原文的完整 include 不带 cursor 会 403
# （2026-11 实测）；翻页跟服务端 paging.next（自带全套参数），无需长串。
_ANSWERS_FEEDS_TOP = (
    "https://www.zhihu.com/api/v4/questions/{qid}/feeds"
    "?include=data%5B%2A%5D.is_normal%2Ccomment_count%2Ccan_comment%2Ccontent%2Cvoteup_count%2C"
    "comment_permission%2Ccreated_time%2Cupdated_time%2Cquestion%2Cexcerpt%3B"
    "data%5B%2A%5D.author.follower_count%2Cvip_info"
    "&limit=5&order=default&platform=desktop"
)

# 开题一次 evaluate 并行拉元信息 + 首批回答（两请求独立，串行白等一个 RTT）；
# 版本头按端点契约：元信息带 x-zse-93（否则 403），feeds 无 cursor 入口不带（带了反而 403）
_OPEN_QUESTION_JS = """
async ([metaUrl, feedsUrl, referer]) => {
  const go = async (u, with93) => {
    try {
      const headers = { "x-requested-with": "fetch" };
      if (with93) headers["x-zse-93"] = "101_3_3.0";
      const r = await fetch(u, { headers, credentials: "include", referrer: referer });
      if (!r.ok) return { __status: r.status };
      return await r.json();
    } catch (e) { return { __error: String(e) }; }
  };
  const [meta, feeds] = await Promise.all([go(metaUrl, true), go(feedsUrl, false)]);
  return { meta, feeds };
}
"""


# ---- 页面操作 ----


async def _goto_logged_in(page: Any, url: str) -> None:
    """在已打开的页上导航，确认没撞上登录墙（2026 起热榜也在墙后）。"""
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    if "signin" in page.url:
        raise LoginRequired("知乎需要登录后才能查看此内容")


async def _open_question(qid: str) -> DetailLoader:
    """问题详情纯 API：元信息 questions API + 首批回答 feeds 无 cursor 从头拉。

    全程不加载问题页面——通道页签发两个请求即得（提问人/描述/计数 + 完整
    内容的前 5 条回答），之后 load_more 沿 paging.next 翻。
    """
    referer = f"https://www.zhihu.com/question/{qid}"
    args = [QUESTION_API.format(qid=qid), _ANSWERS_FEEDS_TOP.format(qid=qid), referer]
    for attempt in range(2):
        page = await _channel()
        try:
            r = await page.evaluate(_OPEN_QUESTION_JS, args)
            break
        except Exception:
            if attempt:
                raise
            global _channel_page
            _channel_page = None  # evaluate 撞上在途跳转等：换页签重试一次
    try:
        _raise_fetch_error(r["meta"])
    except SourceError as exc:
        if "404" in str(exc):  # 元信息端点 404 = 问题本身没了，别让用户看 HTTP 码猜
            raise SourceError("知乎 该问题可能已被删除或不存在") from exc
        raise
    _raise_fetch_error(r["feeds"])
    doc = _api_question_doc(r["meta"], _feeds_answers(r["feeds"], qid))
    paging = r["feeds"].get("paging") or {}
    return ZhihuQuestionLoader(doc, paging.get("next") or None, referer)


# ---- 增量 Loader（持活页签，close() 幂等）----


class ZhihuApiFeedLoader(FeedLoader):
    """游标分页 API 的信息流 Loader（推荐/关注）：请求走常驻通道页签。

    不持有页签（通道是进程级单例，随会话生灭）；done 只在响应明示没有下一页
    （is_end / next 空）时为真——拉空可能只是抖动，n 可再试；按 question id
    去重与首批共用 dedup_append。
    """

    def __init__(self, first_url: str, referer: str) -> None:
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._next = first_url
        self._referer = referer  # 与页面请求同形：按所在页面指定 referer
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[FeedItem]:
        if self._done or not self._next:
            self._done = True
            return []
        data = await _api_json(self._next, self._referer)
        paging = data.get("paging") or {}
        self._next = paging.get("next") or ""
        if not self._next or paging.get("is_end"):
            self._done = True
        return dedup_append(self.items, self._seen, (_feed_api_item(r) for r in data.get("data") or []))

    async def close(self) -> None:
        pass  # 通道页签进程级共享，不归单个 Loader 关


class ZhihuQuestionLoader(DetailLoader):
    """问题详情：纯 API（元信息 + 回答 feeds 游标翻页），不持有任何页签。

    回答按 url 去重防排序抖动；done 由 is_end/无 next/总数（meta 里的
    「N 个回答」）判定——拉空可能只是抖动，n 可再试。评论层共享通道页签。
    """

    def __init__(self, doc: DetailDoc, next_url: str | None, referer: str) -> None:
        self.doc = doc
        self._next = next_url
        self._referer = referer
        self._seen_urls = {a.url for a in doc.answers if a.url}
        self._done = not next_url
        m = re.search(r"(\d+)\s*个回答", doc.meta or "")
        self._total = int(m.group(1)) if m else None

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[Answer]:
        if self._done or self._next is None:
            self._done = True
            return []
        data = await _api_json(self._next, self._referer)
        qid = (re.search(r"questions/(\d+)/", self._next) or [None, ""])[1]
        new: list[Answer] = []
        for a in _feeds_answers(data, qid):
            if a.url and a.url in self._seen_urls:
                continue
            if a.url:
                self._seen_urls.add(a.url)
            new.append(a)
        self.doc.answers.extend(new)  # 维持「doc = 已加载全部」的不变量
        paging = data.get("paging") or {}
        self._next = paging.get("next") or None
        if not self._next or paging.get("is_end") or self._total is not None and len(self.doc.answers) >= self._total:
            self._done = True
        return new

    async def open_comments(self, index: int) -> ZhihuCommentsLoader:
        """按需抓第 index 个回答的评论首页（进评论层才调，走通道页签）。"""
        if not 0 <= index < len(self.doc.answers):
            raise SourceError("知乎 该回答不在已加载范围内，先在问题里按 n 加载更多")
        m = re.search(r"/answer/(\d+)", self.doc.answers[index].url or "")
        if not m:
            raise SourceError("知乎 拿不到该回答的 id，暂时无法进评论层")
        try:
            comments, next_url, _ = await _comment_page(
                await _channel(), _CM_FIRST_URL.format(aid=m.group(1))
            )
            return ZhihuCommentsLoader(comments, next_url)
        except SourceError:
            raise
        except Exception as exc:  # 评论打不开不算灾难：给出口让 UI 提示
            raise SourceError(f"知乎 评论区打开失败：{exc}") from exc

    async def close(self) -> None:
        pass  # 纯 API 无页签；通道进程级共享


class ZhihuCommentsLoader(CommentLoader):
    """单条回答的评论加载器：纯 comment_v5 API 游标翻页（root_comment 每页
    10 条 + 子回复补全），n 一路按到 is_end。不持有页签（通道现取）。
    """

    def __init__(self, comments: list[Comment], next_url: str) -> None:
        self.comments = comments
        self._next = next_url
        self._ids = {c.raw_id for c in comments}
        self._done = not next_url  # 没有下一页 = is_end

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[Comment]:
        if self._done or not self._next:
            self._done = True
            return []
        try:
            comments, next_url, is_end = await _comment_page(await _channel(), self._next)
        except SourceError:
            return []  # 单页失败不判死：可以再按 n 重试
        new = [c for c in comments if c.raw_id not in self._ids]
        for c in new:
            self._ids.add(c.raw_id)
        self.comments.extend(new)
        self._next = next_url
        if is_end or not next_url:
            self._done = True
        return new

    async def close(self) -> None:
        pass  # 纯 API 无页签；通道进程级共享
