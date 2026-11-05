"""X 取数层：通道页签（知乎/Reddit 同款）——常驻 x.com 页签的页面 fetch 直调 GraphQL。

请求由真 Chrome 发出：TLS/头序/cookie 与登录会话完全自洽，cf_clearance
与 Chrome TLS 绑定的封锁（搜索端点已兑现过）对页内 fetch 天然不存在——
手动只补浏览器不会自动带的头：公开网页端 Bearer（多年稳定的公开常量）
+ X-Csrf-Token（页内从 ct0 cookie 就地读，与 X 自己的 SPA 同一动作）。

请求形状（queryId/variables/features）以种子常量起步（快照，零收割成本
零启动延迟），失效自动收割自愈（x_probe.py 的人工收割内建为自动）——
重开页签加载当前部署，收集它加载过的全部 JS chunk（resource entries 为主：
运行时动态加载的 chunk 只有那里可见）下载 grep queryId/operationName 对；
chunk 文件名自带端点名，按名优先下，通常一两个就命中。（2026-11 实测：
X 把自己的 GraphQL 请求从 resource timing 里隐藏了，「抄 SPA 发过的请求
URL」这条路被刻意封死，chunk 才是 qid 的家；页内下载 chunk 有 CORS 墙，
abs.twimg.com 对 httpx 无门禁。）请求报错（errors / 400/404/422，即形状
腐烂）时强制重割，形状确实换过才原参重试一次（Reddit 版本头的同款自愈
闭环）——X 改版换 queryId 从「跑脚本手抄常量」变成「当天自动恢复」；
重割也没换形状才报人话指引。

TweetDetail 的 features chunk 里 grep 不出（features 是调用时拼的，决定
响应形状，Grok 译文字段就在其中）：用种子常量；失效时懒深度截获一次——
瞬态页打开稳定推文（jack/status/20），网络层（page.on("request")）收
浏览器自己发的完整请求 URL——resource timing 隐不掉网络层——收割
qid+variables+features 后关页重试（focalTweetId/cursor 是每次调用的参数，
收割时剔除防锁死）。

401 语义唯一（auth_token 被服务端吊销，cookie 只是还躺在本地）：清失效
cookie（不清的话判登会一直被骗过）+ LoginRequired 走登录引导；403 是
风控/账号受限，重登救不了，提示里说清。搜索不做：SearchTimeline 双重
门禁——x-client-transaction-id（只有 X 自己的 JS 会算）+ cf_clearance
TLS 绑定，页内手动 fetch 同样进不去；「导航+网络层截获」原型验证过可
行但行为耦合重，为低频功能不值当——后人想恢复先读这段。
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from feedscope.core import http, samples
from feedscope.core.browser import ChannelTab, drop_profile_cookies, get_session, run_page
from feedscope.core.const import PAGE_TIMEOUT_MS
from feedscope.core.models import Answer, DetailDoc, FeedItem
from feedscope.core.source import DetailLoader, FeedLoader, LoginRequired, SourceError, dedup_append
from feedscope.sources.x.parse import (
    BASE,
    _detail_doc,
    _detail_entries_and_cursor,
    _entries_and_cursor,
    _focal_and_replies,
    _iter_tweet_results,
    _ordered_results,
    _reply_answer,
    _tweet_from_result,
)

_tab = ChannelTab(f"{BASE}/home", name="x")  # 停靠 x.com/home：同源 fetch 与 cookie 的前提

# 公开网页端 Bearer（多年稳定的公开常量）；浏览器不会自动带，页内手动补
_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# feed -> GraphQL operationName（For You 与 Following 是同族端点：同容器同变量集）
_TIMELINE_OPS: dict[str, str] = {"foryou": "HomeTimeline", "following": "HomeLatestTimeline"}
_DETAIL_OP = "TweetDetail"

# 深度截获用的稳定推文（jack 首推，多年存在）：打开它的详情页即触发 SPA 发 TweetDetail
_STABLE_TWEET = "https://x.com/jack/status/20"

# 每次调用按需拼接、绝不入缓存的变量（收割时剔除：烤进去会把所有详情锁死到截获推文）
_PER_CALL_VARS = ("cursor", "focalTweetId")

# 形状腐烂的状态码（queryId/变量集失效的典型信号）；401/403 是登录/风控语义、
# 429/5xx 是限流/抖动，都不该触发重割
_SHAPE_ROT = frozenset({400, 404, 422})


@dataclass
class _Op:
    """一个 GraphQL 端点的请求形状（收割或种子的最新成果）。"""

    qid: str
    variables: dict
    features: dict | None = None


# 种子（2026-11 抓包快照）：两条收割路都断时的保底，失效自愈会先走收割。
# 时间线必填变量集（缺一个都 422「must be defined」）；翻页由调用方加 cursor
_SEEDS: dict[str, _Op] = {
    "HomeTimeline": _Op(
        qid="fedFLLzmiCAuzPgA8lyh8Q",
        variables={
            "count": 20,
            "includePromotedContent": True,
            "latestControlAvailable": True,
            "requestContext": "launch",
            "withDownvotePerspectives": False,
            "withBirdwatchNotes": False,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": False,
            "withSuperFollowsUserFields": True,
            "withReactionsMetadata": False,
            "withReactionsPerspectives": False,
            "withSuperFollowsTweetFields": True,
            "withVoice": True,
            "withArticleRichContent": False,
        },
    ),
    "HomeLatestTimeline": _Op(
        qid="BKB7oi212Fi7kQtCBGE4zA",  # 同族端点：直收对方必填集即 200（2026-11 实测）
        variables={},  # 与 HomeTimeline 同变量集，运行时共用（见 _seed_for）
    ),
    "TweetDetail": _Op(
        qid="qpemjmVMoTWDvcRT43JIVQ",
        variables={
            "with_rux_injections": False,
            "rankingMode": "Relevance",
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
        },
        # features 决定响应形状（Grok 译文字段靠其中的 grok_show_grok_translated_post
        # 开关），chunk 里 grep 不出、只有真实请求截获才拿得到
        features={
            "rweb_video_screen_enabled": False,
            "rweb_cashtags_enabled": True,
            "profile_label_improvements_pcf_label_in_post_enabled": True,
            "responsive_web_profile_redirect_enabled": True,
            "rweb_tipjar_consumption_enabled": False,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "premium_content_api_read_enabled": False,
            "communities_web_enable_tweet_community_results_fetch": True,
            "c9s_tweet_anatomy_moderator_badge_enabled": True,
            "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
            "responsive_web_grok_analyze_post_followups_enabled": True,
            "rweb_cashtags_composer_attachment_enabled": True,
            "responsive_web_jetfuel_frame": True,
            "rweb_sports_post_context_enabled": True,
            "x_elections_post_context_enabled": True,
            "responsive_web_grok_share_attachment_enabled": True,
            "responsive_web_grok_annotations_enabled": True,
            "articles_preview_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "rweb_conversational_replies_downvote_enabled": False,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "content_disclosure_indicator_enabled": True,
            "content_disclosure_ai_generated_indicator_enabled": True,
            "responsive_web_grok_show_grok_translated_post": True,
            "responsive_web_grok_analysis_button_from_backend": True,
            "post_ctas_fetch_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": False,
            "responsive_web_nested_quote_preview_enabled": True,
            "responsive_web_grok_image_annotation_enabled": True,
            "responsive_web_grok_imagine_annotation_enabled": True,
            "responsive_web_made_on_x_label_enabled": False,
            "responsive_web_grok_community_note_auto_translation_is_enabled": True,
            "responsive_web_enhance_cards_enabled": False,
        },
    ),
}

_harvest: dict[str, _Op] = {}  # operationName -> _Op（收割成果，覆盖同源种子）

_HARVEST_WAIT = 1.0  # 重开页签后等数据 chunk 挂进 resource entries 的时长（测试置 0）


# ---- 页签里执行的 JS（页面 fetch / 收集 SPA 自己发过的 GraphQL URL / 收集 chunk URL）----

# GraphQL fetch：cookie/TLS/头序是真 Chrome 的，只补 Bearer 与 csrf（ct0 页内
# 就地读，X 自己的 SPA 同一动作）。失败按约定回 __status/__error 不抛异常
_FETCH_GQL_JS = """
async ([url, bearer]) => {
  try {
    const ct0 = (document.cookie.match(/(?:^|;\\s*)ct0=([^;]+)/) || [])[1] || "";
    const h = {
      "Authorization": "Bearer " + bearer,
      "X-Csrf-Token": ct0,
      "X-Twitter-Active-User": "yes",
      "X-Twitter-Client-Language": "zh-cn",
      "X-Twitter-Auth-Type": "OAuth2Session",
    };
    const r = await fetch(url, { headers: h, credentials: "include" });
    if (!r.ok) return { __status: r.status };
    return { __body: await r.json() };
  } catch (e) { return { __error: String(e) }; }
}
"""

# 当前部署加载过的 JS chunk URL 三路并集：resource entries（运行时动态
# 加载的 chunk 只有这里可见——2026-11 实测 X 把自己的 GraphQL 请求从
# resource timing 里隐藏了、但 JS chunk 照记）+ 页面 HTML 文本（x_probe 的
# 老路）+ script 标签。chunk 里是 queryId 的家，Python 侧下载 grep
_SCRIPT_URLS_JS = """
() => {
  const res = performance.getEntriesByType("resource").map(e => e.name);
  const inHtml = document.documentElement.outerHTML.match(/https:\\/\\/abs\\.twimg\\.com\\/[^"'\\s]+\\.js/g) || [];
  const tags = [...document.querySelectorAll("script[src]")].map(s => s.src);
  return [...new Set([...res, ...inHtml, ...tags])].filter(u => u.endsWith(".js"));
}
"""


def _parse_graphql_url(url: str) -> tuple[str, _Op] | None:
    """截获到的 GraphQL URL -> (operationName, 请求形状)；残缺/非目标形状返回 None。

    variables/features 从查询参数还原；每次调用的参数（cursor/focalTweetId）
    剔除——它们由调用方按次拼接，烤进缓存会把翻页/详情锁死到截获那次。
    （深度截获专用：resource timing 隐 API 请求，SPA 发过的 URL 拿不到了。）
    """
    parts = urllib.parse.urlsplit(url)
    m = re.search(r"/i/api/graphql/([^/]+)/([^/?]+)$", parts.path)
    if not m:
        return None
    qs = urllib.parse.parse_qs(parts.query)

    def _json_param(key: str) -> dict:
        try:
            data = json.loads(qs.get(key, ["{}"])[0])
            return (
                {k: v for k, v in data.items() if k not in _PER_CALL_VARS}
                if isinstance(data, dict)
                else {}
            )
        except ValueError:
            return {}

    return m.group(2), _Op(
        qid=m.group(1), variables=_json_param("variables"), features=_json_param("features") or None
    )


async def _download_chunk(url: str) -> str | None:
    """下载一个公开 CDN chunk（abs.twimg.com 无门禁，httpx 即可）；失败返回 None。"""
    try:
        r = await http.get(url, source="X chunk")
    except SourceError:
        return None
    return r.text


_QID_RE = re.compile(r'queryId:"([^"]+)",operationName:"([^"]+)"')


def _seed_for(op_name: str) -> _Op | None:
    """种子里取形状（收割缺这个端点时兜底）。"""
    op = _SEEDS.get(op_name)
    if op is None:
        return None
    if not op.variables and op_name == "HomeLatestTimeline":
        return _Op(
            qid=op.qid, variables=dict(_SEEDS["HomeTimeline"].variables)
        )  # 同族端点共用变量集
    return op


async def harvest() -> None:
    """重新收割时间线请求形状（失效自愈 / x_probe 巡检用）。

    先重开页签（停靠的 SPA 可能还是旧部署），等数据 chunk 挂进 resource
    entries，再收集当前部署的全部 chunk 下载 grep queryId/operationName 对。
    chunk 文件名自带端点名（如 shared~bundle.Compose~bundle.HomeTimeline~）：
    名字带缺口端点的优先下，通常一两个 chunk 就命中；只补缺口端点（收割
    成果不被只带 qid 的 grep 反复覆盖），variables 沿用已有形状。失败静默
    ——种子还在，取数不受阻。TweetDetail 不进 grep：种子 + 懒深度截获
    （_deep_capture_detail）已覆盖。
    """
    _tab.reset()
    try:
        await _tab.get()  # 重开页签（goto 完成），收割才算数
    except Exception:
        return  # 页签打不开等硬错误：交给种子兜底
    await asyncio.sleep(_HARVEST_WAIT)  # SPA 数据 chunk 异步加载，稍等它进 resource entries
    try:
        chunks = await _tab.eval(_SCRIPT_URLS_JS) or []
    except Exception:
        return
    missing = [op for op in _TIMELINE_OPS.values() if op not in _harvest]
    chunks = sorted(
        set(chunks),
        key=lambda u: (0 if any(op.lower() in u.lower() for op in missing) else 1, u),
    )
    for u in chunks:
        if "twimg.com" not in u or not u.endswith(".js"):
            continue
        text = await _download_chunk(u)
        if not text:
            continue
        for m in _QID_RE.finditer(text):
            op_name = m.group(2)
            if op_name not in missing:
                continue
            seed = _seed_for(op_name)
            if seed is not None:
                _harvest[op_name] = _Op(
                    qid=m.group(1), variables=dict(seed.variables), features=seed.features
                )
                missing.remove(op_name)
        if not missing:
            return


def _resolve(op_name: str) -> _Op | None:
    """当前生效的形状：收割优先、种子兜底。"""
    return _harvest.get(op_name) or _seed_for(op_name)


async def _deep_capture_detail() -> _Op | None:
    """懒深度截获 TweetDetail 形状：瞬态页开稳定推文，网络层收 SPA 自己发的请求。

    features 只有这条路拿得到（chunk grep 不出）；页面用完即关（run_page），
    不碰常驻通道页签。截不到（推文没了/改版没了这个端点）返回 None。
    """
    box: dict[str, str] = {}

    def on_request(req: Any) -> None:
        if "/i/api/graphql/" in req.url and req.url.split("?")[0].endswith(f"/{_DETAIL_OP}"):
            box["url"] = req.url

    async def _watch(page: Any) -> None:
        page.on("request", on_request)
        await page.goto(_STABLE_TWEET, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        for _ in range(30):  # 最多等 15 秒（渲染期请求，等它出现）
            if box:
                return
            await page.wait_for_timeout(500)

    await get_session().run(run_page, _watch)
    if not box or not (parsed := _parse_graphql_url(box["url"])):
        return None
    _harvest[_DETAIL_OP] = parsed[1]
    return parsed[1]


async def _raw(url: str, *, source: str) -> tuple[int | None, dict]:
    """一次页签 fetch -> (状态码或 None, body)；网络/结构失败转 SourceError。"""
    try:
        data = await _tab.eval(_FETCH_GQL_JS, [url, _BEARER])
    except Exception as exc:  # Playwright 层失败（重建重试也没救回来）
        raise SourceError(f"{source} 请求失败：{exc}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"{source} 返回结构异常")
    if err := data.get("__error"):
        raise SourceError(f"{source} 请求失败：{err}")
    status = data.get("__status")
    body = data.get("__body")
    if status is None and not isinstance(body, dict):
        raise SourceError(f"{source} 返回结构异常")
    return (int(status) if status else None), (body or {})


def _url(op_name: str, op: _Op, extra_vars: dict | None) -> str:
    """operationName + 形状 + 按次变量（cursor/focalTweetId）-> 完整 GraphQL URL。"""
    variables = dict(op.variables, **(extra_vars or {}))
    params = {"variables": json.dumps(variables)}
    if op.features:
        params["features"] = json.dumps(op.features)
    return f"{BASE}/i/api/graphql/{op.qid}/{op_name}?{urllib.parse.urlencode(params)}"


async def _graphql(op_name: str, extra_vars: dict | None, *, source: str) -> dict:
    """一次 GraphQL 调用 -> body；登录/风控语义转译，形状腐烂自动重割自愈一次。"""
    op = _resolve(op_name)
    if op is None:
        await harvest()
        op = _resolve(op_name)
        if op is None:
            raise SourceError(f"{source} 拿不到接口请求形状（收割与种子都缺），请反馈维护者")
    for attempt in range(2):
        url = _url(op_name, op, extra_vars)
        status, body = await _raw(url, source=source)
        if (
            status == 401
        ):  # auth_token 被服务端吊销：清失效 cookie（判登 cookie 在即算已登录会一直被骗）再引导重登
            await drop_profile_cookies("https://x.com", ("auth_token", "ct0"))
            raise LoginRequired("X 登录已失效（服务端已不认本地登录态），Ctrl+U 重新登录") from None
        if status == 403:
            raise SourceError(
                f"{source} 请求失败（HTTP 403）——可能被风控或账号受限，重新登录救不了，稍后再试或换网络"
            ) from None
        if status and status not in _SHAPE_ROT:
            raise SourceError(
                f"{source} 请求失败（HTTP {status}）：接口限流或抖动，稍后再试"
            ) from None
        if not status and not body.get("errors"):
            samples.save(samples.url_name(source, url), json.dumps(body, ensure_ascii=False))
            return body
        if attempt == 0:  # 形状腐烂（errors/400/404/422）：重割一次，形状确实换过才重试
            healed = await _deep_capture_detail() if op_name == _DETAIL_OP else None
            if healed is None:
                await harvest()
                healed = _resolve(op_name)
            if healed is None or healed == op:
                break
            op = healed
    raise SourceError(
        f"{source} 接口报错（请求形状可能已失效，自动收割未修复——跑 scripts/x_probe.py 诊断或反馈维护者）"
    )


async def _fetch_entries(feed: str, cursor: str | None) -> tuple[list[dict], str | None]:
    """时间线一次 GraphQL 调用 -> (entries, 下一页 cursor)；feed 见 _TIMELINE_OPS。"""
    op_name = _TIMELINE_OPS[feed]
    data = await _graphql(op_name, {"cursor": cursor} if cursor else None, source="X 时间线")
    return _entries_and_cursor(data)


async def _fetch_conversation(rest_id: str, cursor: str | None) -> tuple[list[dict], str | None]:
    """推文会话一次 GraphQL 调用 -> (entries, 回复翻页 cursor)。"""
    extra = {"focalTweetId": rest_id}
    if cursor:
        extra["cursor"] = cursor
    data = await _graphql(_DETAIL_OP, extra, source="X 详情")
    return _detail_entries_and_cursor(data)


class XFeedLoader(FeedLoader):
    """主页时间线（推荐/正在关注）：游标翻页 + 按推文 id 去重；不持有页签。

    done 在响应里没有 cursor-bottom 时为真（无限流通常永远有下一页，拉空只是抖动）。
    """

    def __init__(self, feed: str) -> None:
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._cursor: str | None = None  # None = 首页
        self._feed = feed
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[FeedItem]:
        if self._done:
            return []
        entries, cursor = await _fetch_entries(self._feed, self._cursor)
        self._cursor = cursor
        self._done = cursor is None
        return dedup_append(
            self.items, self._seen, (_tweet_from_result(r) for r in _iter_tweet_results(entries))
        )

    async def close(self) -> None:
        pass  # 通道页签进程级共享，不归单个 Loader 关


class XTweetDetailLoader(DetailLoader):
    """推文详情：焦点推文首批给出，回复沿 ShowMore cursor 翻页，按推文 id 去重。

    TweetDetail 的 cursor 无回复也一直发（0 回复推文也带游标），故「空页/无回复」
    直接判 done，否则 n 会无休空转。不持有页签（通道进程级共享）。
    """

    def __init__(self, rest_id: str, entries: list[dict], cursor: str | None) -> None:
        self._rest_id = rest_id
        self._cursor = cursor
        self._seen: set[str] = set()  # 已输出的推文 id（焦点 + 父推 + 回复，跨页去重）
        focal, parent, replies = _focal_and_replies(entries, rest_id)
        if not focal:
            raise SourceError("X 详情数据为空（推文可能已被删除或隐藏）")
        title, meta, desc, time = _detail_doc(focal, parent)
        self.doc = DetailDoc(title=title, meta=meta, description_md=desc, time=time)
        self._seen.add(str(focal.get("rest_id") or ""))
        if parent:
            self._seen.add(str(parent.get("rest_id") or ""))
        if not self._answers(replies):
            self._cursor = None  # 首批即无回复：会话确无回复，无谓的游标不再翻

    def _answers(self, results: list[dict]) -> list[Answer]:
        """推文 result 列表 -> Answer 批次（残卡丢弃），登记去重并写入 doc。"""
        new: list[Answer] = []
        for r in results:
            rid = str(r.get("rest_id") or "")
            if rid:
                self._seen.add(rid)
            a = _reply_answer(r)
            if a is not None:
                new.append(a)
        self.doc.answers.extend(new)
        return new

    @property
    def done(self) -> bool:
        return self._cursor is None

    async def load_more(self) -> list[Answer]:
        if self._cursor is None:
            return []
        entries, cursor = await _fetch_conversation(self._rest_id, self._cursor)
        self._cursor = cursor
        new = self._answers(_ordered_results(entries, self._seen))
        if not new:
            self._cursor = None  # 空页 = 真到头（游标会一直发，空页判死避免空转）
        return new

    async def close(self) -> None:
        pass  # 通道页签进程级共享，不归单个 Loader 关
