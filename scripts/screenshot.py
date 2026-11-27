"""生成 README 演示截图：离线演示源驱动真实 FeedScopeApp，导出 docs/*.png。

用法：uv run python scripts/screenshot.py   # 产物写 docs/*.png，重跑即刷新

数据是编的（App 不碰网络；栅格化另用本机 Chrome），但走的是真实 UI 全链路。
管线与本目录另外两个模块：demo_sources（演示数据与环境隔离）、rasterize
（SVG 修正格宽 + 栅格化）；本文件只有「按什么键序走一遍界面」（walk_screens，
demo_gif.py 复用同一条键序）和落盘。

scripts/ 不是包：同目录按模块名互相 import。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from demo_sources import DOCS, demo_environment
from rasterize import fix_cjk_metrics, rasterize

from feedscope.ui import app as app_mod

Emit = Callable[[app_mod.FeedScopeApp, str], None]


async def walk_screens(emit: Emit, *, read_mix: bool = True, prefix: str = "screenshot-") -> None:
    """按固定键序走一遍界面，每到一个画面调 emit(app, 名字)。

    知乎 → X → 推文详情 → Reddit → 帖子详情 → HN → story 详情 → 回复树 → V2EX →
    主题详情。read_mix：每张列表图前混合已读态（截图要展示圆点三态；GIF 不要——
    它的叙事是「首跑全 ● → Enter 逐条清点」）。前缀只为区分产物，不影响走位。
    """
    app = app_mod.FeedScopeApp()
    async with app.run_test(size=(120, 38)) as pilot:
        for _ in range(6):
            await pilot.pause()

        async def show(name: str, *, mix: bool) -> None:
            if mix:
                await _demo_read_mix(app, pilot)
            emit(app, prefix + name)

        await show("zhihu", mix=read_mix)
        await pilot.press("tab")  # Tab 切到 X
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await show("x", mix=read_mix)
        await pilot.press("enter")  # 进首条推文详情（外文 + 译文 + 引用块）
        for _ in range(8):
            await pilot.pause()
        await show("detail", mix=False)
        await pilot.press("escape")  # 回 X 列表
        await pilot.press("tab")  # Tab 切到 Reddit
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await show("reddit", mix=read_mix)
        await pilot.press("enter")  # 进帖子详情（markdown 自述 + 带赞数的评论楼层）
        for _ in range(8):
            await pilot.pause()
        await show("reddit-detail", mix=False)
        await pilot.press("escape")  # 回 Reddit 列表
        await pilot.press("tab")  # Tab 切到 HN
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await show("hn", mix=read_mix)
        await pilot.press("enter")  # 进 story 详情（原文一行 + 评论楼层）
        for _ in range(8):
            await pilot.pause()
        await show("hn-detail", mix=False)
        await pilot.press("enter")  # 进首条评论的回复树（@ 父作者拍平）
        for _ in range(8):
            await pilot.pause()
        await show("hn-thread", mix=False)
        await pilot.press("escape")  # 回 HN 详情
        await pilot.press("escape")  # 回 HN 列表
        await pilot.press("tab")  # Tab 切到 V2EX
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await show("v2ex", mix=read_mix)
        await pilot.press("enter")  # 进主题详情（markdown 正文 + 回复楼层）
        for _ in range(8):
            await pilot.pause()
        await show("v2ex-detail", mix=False)


async def _demo_read_mix(app: app_mod.FeedScopeApp, pilot) -> None:
    """列表截图的已读混合态：每隔三条标一条已读再重排。

    截图要同时展示圆点三态（● 新 / ○ 未读 / 空 已读）——全新状态全屏 ●
    看不出「已读会变空」，全已读又看不出特性；隔条混合最像真实使用痕迹。
    直标 store 会绕过主屏的角标刷新链：标完手动重建边栏，否则角标停在
    抓取时的全量数（9 而不是 6）。
    """
    main = app.main
    if main is None or not main.items:
        return
    for it in main.items[::3]:
        app.state.mark_read(it.source, it.id)
    await main.flow.rebuild_rows()
    await main._render_feed_sidebar()
    for _ in range(2):
        await pilot.pause()


def _emit(app: app_mod.FeedScopeApp, name: str) -> None:
    """落盘一张：SVG -> 修 CJK 格宽 -> 栅格化 PNG。"""
    svg, fixed = fix_cjk_metrics(app.export_screenshot())
    png = DOCS / f"{name}.png"
    rasterize(svg, png)
    print(f"{png}  {png.stat().st_size // 1024} KB（修正 {fixed} 个 run 的格宽）")


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    with demo_environment():
        asyncio.run(walk_screens(_emit))


if __name__ == "__main__":
    main()
