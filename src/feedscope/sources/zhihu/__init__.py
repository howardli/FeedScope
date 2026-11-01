"""知乎源（zhihu）：热榜 / 推荐 / 关注 三个信息流 + 问题详情。

包内分层（全项目统一的源模板）：
- fetch.py  取数层：全部数据走免签 API（常驻通道页签的页面 fetch，带 x-zse-93
  版本头解锁 v4），不加载任何内容页面
- parse.py  解析层：API JSON -> 统一模型，纯函数可测
- __init__.py 编排层：Source 的能力声明（词汇表/登录）与 open_* 分派

知乎 2026 起在登录墙后：通道页签撞墙由 URL 复检成 LoginRequired。
"""

from __future__ import annotations

import re
from typing import ClassVar

from feedscope.core.models import FeedItem
from feedscope.core.registry import register
from feedscope.core.source import (
    DetailLoader,
    FeedLoader,
    Source,
    SourceError,
    StaticFeedLoader,
    require_feed_items,
)
from feedscope.sources.zhihu.fetch import (
    FOLLOW_API,
    HOT_API,
    RECOMMEND_API,
    ZhihuApiFeedLoader,
    _fetch_hot_list,
    _open_question,
)

# 各流的 (API URL, referer)：URL 逐字照抄页面自己发的首请求；referer 是页面
# 发该请求时所在的页面（fetch referrer 选项指定）。热榜页是 SSR、页面不发
# 列表请求（热榜走 www 网关的 v3 孪生门，全源流量统一 www 域），referer 借对应页面 URL。
_FEED_CTX = {
    "hot": (HOT_API, "https://www.zhihu.com/hot"),
    "recommend": (RECOMMEND_API, "https://www.zhihu.com/"),
    "follow": (FOLLOW_API, "https://www.zhihu.com/follow"),
}


@register
class ZhihuSource(Source):
    source_id = "zhihu"
    label = "知乎"
    source_key = "z"  # 一键直达键
    supports_detail = True
    supports_comments = True  # 详情屏 Enter 进楼层选择 -> 回答的评论层（按需抓取）
    feeds: ClassVar[list[str]] = ["hot", "recommend", "follow"]
    feed_labels: ClassVar[dict[str, str]] = {"hot": "热榜", "recommend": "推荐", "follow": "关注"}
    # 信息流只留问题卡（parse 层过滤想法/文章/视频等其余类型）：
    # 所有条目 Enter 都是 TUI 详情，无跳浏览器的分叉
    detail_kinds = frozenset({"", "question"})
    detail_unit_label = "回答"
    login_url = "https://www.zhihu.com/signin"  # 2026 起热榜/信息流都在登录墙后
    login_cookie = "z_c0"  # feedscope login 扫码后写入 profile 的判登 cookie
    logout_cookies: ClassVar[list[str]] = ["z_c0"]  # 登出清掉判登 cookie

    # ---- 信息流 ----

    async def open_feed(self, name: str) -> FeedLoader:
        if name in ("recommend", "follow"):
            url, referer = _FEED_CTX[name]
            loader = ZhihuApiFeedLoader(url, referer)
            await loader.load_more()  # 首批（通道页签在此创建并判登）
            require_feed_items(loader.items, self.label, name)  # 0 条 = 大概率改版
            return loader
        if name == "hot":
            url, referer = _FEED_CTX["hot"]
            items = require_feed_items(await _fetch_hot_list(referer), self.label, name)
            return StaticFeedLoader(items)
        raise ValueError(f"未知信息流：{name}")

    # ---- 问题详情 ----

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        m = re.search(r"question/(\d+)", item.url or "")
        if not m:
            raise SourceError("知乎 该条目没有问题 id，无法进详情")
        return await _open_question(m.group(1))
