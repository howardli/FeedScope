"""生成 README 顶部演示 GIF：复用 screenshot.py 的离线 stub 源驱动真实 UI。

用法：uv run --with pillow python scripts/demo_gif.py   # 产物 docs/demo.gif，重跑即刷新

与 screenshot.py 同一条管线（stub 源 -> FeedScopeApp -> export_screenshot ->
修 CJK 格宽 -> Chrome 栅格化 @2x PNG），区别只在把逐屏 _emit 的落盘改成
收集帧、最后用 Pillow 合成 GIF；键序也复刻 _shots（知乎 → X → 详情 →
Reddit → 详情 → HN → 详情 → 回复树 → V2EX → 详情）。Pillow 只在本脚本
运行期需要，走 `uv run --with pillow` 临时注入，不进依赖清单。

模块级保持零副作用（tests/test_scripts_smoke 会逐个 import scripts/*.py）：
screenshot 的 import 挪进 main，按路径 import（scripts/ 不是包）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
FRAME_MS = 1600  # 每帧停留：读清一屏标题列的时长，再快就得暂停观看了


async def _drive(app_mod: object, emit: Callable[[object, str], None]) -> None:
    """与 screenshot._shots 同一条键序走一遍 UI，每步 emit 一帧。"""
    app = app_mod.FeedScopeApp()  # type: ignore[attr-defined]
    async with app.run_test(size=(120, 38)) as pilot:
        for _ in range(6):
            await pilot.pause()
        emit(app, "zhihu")
        await pilot.press("tab")  # Tab 切到 X
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        emit(app, "x")
        await pilot.press("enter")  # 进首条推文详情（外文 + 译文 + 引用块）
        for _ in range(8):
            await pilot.pause()
        emit(app, "x-detail")
        await pilot.press("escape")  # 回 X 列表
        await pilot.press("tab")  # Tab 切到 Reddit
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        emit(app, "reddit")
        await pilot.press("enter")  # 进帖子详情（markdown 自述 + 评论楼层）
        for _ in range(8):
            await pilot.pause()
        emit(app, "reddit-detail")
        await pilot.press("escape")  # 回 Reddit 列表
        await pilot.press("tab")  # Tab 切到 HN
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        emit(app, "hn")
        await pilot.press("enter")  # 进 story 详情（原文一行 + 评论楼层）
        for _ in range(8):
            await pilot.pause()
        emit(app, "hn-detail")
        await pilot.press("enter")  # 进首条评论的回复树（@ 父作者拍平）
        for _ in range(8):
            await pilot.pause()
        emit(app, "hn-thread")
        await pilot.press("escape")  # 回 HN 详情
        await pilot.press("escape")  # 回 HN 列表
        await pilot.press("tab")  # Tab 切到 V2EX
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        emit(app, "v2ex")
        await pilot.press("enter")  # 进主题详情（markdown 正文 + 回复楼层）
        for _ in range(8):
            await pilot.pause()
        emit(app, "v2ex-detail")


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ 不是包，按路径借道
    import screenshot as shots  # noqa: PLC0415  复用 stub 源与栅格化管线，不复制粘贴

    from feedscope.core.registry import install_sources, source_classes
    from feedscope.ui import app as app_mod

    # 隔离本机用户配置（与 screenshot.main 同一套路：避开首启引导 / 隐藏流漂移 / 真 Chrome 预热）
    cfg = DOCS.parent / ".screenshot-sources.toml"
    cfg.write_text('enabled = ["zhihu", "x", "reddit", "hn", "v2ex"]\n', encoding="utf-8")
    os.environ["FEEDSCOPE_CONFIG"] = str(cfg)
    os.environ["FEEDSCOPE_NO_PREWARM"] = "1"

    frames: list[Path] = []

    def emit(app: object, name: str) -> None:
        svg, _fixed = shots._fix_cjk_metrics(app.export_screenshot())  # type: ignore[attr-defined]
        png = Path(tmp) / f"{name}.png"
        shots._rasterize(svg, png)
        frames.append(png)
        print(f"frame {len(frames):2d}  {name}")

    saved = source_classes()
    install_sources([shots.DemoZhihu, shots.DemoX, shots.DemoReddit, shots.DemoHn, shots.DemoV2ex])
    try:
        with tempfile.TemporaryDirectory(prefix="feedscope-gif-") as tmp:
            asyncio.run(_drive(app_mod, emit))
            _assemble(frames, DOCS / "demo.gif")
    finally:
        install_sources(saved)


def _assemble(frames: list[Path], out: Path) -> None:
    """PNG 帧序列 -> 循环 GIF：RGBA 合到黑底转 RGB（GIF 无 alpha），统一量化 256 色。"""
    from PIL import Image

    imgs: list[Image.Image] = []
    for p in frames:
        rgba = Image.open(p).convert("RGBA")
        base = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        base.alpha_composite(rgba)
        imgs.append(base.convert("RGB"))
    imgs[0].save(
        out,
        save_all=True,
        append_images=imgs[1:],
        duration=FRAME_MS,
        loop=0,  # 无限循环
        optimize=True,
    )
    print(f"{out}  {out.stat().st_size // 1024} KB（{len(imgs)} 帧 × {FRAME_MS}ms）")


if __name__ == "__main__":
    main()
