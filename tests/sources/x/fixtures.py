"""X 源测试的夹具构造件：GraphQL 响应的最小可用形状（test_parse 与 test_fetch 共用）。"""

from __future__ import annotations


def tweet_result(
    rest_id: str,
    *,
    text: str = "推文",
    handle: str = "user",
    name: str = "作者",
    media: list[dict] | None = None,
    note: str | None = None,
    new_user: bool = False,
) -> dict:
    """一条 URT 推文 result（fetch 与 parse 的合同数据）。

    new_user=True 用 2026 新用户形态（TweetDetail 已迁移：name/screen_name 在
    user.core；HomeLatestTimeline 仍是旧 legacy 形态）。
    """
    legacy: dict = {"full_text": text, "entities": {"media": media or []}}
    if new_user:
        user_result: dict = {"core": {"screen_name": handle, "name": name}}
    else:
        user_result = {"legacy": {"screen_name": handle, "name": name}}
    r: dict = {
        "rest_id": rest_id,
        "legacy": legacy,
        "core": {"user_results": {"result": user_result}},
    }
    if note is not None:
        r["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    return r


def entry(
    *,
    entry_id: str,
    item_content: dict | None = None,
    cursor_value: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """构造一条 URT entry：item / cursor / module 三选一。"""
    if cursor_value is not None:
        return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineCursor", "value": cursor_value}}
    if items is not None:
        return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineModule", "items": items}}
    return {"entryId": entry_id, "content": {"entryType": "TimelineTimelineItem", "itemContent": item_content or {}}}


def item_content(result: dict | None = None, promoted: bool = False) -> dict:
    it: dict = {}
    if promoted:
        it["promotedMetadata"] = {"adMetadataContainer": {}}
    if result is not None:
        it["tweet_results"] = {"result": result}
    return it


def showmore_cursor(value: str) -> dict:
    """ShowMore 回复游标条目（itemContent 形态；线上主形态是 cursor-bottom 的 content.value）。"""
    return {
        "entryId": "cursor-showmorethread-0",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {"itemType": "TimelineTimelineCursor", "value": value},
        },
    }


def conv_data(entries: list[dict], cursor: str | None = None) -> dict:
    """TweetDetail 响应 fixture（游标用线上实测的 cursor-bottom + content.value 形态）。"""
    es = entries + ([entry(entry_id="cursor-bottom-0", cursor_value=cursor)] if cursor else [])
    return {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
        {"type": "TimelineAddEntries", "entries": es}
    ]}}}


def wrapped_result(result: dict) -> dict:
    """TweetWithVisibilityResults 拆包形态：result 套一层 .tweet。"""
    return {"__typename": "TweetWithVisibilityResults", "tweet": result}
