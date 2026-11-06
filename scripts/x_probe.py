"""X 通道诊断：打印运行时收割到的请求形状，并实测一次时间线与详情。

请求形状收割已内建进 fetch.py 的运行时（重开页签后从 JS chunk grep
queryId → 种子兜底；TweetDetail 的 features 走网络层深度截获，失效自动
重割自愈），本脚本只剩诊断用途——自愈失败想看现场、或想确认当前收割
状态时跑：
    uv run python scripts/x_probe.py
输出全部脱敏（无 cookie、无完整响应体）。
"""

import asyncio
import json
import sys

from feedscope.core.browser import close_session
from feedscope.core.source import LoginRequired
from feedscope.sources.x import require_login
from feedscope.sources.x.fetch import _DETAIL_OP, fetch_conversation, fetch_entries, harvest


async def _probe() -> None:
    try:
        await require_login("探针脚本")
    except LoginRequired:
        print("NO_AUTH：先运行 feedscope login x")
        sys.exit(1)
    print("AUTH_OK")

    await harvest()  # 现场重割（重开页签加载当前部署）
    from feedscope.sources.x.fetch import _harvest, _seed_for

    for op_name in ("HomeTimeline", "HomeLatestTimeline", _DETAIL_OP):
        op = _harvest.get(op_name) or _seed_for(op_name)
        src = "harvest" if op_name in _harvest else "seed"
        if op is None:
            print(f"{op_name}: MISSING")
            continue
        shape = {
            "qid": op.qid,
            "src": src,
            "variables": sorted(op.variables),
            "features": len(op.features or {}),
        }
        print(f"{op_name}: {json.dumps(shape, ensure_ascii=False)}")

    entries, _cursor = await fetch_entries("foryou", None)
    print(f"TIMELINE_OK entries={len(entries)}")

    entries, _cursor = await fetch_conversation("20", None)
    print(f"DETAIL_OK entries={len(entries)}")


async def main() -> None:
    try:
        await _probe()
    finally:
        # 任何退出路径（含异常/超时）都要关浏览器：残留的持久 Chrome 会锁住 profile
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
