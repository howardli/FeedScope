"""取数层：HN 走 Algolia 公开索引 API（免 key 免登录）——search 列表 + items/:id 整树。

两个端点都是单请求水合 JSON：列表一次 30 条全字段；详情一次给全评论树
（楼层/评论层的翻页全在内存切片，见 __init__ 的内存 Loader），fetch 层只有
两个无状态 GET 与一个翻页 Loader。索引有分钟级延迟、列表按 points 排序
（非官方榜序）——接受这两点换零解析成本（官方 Firebase 逐条取、官方
HTML 要解析 DOM）；真在意榜序时只换本文件的列表通道，树端点不动。
"""

from __future__ import annotations

from feedscope.core import http
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, SourceError, dedup_append
from feedscope.sources.hn.parse import parse_search

_API = "https://hn.algolia.com/api/v1"

# 各内置流的 (search 端点, 过滤参数)（feeds 词汇表与顺序见 __init__）：
# front_page 是「当前首页」快照（30 条）；ask/show 按 tag 过滤；new 按时间倒序
FEEDS: dict[str, tuple[str, dict[str, str]]] = {
    "front": (f"{_API}/search", {"tags": "front_page"}),
    "new": (f"{_API}/search_by_date", {"tags": "story"}),
    "ask": (f"{_API}/search", {"tags": "ask_hn"}),
    "show": (f"{_API}/search", {"tags": "show_hn"}),
}

HITS_PER_PAGE = 30  # 与 HN 官方首页同宽（front_page 恰好 30 条，一页装下）


async def fetch_feed(name: str, label: str, page: int) -> dict:
    """一个信息流的一页 search 结果（原始 JSON dict；解析/校验见 http.get_json）。"""
    url, base_params = FEEDS[name]
    return await http.get_json(
        url,
        source=f"HN「{label}」",
        params={**base_params, "hitsPerPage": HITS_PER_PAGE, "page": page},
        expect=dict,
    )


async def fetch_item(object_id: str) -> dict:
    """一条 story 的 items/:id 整棵评论树（原始 JSON dict；解析/校验见 http.get_json）。"""
    return await http.get_json(f"{_API}/items/{object_id}", source="HN 详情", expect=dict)


class HnFeedLoader(FeedLoader):
    """信息流加载器：search 按页翻（page 0 起、nbPages 到头），id 去重。

    索引重排会让跨页撞见已见条目（分数涨跌挤进挤出），dedup_append 与
    各源共用「同一 id 只出现一次」的不变量；无资源可释（连接池进程级共享）。
    """

    def __init__(self, name: str, label: str) -> None:
        self.items: list[FeedItem] = []
        self._name = name
        self._label = label
        self._seen: set[str] = set()
        self._page = 0  # 下一页页号（Algolia 0 起）
        self._nb_pages = 1

    @property
    def done(self) -> bool:
        return self._page >= self._nb_pages

    async def load_more(self) -> list[FeedItem]:
        if self.done:
            return []
        data = await fetch_feed(self._name, self._label, self._page)
        hits = data.get("hits")
        if not isinstance(hits, list):
            raise SourceError(f"HN「{self._label}」返回里没有 hits，接口可能已变")
        new = dedup_append(self.items, self._seen, parse_search(hits))
        self._page += 1
        try:
            self._nb_pages = max(1, int(data.get("nbPages") or 1))
        except (TypeError, ValueError):
            self._nb_pages = 1
        return new
