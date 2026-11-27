"""生成 README 顶部演示 GIF：复用截图管线的离线演示源驱动真实 UI。

用法：uv run --with pillow python scripts/demo_gif.py   # 产物 docs/demo.gif，重跑即刷新

与 screenshot.py 同一条管线（演示源 -> FeedScopeApp -> export_screenshot -> 修 CJK
格宽 -> Chrome 栅格化 @2x PNG）和同一条键序（screenshot.walk_screens），区别只在
把逐屏落盘改成收集帧、最后用 Pillow 合成 GIF；不做已读混合（叙事是「首跑全 ● →
Enter 逐条清点」）。Pillow 只在本脚本运行期需要，走 `uv run --with pillow` 临时
注入，不进依赖清单。模块级零副作用（tests/scripts 会逐个 import scripts/*.py）。

scripts/ 不是包：同目录按模块名互相 import。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from demo_sources import DOCS, demo_environment
from rasterize import fix_cjk_metrics, rasterize
from screenshot import walk_screens

FRAME_MS = 1600  # 每帧停留：读清一屏标题列的时长，再快就得暂停观看了


def main() -> None:
    frames: list[Path] = []

    def emit(app, name: str) -> None:
        svg, _fixed = fix_cjk_metrics(app.export_screenshot())
        png = Path(tmp) / f"{name}.png"
        rasterize(svg, png)
        frames.append(png)
        print(f"frame {len(frames):2d}  {name}")

    with demo_environment(), tempfile.TemporaryDirectory(prefix="feedscope-gif-") as tmp:
        asyncio.run(walk_screens(emit, read_mix=False, prefix=""))
        _assemble(frames, DOCS / "demo.gif")


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
