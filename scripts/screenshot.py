"""生成 README 演示截图：离线 stub 源驱动真实 FeedScopeApp，导出 PNG。

用法：uv run python scripts/screenshot.py   # 产物写 docs/*.png，重跑即刷新

数据是编的（不碰网络与浏览器），但走的是真实 UI 全链路（注册表 -> FeedScopeApp
-> 列表/详情屏），改 UI 后重跑即可同步截图。FEEDSCOPE_CONFIG 指向不存在的路径，
避免本机隐藏流配置让侧栏条目数漂移；PREWARM 关闭避免拉起真 Chrome。

管线：export_screenshot 出 SVG -> _fix_cjk_metrics 修正中文格宽（Rich 的
SVG 导出按字符数而非终端格数算 textLength，双格宽的 CJK 只分到一半 advance，
字形 20px 挤在 12.2px 格里互相叠字；x 坐标本就按格数算对，只换 textLength）
-> 无头 Chrome 栅格化成 @2x PNG（像素级固定，彻底摆脱各浏览器字体回退差异；
Chrome 本就是本项目的运行前提）。
"""

from __future__ import annotations

import asyncio
import html as _html
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import ClassVar

from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import (
    CommentLoader,
    DetailLoader,
    FeedLoader,
    Source,
    StaticFeedLoader,
)
from feedscope.core.text import display_width
from feedscope.ui import app as app_mod

DOCS = Path(__file__).resolve().parent.parent / "docs"

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


def _fix_cjk_metrics(svg: str) -> tuple[str, int]:
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

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
)


def _chrome_bin() -> str:
    for cand in _CHROME_CANDIDATES:
        if "/" in cand and Path(cand).exists():
            return cand
        if (p := shutil.which(cand)) :
            return p
    raise SystemExit("找不到 Chrome（截图栅格化依赖它，装法见 README）")


def _rasterize(svg: str, png: Path) -> None:
    """SVG -> @2x PNG：窗口尺寸取 SVG 自带 width/height，透明底交给 SVG 自绘。"""
    w = int(float(re.search(r'width="([\d.]+)"', svg).group(1)))
    h = int(float(re.search(r'height="([\d.]+)"', svg).group(1))) + 1
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
        f.write(svg)
        svg_path = f.name
    try:
        subprocess.run(
            [
                _chrome_bin(),
                "--headless",
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

# ---- 知乎热榜（编造的标题与热度，形态对齐 _hot_api_items 产物）----

_HOT = [
    ("为什么现在的年轻人开始流行「上班恶心穿搭」？", "4973 万热度"),
    ("程序员到了 35 岁真的会失业吗？", "3121 万热度"),
    ("如何评价 2026 年诺贝尔物理学奖授予人工神经网络领域？", "2886 万热度"),
    ("坚持每天跑步 5 公里，一年后身体会发生什么变化？", "2450 万热度"),
    ("《三体》影视化为什么这么难？", "2104 万热度"),
    ("一个人开始变强的迹象是什么？", "1877 万热度"),
    ("为什么高铁座椅的靠背不能 180 度放平？", "1523 万热度"),
    ("在家办公三年，你的真实体验是什么？", "1290 万热度"),
    ("为什么越来越多人开始用终端工具替代图形界面？", "1102 万热度"),
]


def _zhihu_item(title: str, meta: str) -> FeedItem:
    return FeedItem(
        source="zhihu",
        id=f"question/{abs(hash(title)) % 10**9}",
        title=title,
        url="https://www.zhihu.com/question/demo",
        kind="question",
        meta=meta,
    )


class DemoZhihuDetail(DetailLoader):
    """知乎问题详情 stub：标题/提问人/描述 + 三条回答楼层。"""

    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="为什么越来越多人开始用终端工具替代图形界面？",
            meta="数据搬运工 提问 · 42 个回答 · 128 万浏览",
            time="2026-10-28 21:07",
            description_md="最近发现身边的朋友，编辑器、邮件、日历甚至阅读资讯都搬进了终端。是怀旧滤镜，还是终端真有不可替代的优势？",
            answers=[
                Answer(
                    author="老张写代码",
                    votes="赞 1482",
                    content_md="三个字：**可组合**。\n\n终端工具的输入输出都是纯文本，任何工具之间都能用管道串起来——这是图形界面永远做不到的事。我在终端里看知乎、刷 X、查文档，`Alt+Tab` 都不用按。\n\n再者是**可脚本化**：所有操作都能写成一行情命令自动执行，图形界面的按钮永远学不会。",
                    time="2026-10-29 09:12",
                    comment_count="158 条评论",
                ),
                Answer(
                    author="终端原教旨主义者",
                    votes="赞 936",
                    content_md="因为终端里一切皆文本：可搜索、可管道、可脚本化，键盘一双手就能完成全部操作。\n\n[图片](https://pic.demo/zhihu/terminal-vs-gui.jpg)",
                    time="2026-10-29 11:40",
                    comment_count="87 条评论",
                ),
                Answer(
                    author="匿名用户",
                    votes="赞 214",
                    content_md="远程服务器上没有图形界面，SSH 进去只有终端，用久了就回不去了。",
                    time="2026-10-30 02:18",
                    comment_count="12 条评论",
                ),
            ],
        )

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Answer]:
        return []

    async def close(self) -> None:
        pass


class DemoZhihu(Source):
    source_id = "zhihu"
    label = "知乎"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["hot", "recommend", "follow"]
    feed_labels: ClassVar[dict[str, str]] = {"hot": "热榜", "recommend": "推荐", "follow": "关注"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"question"})

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_zhihu_item(t, m) for t, m in _HOT])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoZhihuDetail()


# ---- X 时间线（编造的推文与署名，形态对齐 _tweet_from_result 产物）----

_TWEETS = [
    (
        "_terminal.dev 2.0 发布：LSP 补全、内联图像预览、还有 GPU 加速滚动，终端党的春天 [图片]",
        "小林 @terminalfan",
    ),
    (
        "今天把博客从 Next.js 迁到纯静态，构建时间 90s -> 2s，部署脚本只剩三行",
        "静态站点搬运工 @staticweb",
    ),
    (
        "长上下文模型用了一年，最大的变化是终于不用把文档切块再拼结论了，直接整本说明书丢进去",
        "AI 观察员 @ai_notes",
    ),
    (
        "周末用 Rust 重写了家里的 NAS 工具链，内存占用从 400MB 降到 38MB [图片 2]",
        "造轮子工程师 @wheel_builder",
    ),
    (
        "冷门技巧：shell 里 Ctrl+Z 挂起 vim 出去看眼文件，fg 回来光标还在原地，比 :q 重开优雅多了",
        "命令行老手 @cli_veteran",
    ),
    (
        "气候变化让今年的樱桃季提前了两周，果园主说这是十年里最旱的一个春天 [图片]",
        "田野记录者 @field_notes",
    ),
    (
        "远程办公五年总结：最重要的一条是把通勤时间还给睡眠，而不是还给第二份工作",
        "数字游民日记 @remote_log",
    ),
]


def _x_item(title: str, meta: str) -> FeedItem:
    return FeedItem(
        source="x",
        id=f"status/{abs(hash(title)) % 10**9}",
        title=title,
        url="https://x.com/demo/status/1",
        kind="tweet",
        meta=meta,
    )


class DemoXDetail(DetailLoader):
    """X 推文详情 stub：外文正文 + Grok 译文块 + 被引推文 + 外文回复（带译文）。"""

    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="Textual 3.0 is out: CSS grid, command palette, and a",
            meta="Sarah @sarah_builds · 214 回复 · 1.2 万 赞",
            time="2026-10-31 18:22",
            description_md=(
                "Finally shipping what we've been working on for a year.\n\n"
                "Textual 3.0 brings CSS grid layout, a command palette, and a rendering "
                "pipeline that's twice as fast on long documents. Docs: docs.textual.io\n\n"
                "[图片](https://pbs.twimg.com/media/Example3.0.png)\n\n"
                "> **译文**（英语）：终于发布了我们打磨一年的东西。Textual 3.0 带来 CSS grid "
                "布局、命令面板，以及长文档下快一倍的渲染管线。文档：docs.textual.io\n\n"
                "> **被引推文** · 小林 @terminalfan：终端 UI 框架的天花板又被抬高了，2.0 的 "
                "DataTable 性能就已经很夸张 [图片 2]"
            ),
            answers=[
                Answer(
                    author="Miguel @mig_dev",
                    votes="赞 312",
                    content_md=(
                        "Grid layout is a game changer. Migrated my dashboard in an afternoon "
                        "and deleted 200 lines of manual sizing code.\n\n"
                        "> **译文**（英语）：Grid 布局是颠覆性的改动。一下午就把仪表盘迁完了，"
                        "还删掉了 200 行手动算尺寸的代码。"
                    ),
                    time="2026-10-31 18:40",
                ),
                Answer(
                    author="阿哲 @azhe_codes",
                    votes="赞 148",
                    content_md=(
                        "这波对中文用户也友好，标点避头尾终于按显示宽度折行了 ▸ 引用 "
                        "Miguel @mig_dev：Grid layout is a game changer…"
                    ),
                    time="2026-10-31 19:02",
                ),
            ],
        )

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Answer]:
        return []

    async def close(self) -> None:
        pass


class DemoX(Source):
    source_id = "x"
    label = "X"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["foryou", "following"]
    feed_labels: ClassVar[dict[str, str]] = {"foryou": "推荐", "following": "正在关注"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"tweet"})
    detail_unit_label = "回复"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_x_item(t, m) for t, m in _TWEETS])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoXDetail()


# ---- HN（编造的条目与讨论，形态对齐 sources/hn/parse 产物）----

_HN = [
    ("SQLite as an application file format", "942 点 · 213 评 · simonw", "story"),
    ("Postgres 19 is late because so many more bugs are being found now", "769 点 · 456 评 · soheilpro", "story"),
    ("Show HN: HackerTube – YouTube, but for tech talks mentioned on HN", "1771 点 · 289 评 · roansh", "show"),
    ("Ask HN: What does your terminal setup look like in 2026?", "388 点 · 512 评 · catlover99", "ask"),
    ("The case for slow software", "1204 点 · 331 评 · brocardo", "story"),
    ("Rust in the Android platform", "656 点 · 98 评 · jfriby", "story"),
    ("Ask HN: Who is hiring? (November 2026)", "212 点 · 641 评 · whoishiring", "ask"),
    ("A deep dive into epoll", "534 点 · 87 评 · kernelsand", "story"),
]


def _hn_item(title: str, meta: str, kind: str) -> FeedItem:
    return FeedItem(
        source="hn",
        id=str(abs(hash(title)) % 10**9),
        title=title,
        url="https://example.com/article",
        kind=kind,
        meta=meta,
    )


class DemoHnComments(CommentLoader):
    """HN 楼层回复树 stub：顶层回复 + 拍平的深层回复（@ 直接父）。"""

    def __init__(self, comments: list[Comment]) -> None:
        self.comments = comments
        self._done = True

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[Comment]:
        return []

    async def close(self) -> None:
        pass


class DemoHnDetail(DetailLoader):
    """HN story 详情 stub：原文一行 + 顶层评论当楼层 + 各自回复树。"""

    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="SQLite as an application file format",
            meta="942 点 · 213 评 · simonw",
            time="2026-11-02 03:12",
            description_md="[原文](https://sqlite.org/appfileformat.html)",
            answers=[
                Answer(
                    author="catenary",
                    votes="",
                    content_md=(
                        "Every few years I rediscover this document and every time it "
                        "changes how I think about persistence. The section on "
                        "transactional semantics for derived data alone is worth it."
                    ),
                    time="2026-11-02 04:03",
                    url="https://news.ycombinator.com/item?id=42000001",
                    comment_count="4 条回复",
                ),
                Answer(
                    author="dang",
                    votes="",
                    content_md=(
                        "We used an appfile for a CAD tool: undo history, thumbnails and "
                        "the geometry in one file users can email each other. Zero exports, "
                        "zero format drift between versions."
                    ),
                    time="2026-11-02 04:31",
                    url="https://news.ycombinator.com/item?id=42000002",
                    comment_count="2 条回复",
                ),
                Answer(
                    author="rwmj",
                    votes="",
                    content_md="The format is fine; the real magic is that readers are forward-compatible by construction.",
                    time="2026-11-02 05:07",
                    url="https://news.ycombinator.com/item?id=42000003",
                    comment_count="",
                ),
            ],
        )

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Answer]:
        return []

    async def open_comments(self, index: int) -> DemoHnComments:
        return DemoHnComments(
            [
                Comment(
                    author="drewc",
                    votes="",
                    content_md="Derived data was the unlock for us — rebuild the index inside the file on open, no sidecar to lose.",
                    time="2026-11-02 04:20",
                ),
                Comment(
                    author="peterdshutt",
                    votes="",
                    content_md="Same trick works for full-text search: store the FTS index as a shadow table and it stays transactional.",
                    time="2026-11-02 04:44",
                    replies=[
                        Comment(
                            author="catenary",
                            votes="",
                            content_md="@peterdshutt：and because it's in the same transaction, a crashed write can never leave the index stale.",
                            time="2026-11-02 04:52",
                        ),
                    ],
                ),
            ]
        )

    async def close(self) -> None:
        pass


class DemoHn(Source):
    source_id = "hn"
    label = "HN"
    supports_detail = True
    supports_comments = True
    feeds: ClassVar[list[str]] = ["front", "new", "ask", "show"]
    feed_labels: ClassVar[dict[str, str]] = {"front": "首页", "new": "最新", "ask": "Ask", "show": "Show"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"story", "ask", "show"})
    detail_unit_label = "评论"
    comment_unit_label = "回复"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_hn_item(t, m, k) for t, m, k in _HN])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoHnDetail()


async def _shots() -> None:
    app = app_mod.FeedScopeApp()
    async with app.run_test(size=(120, 38)) as pilot:
        for _ in range(6):
            await pilot.pause()
        DOCS.mkdir(exist_ok=True)
        _emit(app, "screenshot-zhihu")
        await pilot.press("tab")  # Tab 切到 X
        for _ in range(6):
            await pilot.pause()
        _emit(app, "screenshot-x")
        await pilot.press("enter")  # 进首条推文详情（外文 + 译文 + 引用块）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-detail")
        await pilot.press("escape")  # 回 X 列表
        await pilot.press("tab")  # Tab 切到 HN
        for _ in range(6):
            await pilot.pause()
        _emit(app, "screenshot-hn")
        await pilot.press("enter")  # 进 story 详情（原文一行 + 评论楼层）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-hn-detail")
        await pilot.press("enter")  # 进首条评论的回复树（@ 父作者拍平）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-hn-thread")


def _emit(app: app_mod.FeedScopeApp, name: str) -> None:
    """导出一张：SVG -> 修 CJK 格宽 -> 栅格化 PNG。"""
    svg, fixed = _fix_cjk_metrics(app.export_screenshot())
    png = DOCS / f"{name}.png"
    _rasterize(svg, png)
    print(f"{png}  {png.stat().st_size // 1024} KB（修正 {fixed} 个 run 的格宽）")


def main() -> None:
    # 隔离本机用户配置（隐藏流会让侧栏条目数漂移）；预写 enabled 跳过首启引导
    # （引导是交互弹层，自动化流程过不去）；PREWARM 关掉不拉真 Chrome
    cfg = DOCS.parent / ".screenshot-sources.toml"
    cfg.write_text('enabled = ["zhihu", "x", "hn"]\n', encoding="utf-8")
    os.environ["FEEDSCOPE_CONFIG"] = str(cfg)
    app_mod.PREWARM = False
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[DemoZhihu.source_id] = DemoZhihu
    _REGISTRY[DemoX.source_id] = DemoX
    _REGISTRY[DemoHn.source_id] = DemoHn
    try:
        asyncio.run(_shots())
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


if __name__ == "__main__":
    main()
