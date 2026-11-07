"""`feedscope probe`：源巡检——每个源的每个信息流拉一次首批，改版当天一条命令定位。

- 每流一行：[OK] N 条 / [SKIP] 未登录（LoginRequired，feedscope login 后可测）/
  [FAIL] 错误消息（含首屏 0 条守卫的「可能已改版」）
- 首个成功流里挑一条可进详情的再测 open_detail：标题/正文/楼层全空才算坏
  （0 回复的帖子是正常数据，不算）
- 默认开样本落盘（FEEDSCOPE_SAMPLE_DIR 未设时设到 ~/.cache/feedscope/samples/）：
  站点改版后对着真实样本修 parse.py、补 fixture，或把样本交给 AI 重新生成解析器
- 退出码：有 FAIL = 1（可挂 CI 定时跑，改版当天就知道哪个源坏了）；未知源 id = 2
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Literal

from platformdirs import user_cache_path

import feedscope.sources  # noqa: F401  import 即注册，否则巡检不到任何源
from feedscope.core import http
from feedscope.core.browser import close_session
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources
from feedscope.core.source import FeedLoader, LoginRequired, Source, SourceError
from feedscope.report import line as _line
from feedscope.core.i18n import t


def _classify(exc: BaseException) -> tuple[Literal["SKIP", "FAIL"], str]:
    """异常 -> (状态, 消息)：LoginRequired=SKIP、SourceError=FAIL、其余兜住也算 FAIL。

    巡检要兜住一切——崩溃也是发现（取数/详情两处共用同一套分类）。"""
    if isinstance(exc, LoginRequired):
        return "SKIP", str(exc)
    if isinstance(exc, SourceError):
        return "FAIL", str(exc)
    return "FAIL", t("probe.unexpected", exc=repr(exc))


async def _try_open_feed(
    source: Source, name: str
) -> tuple[Literal["OK", "SKIP", "FAIL"], FeedLoader | None, str]:
    """打开一个流，返回 (状态, loader, 消息)；异常一律兜住转成状态行。"""
    label = f"{source.label_text} {name}"
    try:
        return "OK", await source.open_feed(name), label
    except Exception as exc:
        state, msg = _classify(exc)
        return state, None, f"{label} - {msg}"


async def _probe_source(source: Source, stats: dict[str, int]) -> None:
    """测一个源的全部信息流 + 一条详情；统计写进 stats。"""
    detail_item: FeedItem | None = None
    for name in source.feeds:
        state, loader, line = await _try_open_feed(source, name)
        if loader is None:
            _line(state, line)
            stats[state.lower()] += 1
            continue
        try:
            _line("OK", line, t("probe.n_items", n=len(loader.items)))
            stats["ok"] += 1
            if detail_item is None:  # 挑一条可进详情屏的条目测 open_detail
                detail_item = next(
                    (it for it in loader.items if it.kind in source.detail_kinds), None
                )
        finally:
            await loader.close()

    if source.supports_detail and detail_item is not None:
        await _probe_detail(source, detail_item, stats)


async def _probe_detail(source: Source, item: FeedItem, stats: dict[str, int]) -> None:
    label = t("probe.detail_label", label=source.label_text)
    try:
        loader = await source.open_detail(item)
    except Exception as exc:
        state, msg = _classify(exc)
        _line(state, label, msg)
        stats[state.lower()] += 1
        return
    try:
        doc = loader.doc
        if not (doc.title or doc.description_md or doc.answers):
            _line("FAIL", label, t("probe.detail_empty"))
            stats["fail"] += 1
        else:
            title = doc.title[:24] + ("…" if len(doc.title) > 24 else "")
            _line(
                "OK", label,
                t("probe.detail_ok", title=title, n=len(doc.answers), unit=source.detail_unit),
            )
            stats["ok"] += 1
    finally:
        await loader.close()


async def _run(only: Sequence[str]) -> int:
    known = {s.source_id for s in all_sources()}
    if unknown := [i for i in only if i not in known]:
        joiner = t("common.enum_comma")
        print(
            t("probe.unknown_ids", unknown=joiner.join(unknown), known=joiner.join(sorted(known)))
        )
        return 2
    sample_dir = os.environ.setdefault(
        "FEEDSCOPE_SAMPLE_DIR", str(user_cache_path("feedscope") / "samples")
    )
    print(t("probe.title", dir=sample_dir))
    print()
    stats = {"ok": 0, "fail": 0, "skip": 0}
    try:
        for source in (s for s in all_sources() if not only or s.source_id in only):
            await _probe_source(source, stats)
    finally:
        await http.close()
        await close_session()
    print()
    print(t("probe.summary", ok=stats['ok'], fail=stats['fail'], skip=stats['skip']))
    if stats["fail"]:
        print(t("probe.fail_hint", dir=sample_dir))
    return 1 if stats["fail"] else 0


def run_probes(only: Sequence[str] = ()) -> int:
    """同步入口（cli 用）：巡检全部源或指定源，返回进程退出码。"""
    return asyncio.run(_run(only))
