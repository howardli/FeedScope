"""取数层：V2EX 走官方 v1 公开 API（免 token 免登录）——列表 + 全量回复。

端点都是单请求水合 JSON：hot 10 条 / latest 40 条一页全量（无分页参数，
列表到头即止）；replies/show.json 实测忽略 page、一次返回全部回复——
楼层翻页全在内存切片（见 __init__ 的内存 Loader），fetch 层只有两个
无状态 GET。正文不走网络：列表端点的 content 就是全量（透传见 parse）。

频率限制官方未明示（社区口径约 120 次/时/IP）：刷一次流 1 请求、开一篇
详情 1 请求，正常阅读离上限很远。要按节点订阅时另有 v2 API（需 Personal
Access Token）与 v1 的 node_name 查询，不在 MVP 这层。
"""

from __future__ import annotations

from feedscope.core import http

_API = "https://www.v2ex.com/api"

# 各内置流的端点（feeds 词汇表与顺序见 __init__）
FEEDS: dict[str, str] = {
    "hot": f"{_API}/topics/hot.json",
    "new": f"{_API}/topics/latest.json",
}


async def _get_list(url: str, source: str, params: dict[str, str] | None = None) -> list[dict]:
    """GET 一个返回数组的端点 -> 原始 dict 列表（解析/校验见 http.get_json；非 dict 成员剔除）。"""
    data = await http.get_json(url, source=source, params=params, expect=list)
    return [d for d in data if isinstance(d, dict)]


async def fetch_topics(name: str, label: str) -> list[dict]:
    """一个信息流的全部主题（v1 一页全量，无翻页）。"""
    try:
        url = FEEDS[name]
    except KeyError:
        raise ValueError(f"未知信息流：{name}") from None
    return await _get_list(url, f"V2EX「{label}」")


async def fetch_replies(topic_id: str) -> list[dict]:
    """一条主题的全部回复（v1 端点一次给全量，page 参数形同虚设）。"""
    return await _get_list(f"{_API}/replies/show.json", "V2EX 回复", params={"topic_id": topic_id})
