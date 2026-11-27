"""把 Textual 导出的 SVG 变成 README 用的 @2x PNG（screenshot.py 与 demo_gif.py 共用）。

管线：SVG -> fix_cjk_metrics 修正中文格宽 -> 无头 Chrome 栅格化。

- 修正格宽：Rich 的 SVG 导出按字符数而非终端格数算 textLength，双格宽的 CJK
  只分到一半 advance（字形 20px 挤在 12.2px 格里互相叠字）；x 坐标本就按格数
  算对，只换 textLength
- 栅格化用本机 Chrome（像素级固定，彻底摆脱各浏览器字体回退差异；Chrome 本
  就是本项目的运行前提）。scripts/ 不是包：同目录按模块名互相 import，直接运行
  与 tests/scripts 的导入冒烟（把本目录加进 sys.path）都走这条路
"""

from __future__ import annotations

import html as _html
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from feedscope.core.browser import find_chrome
from feedscope.core.text import display_width

# ---- SVG 后处理：修正 CJK 格宽（Rich 导出按字符数计 textLength 的缺陷）----

_TEXT_RE = re.compile(
    r'<text ([^>]*?)x="([\d.]+)" y="([\d.]+)" textLength="([\d.]+)"([^>]*)>(.*?)</text>',
    re.DOTALL,
)


def _plain(content: str) -> str:
    """run 内容 -> 纯文本（去实体/标签），供 display_width 计量。"""
    return re.sub(r"<[^>]+>", "", _html.unescape(content))


def _cell_width(svg: str) -> float:
    """单格宽 = ASCII run 的 tl/len 众数（Fira Code 0.61em 派生，如 12.2）。"""
    ratios = Counter()
    for _pre, _x, _y, tl, _post, content in _TEXT_RE.findall(svg):
        text = _plain(content)
        if len(text) >= 2 and text.isascii():
            ratios[round(float(tl) / len(text), 1)] += 1
    return ratios.most_common(1)[0][0]


def fix_cjk_metrics(svg: str) -> tuple[str, int]:
    """按终端格数重算每个 run 的 textLength；x 坐标保持原样。

    实测核实（用右对齐列反推）：SVG 里 run 的 x 本就按格数定位（cell-based，
    正确），错的只有 textLength（按字符数，CJK 少算一半）——所以只替换
    textLength、绝不动 x，动了反而把折行/右对齐推错位。返回 (svg, 修正数)。
    """
    cell = _cell_width(svg)
    fixed = 0

    def _patch(m: re.Match[str]) -> str:
        nonlocal fixed
        pre, x, y, tl, post, content = m.groups()
        new_tl = display_width(_plain(content)) * cell
        if abs(new_tl - float(tl)) > 0.05:
            fixed += 1
        return f'<text {pre}x="{x}" y="{y}" textLength="{new_tl:.1f}"{post}>' + content + "</text>"

    return _TEXT_RE.sub(_patch, svg), fixed


# ---- 无头 Chrome 栅格化（与项目运行前提同源：channel="chrome" 的本机 Chrome）----


def _chrome_bin() -> str:
    """Chrome 可执行文件：与 doctor 共用同一发现逻辑（core.browser.find_chrome）。"""
    chrome = find_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome（截图栅格化依赖它，装法见 README）")
    return chrome


def rasterize(svg: str, png: Path) -> None:
    """SVG -> @2x PNG：窗口尺寸取 SVG 自带 width/height，透明底交给 SVG 自绘。

    必须给 Chrome 一个显式的 --user-data-dir：新版 Chrome 的 headless 不再
    自动创建临时 profile，缺了它直接 "Failed to create a unique user data
    directory for headless" 退出（截图脚本在 Chrome 156 上实测）。
    """
    w = int(float(re.search(r'width="([\d.]+)"', svg).group(1)))
    h = int(float(re.search(r'height="([\d.]+)"', svg).group(1))) + 1
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
        f.write(svg)
        svg_path = f.name
    try:
        with tempfile.TemporaryDirectory(prefix="feedscope-shot-") as profile:
            subprocess.run(
                [
                    _chrome_bin(),
                    "--headless",
                    f"--user-data-dir={profile}",
                    "--no-sandbox",  # 本地 SVG 是自产可信内容；容器/CI 里 Chrome 沙箱常起不来
                    "--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    "--default-background-color=00000000",
                    f"--window-size={w},{h}",
                    f"--screenshot={png}",
                    f"file://{svg_path}",
                    "--virtual-time-budget=8000",  # 等 CDN 字体（Fira Code）加载
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
    finally:
        Path(svg_path).unlink(missing_ok=True)
