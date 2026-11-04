"""X 取数层：httpx 直调 GraphQL（推荐/关注时间线 + 详情），不碰浏览器页面。

httpx 流量与登录会话的一致性对齐两处（2026-11）：cookie 带 profile 全量
jar（真浏览器每个请求都带整串，只挑 auth_token/ct0 是最显眼的偏差）；UA 用
探针修过的本机 Chrome 完整版（cf_clearance 类校验对 UA 一致性敏感）。两者
由编排层的 _x_session 注入（见 __init__.py），探针不可得才退截断版兜底。
头序与 x-client-transaction-id 不伪造——这些端点不校验（实测）。

搜索不做（全 app 已无搜索功能，2026-11 砍掉；X 这边的实测结论留档）：
SearchTimeline 双重门禁——x-client-transaction-id（页面 fetch 缺它即 403，
只有 X 自己的 JS 会算）+ cf_clearance 与 Chrome TLS 指纹绑定（httpx 拿到
也无效）。「导航搜索页 + 滚动触发 + 网络层收响应」的原型验证过可行，
但为低频功能引入第二条取数通道（持有页签 + 静默窗口判底）复杂度不值当；
后人想恢复搜索先读这段。

登录墙在 cookie：auth_token/ct0 判登在 __init__.py 的 _x_session 读 cookie
时做，本文件只负责取数；cookie 只是「还在本地」，被服务端吊销后 401 由
_graphql_json 自愈（清 cookie + LoginRequired，见其 docstring）。queryId /
bearer 抠自网页（时间线 queryId 从 main.js
grep，TweetDetail 的 queryId+features 从真实请求捕获重放——它的响应形状决定
有没有内嵌 Grok 译文，见 _DETAIL_FEATURES 与 parse._grok_translation）。X 改版
会变，失效时报「时间线/详情接口报错」，跑 scripts/x_probe.py 自救。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from feedscope.core import http
from feedscope.core.browser import drop_profile_cookies
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

# 从 main.js 抠出（X 改版会变）。bearer 是公开常量（多年稳定）；queryId 失效
# 时接口返回 422，改这里即可。For You 与 Following 是同族端点：同容器
# （data.home.home_timeline_urt）同变量集（2026-11 实测，For You 直收
# Following 的必填集即 200）。
_FOLLOWING_QUERY_ID = "BKB7oi212Fi7kQtCBGE4zA"  # HomeLatestTimeline
_FORYOU_QUERY_ID = "fedFLLzmiCAuzPgA8lyh8Q"  # HomeTimeline（推荐页签）
_TIMELINES: dict[str, tuple[str, str]] = {  # feed -> (operationName, queryId)
    "foryou": ("HomeTimeline", _FORYOU_QUERY_ID),
    "following": ("HomeLatestTimeline", _FOLLOWING_QUERY_ID),
}
_DETAIL_QUERY_ID = "qpemjmVMoTWDvcRT43JIVQ"  # TweetDetail（网页当前同款，失效跑 scripts/x_probe.py 重抓）
_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# HomeLatestTimeline 的必填变量集（实测缺一个都 422「must be defined」）；翻页加 cursor。
_VARIABLES = {
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
}


@dataclass
class XSession:
    """httpx 直调的登录态：profile 全量 cookie + 本机 Chrome 真实 UA。

    两者都来自持久 profile 所在的浏览器会话（编排层 _x_session 注入），让
    httpx 流量与登录会话同形。ct0 必在 jar 里（判登保证），csrf 头与 cookie
    同源成对；Cookie 头按 jar 原顺序整串拼（顺序即 Chrome 送出的顺序）。
    """

    cookies: dict[str, str]
    ua: str

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.ua,
            "Authorization": f"Bearer {_BEARER}",
            "X-Csrf-Token": self.cookies["ct0"],
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Client-Language": "zh-cn",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
        }


async def _graphql_json(url: str, variables: dict, session: XSession, features: dict | None = None) -> dict:
    """一次 GraphQL GET -> JSON；httpx 异常统一由 core/http 包成 SourceError。

    features 是 GraphQL 的功能开关集（与网页请求同形随调用传），时间线端点
    不校验可省，TweetDetail 的新持久化查询必带（见 _DETAIL_FEATURES）。

    401/403 的鉴别：X 的 401 语义唯一（auth_token 被服务端吊销/不认，
    ct0 同 jar 成对不存在错位问题），无需像 Reddit 那样探「我是谁」——
    清掉失效 cookie（判登 cookie 在即算已登录会一直被骗）转 LoginRequired
    走登录引导；403 则是风控/账号受限，重登救不了，提示里说清。
    """
    params = {"variables": json.dumps(variables)}
    if features is not None:
        params["features"] = json.dumps(features)
    try:
        r = await http.get(
            url,
            source="X",
            params=params,
            headers=session.headers(),
        )
    except SourceError as exc:
        if "（HTTP 401）" in str(exc):
            await drop_profile_cookies("https://x.com", ("auth_token", "ct0"))
            raise LoginRequired("X 登录已失效（服务端已不认本地登录态），Ctrl+U 重新登录") from None
        if "（HTTP 403）" in str(exc):
            raise SourceError(f"{exc}——可能被风控或账号受限，重新登录救不了，稍后再试或换网络") from None
        raise
    return r.json()


async def _fetch_entries(session: XSession, feed: str, cursor: str | None) -> tuple[list[dict], str | None]:
    """时间线一次 GraphQL 调用 -> (entries, 下一页 cursor)；feed 见 _TIMELINES。"""
    op, qid = _TIMELINES[feed]
    variables = dict(_VARIABLES)
    if cursor:
        variables["cursor"] = cursor
    url = f"{BASE}/i/api/graphql/{qid}/{op}"
    data = await _graphql_json(url, variables, session)
    if "errors" in data:
        raise SourceError("X 时间线接口报错（queryId/bearer 可能已失效，请反馈维护者）")
    return _entries_and_cursor(data)


# TweetDetail 必填变量集（与网页请求同形，2026-11 抓包；缺一个都 422「must be
# defined」）；翻页加 cursor。
_DETAIL_VARIABLES = {
    "with_rux_injections": False,
    "rankingMode": "Relevance",
    "includePromotedContent": True,
    "withCommunity": True,
    "withQuickPromoteEligibilityTweetFields": True,
    "withBirdwatchNotes": True,
    "withVoice": True,
}

# TweetDetail 的功能开关集（新持久化查询必带，缺了 400；与网页请求同形，
# 2026-11 抓包）。其中两枚直接决定译文字段在不在响应里：
# responsive_web_grok_show_grok_translated_post / graphql_is_translatable_…
# ——旧 queryId 的响应形状没有 Grok 译文，换端点就是为了它（见 parse._grok_translation）。
_DETAIL_FEATURES = {
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
}


async def _fetch_conversation(
    session: XSession, rest_id: str, cursor: str | None
) -> tuple[list[dict], str | None]:
    """推文会话一次 GraphQL 调用 -> (entries, 回复翻页 cursor)。"""
    variables = dict(_DETAIL_VARIABLES, focalTweetId=rest_id)
    if cursor:
        variables["cursor"] = cursor
    url = f"{BASE}/i/api/graphql/{_DETAIL_QUERY_ID}/TweetDetail"
    data = await _graphql_json(url, variables, session, _DETAIL_FEATURES)
    if "errors" in data:
        raise SourceError("X 详情接口报错（queryId 可能已失效，请反馈维护者）")
    return _detail_entries_and_cursor(data)


class XFeedLoader(FeedLoader):
    """主页时间线（推荐/正在关注）：游标翻页 + 按推文 id 去重；纯 httpx，不持有页签。

    done 在响应里没有 cursor-bottom 时为真（无限流通常永远有下一页，拉空只是抖动）。
    """

    def __init__(self, session: XSession, feed: str) -> None:
        self.items: list[FeedItem] = []
        self._seen: set[str] = set()
        self._cursor: str | None = None  # None = 首页
        self._session = session
        self._feed = feed
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[FeedItem]:
        if self._done:
            return []
        entries, cursor = await _fetch_entries(self._session, self._feed, self._cursor)
        self._cursor = cursor
        self._done = cursor is None
        return dedup_append(self.items, self._seen, (_tweet_from_result(r) for r in _iter_tweet_results(entries)))

    async def close(self) -> None:
        pass  # 无页签/浏览器资源；连接池由 core/http 统一持有




class XTweetDetailLoader(DetailLoader):
    """推文详情：焦点推文首批给出，回复沿 ShowMore cursor 翻页，按推文 id 去重。

    TweetDetail 的 cursor 无回复也一直发（0 回复推文也带游标），故「空页/无回复」
    直接判 done，否则 n 会无休空转。纯 httpx 无持久资源（同 XFeedLoader）。
    """

    def __init__(self, session: XSession, rest_id: str, entries: list[dict], cursor: str | None) -> None:
        self._session = session
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
        entries, cursor = await _fetch_conversation(self._session, self._rest_id, self._cursor)
        self._cursor = cursor
        new = self._answers(_ordered_results(entries, self._seen))
        if not new:
            self._cursor = None  # 空页 = 真到头（游标会一直发，空页判死避免空转）
        return new

    async def close(self) -> None:
        pass  # 无页签/浏览器资源；连接池由 core/http 统一持有
