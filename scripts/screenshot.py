"""生成 README 演示截图：离线 stub 源驱动真实 FeedScopeApp，导出 PNG。

用法：uv run python scripts/screenshot.py   # 产物写 docs/*.png，重跑即刷新

数据是编的（App 不碰网络；栅格化另用本机 Chrome），但走的是真实 UI 全链路
（注册表 -> FeedScopeApp -> 列表/详情屏），改 UI 后重跑即可同步截图。
FEEDSCOPE_CONFIG 指向仓库根下临时生成的 .screenshot-sources.toml（已 gitignore），
固定 enabled 避开本机隐藏流配置让侧栏条目数漂移；FEEDSCOPE_NO_PREWARM 关闭
App 启动预热的真 Chrome（栅格化仍要 Chrome）。

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
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import ClassVar

from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import install_sources, source_classes
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


def _chrome_bin() -> str:
    """Chrome 可执行文件：与 doctor 共用同一发现逻辑（core.const.find_chrome）。"""
    from feedscope.core.const import find_chrome

    chrome = find_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome（截图栅格化依赖它，装法见 README）")
    return chrome


def _rasterize(svg: str, png: Path) -> None:
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


# ---- Reddit（编造的帖子与评论树，形态对齐 sources/reddit/parse 产物）----

_REDDIT = [
    ("Rust 1.85 released: let chains and 2024 edition goodies", "1.2 万 点 · 892 评 · r/rust"),
    ("Ask r/vim: how do you read feeds without leaving the editor?", "1.5 万 点 · 762 评 · r/vim"),
    ("SQLite 3.50 shipped — small but dense release notes as always", "8341 点 · 412 评 · r/sqlite"),
    ("I ported my blog off Next.js to 40 lines of shell script", "2947 点 · 187 评 · r/webdev"),
    ("Postgres 19 delay explained by core team member", "8943 点 · 764 评 · r/PostgreSQL"),
    ("Show r/rust: a TUI that aggregates every feed I read in one terminal", "1204 点 · 96 评 · r/rust"),
    ("The best keyboard for programming is the one already on your laptop", "7643 点 · 892 评 · r/keyboards"),
    ("Why is every CLI tool rewriting itself in Rust — and should I care?", "6720 点 · 531 评 · r/unix"),
]


def _reddit_item(title: str, meta: str, kind: str = "link") -> FeedItem:
    return FeedItem(
        source="reddit",
        id=str(abs(hash(title)) % 10**7),
        title=title,
        url="https://www.reddit.com/r/rust/comments/demo/x/",
        kind=kind,
        meta=meta,
    )


class _DemoCommentsLoader(CommentLoader):
    """楼层回复树 stub：内存切片（形态对齐 core 的 SliceCommentsLoader）。"""

    def __init__(self, comments: list[Comment]) -> None:
        self.comments = comments
        self._rest: list[Comment] = []

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Comment]:
        return []

    async def close(self) -> None:
        pass


class DemoRedditDetail(DetailLoader):
    """Reddit 帖子详情 stub：markdown 自述正文 + 评论楼层（赞数/回复数/置顶）。"""

    def __init__(self) -> None:
        self._floors = [
            (
                Answer(
                    author="rust-mod-team",
                    votes="赞 156",
                    content_md="[置顶] 1.85 discussion megathread — keep release questions here.",
                    time="2026-11-03 09:02",
                    url="https://www.reddit.com/r/rust/comments/demo/x/c_mod/",
                    comment_count="4 条回复",
                ),
                [
                    Comment(
                        author="orbiting_atom",
                        votes="赞 22",
                        content_md="Megathread appreciated, my upgrade went smooth on macOS.",
                        time="2026-11-03 09:11",
                    ),
                ],
            ),
            (
                Answer(
                    author="borrowchk_survivor",
                    votes="赞 1842",
                    content_md=(
                        "Let chains finally land and suddenly half my `let else` ladders read "
                        "like prose. The edition cleanup is the quiet star here."
                    ),
                    time="2026-11-03 09:20",
                    url="https://www.reddit.com/r/rust/comments/demo/x/c_let/",
                    comment_count="12 条回复",
                ),
                [
                    Comment(
                        author="async_wizard",
                        votes="赞 341",
                        content_md="Same. Combined with `if let` chains our parser shed 200 lines.",
                        time="2026-11-03 09:33",
                        replies=[
                            Comment(
                                author="borrowchk_survivor",
                                votes="赞 88",
                                content_md="@async_wizard：careful with chains in `#[async_recursion]`, hit a borrow checker wall there.",
                                time="2026-11-03 09:41",
                            ),
                        ],
                    ),
                    Comment(
                        author="edition_migrator",
                        votes="赞 129",
                        content_md="cargo fix --edition migrated a 40k-line workspace with 6 manual touches. Wild.",
                        time="2026-11-03 10:05",
                    ),
                ],
            ),
            (
                Answer(
                    author="c_curious",
                    votes="赞 47",
                    content_md="Serious question from a C dev: does let chains change codegen at all, or is it purely sugar?",
                    time="2026-11-03 10:12",
                    url="https://www.reddit.com/r/rust/comments/demo/x/c_q/",
                    comment_count="",
                ),
                [],
            ),
        ]
        self.doc = DetailDoc(
            title="Rust 1.85 released: let chains and 2024 edition goodies",
            meta="1.2 万 点 · 892 评 · r/rust · u/rust_release_bot",
            time="2026-11-03 08:55",
            description_md=(
                "Highlights from the announcement:\n\n"
                "- `let` chains stabilized — `if let Some(x) = a && let Some(y) = b` now just works\n"
                "- 2024 edition migration: `cargo fix --edition`\n"
                "- `#[expect]` attribute lands alongside `#[allow]`\n\n"
                "Discussion megathread below, blog post: [原文](https://blog.rust-lang.org/2026/11/03/Rust-1.85.html)"
            ),
            answers=[f for f, _ in self._floors],
        )

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Answer]:
        return []

    async def open_comments(self, index: int) -> CommentLoader:
        return _DemoCommentsLoader(self._floors[index][1])

    async def close(self) -> None:
        pass


class DemoReddit(Source):
    source_id = "reddit"
    label = "Reddit"
    supports_detail = True
    supports_comments = True
    subscribable = True
    feeds: ClassVar[list[str]] = ["home", "popular"]
    feed_labels: ClassVar[dict[str, str]] = {"home": "首页", "popular": "热门"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"link", "self"})
    detail_unit_label = "评论"
    comment_unit_label = "回复"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader(
            [_reddit_item(t, m, "self" if i == 0 else "link") for i, (t, m) in enumerate(_REDDIT)]
        )

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoRedditDetail()


class DemoHn(Source):
    source_id = "hn"
    label = "HN"
    supports_detail = True
    supports_comments = True
    feeds: ClassVar[list[str]] = ["front", "new", "ask", "show"]
    feed_labels: ClassVar[dict[str, str]] = {
        "front": "首页", "new": "最新", "ask": "提问", "show": "展示",
    }
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"story", "ask", "show"})
    detail_unit_label = "评论"
    comment_unit_label = "回复"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_hn_item(t, m, k) for t, m, k in _HN])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoHnDetail()


# ---- V2EX（编造的主题与回复，形态对齐 sources/v2ex/parse 产物）----

_V2EX = [
    ("有感而发：小团队真的别上微服务", "程序员 · 528 回复 · monolith4ever"),
    ("试了下让 AI 写了一周的 Rust，聊聊它比我强和不如我的地方", "OpenAI · 412 回复 · rustacean"),
    ("2026 年了，还是不理解为什么有人坚持用机械键盘写代码", "程序员 · 234 回复 · keyboardfan"),
    ("把家里 NAS 从群晖迁到自建方案，一年下来的真实体验", "NAS · 167 回复 · homelabguy"),
    ("凌晨三点的报警短信，把我从「再优化一下」的执念里炸醒了", "职场话题 · 96 回复 · oncallvictim"),
    ("大家现在用什么 RSS 阅读器？我从云端服务迁出来后终于清净了", "分享创造 · 89 回复 · rsslover"),
    ("周末给老人房装了个摄像头，用的全是自托管组件，零月费", "宽带与接入 · 54 回复 · selfhost"),
    ("macOS 上有什么终端工具能替代全部图形界面？我列了个清单", "macOS · 47 回复 · terminalist"),
]


def _v2ex_item(title: str, meta: str) -> FeedItem:
    return FeedItem(
        source="v2ex",
        id=str(abs(hash(title)) % 10**7),
        title=title,
        url="https://www.v2ex.com/t/demo",
        kind="topic",
        meta=meta,
    )


class DemoV2exDetail(DetailLoader):
    """V2EX 主题详情 stub：markdown 正文 + 平铺回复楼层（无评论层）。"""

    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="有感而发：小团队真的别上微服务",
            meta="程序员 · 528 回复 · monolith4ever",
            time="2026-11-02 21:14",
            description_md=(
                "背景：五人团队，去年被外包架构师带着拆了 14 个服务。今年他走了，"
                "剩下我们四个天天在排期表和链路追踪之间疲于奔命。\n\n"
                "月初我们把全部服务合回单体，两周上线，CPU 成本降了 60%。"
                "不是微服务不好，是**我们没有那个体量，却背了那个复杂度**。\n\n"
                "架构图对比：[图片](https://i.imgur.com/demo-before-after.png)"
            ),
            answers=[
                Answer(
                    author="sre_monk",
                    votes="",
                    content_md=(
                        "深有同感。微服务解决的是组织问题，不是技术问题——五个人的团队"
                        "没有「组织」，只有「一起写代码的朋友」。"
                    ),
                    time="2026-11-02 21:30",
                    url="https://www.v2ex.com/t/demo#reply1",
                ),
                Answer(
                    author="distributed_fan",
                    votes="",
                    content_md=(
                        "反对样本偏差：你们是「被拆错了」，不是「拆错了」。队列削峰、"
                        "独立扩缩容这些收益，单体上你只能眼睁睁看着整站陪葬。\n\n"
                        "@sre_monk 说的问题在于人，微服务只是把它放大了。"
                    ),
                    time="2026-11-02 21:47",
                    url="https://www.v2ex.com/t/demo#reply2",
                ),
                Answer(
                    author="monolith4ever",
                    votes="",
                    content_md="楼主补充：合回单体后测试也从 40 分钟降到 6 分钟，本地能跑全集成了。",
                    time="2026-11-02 22:05",
                    url="https://www.v2ex.com/t/demo#reply3",
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


class DemoV2ex(Source):
    source_id = "v2ex"
    label = "V2EX"
    supports_detail = True
    supports_comments = False  # 回复平铺无嵌套，详情到楼层为止（截图里 Footer 无 Enter 预告）
    feeds: ClassVar[list[str]] = ["hot", "new"]
    feed_labels: ClassVar[dict[str, str]] = {"hot": "热门", "new": "最新"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"topic"})
    detail_unit_label = "回复"

    async def open_feed(self, name: str) -> FeedLoader:
        return StaticFeedLoader([_v2ex_item(t, m) for t, m in _V2EX])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return DemoV2exDetail()


async def _shots() -> None:
    app = app_mod.FeedScopeApp()
    async with app.run_test(size=(120, 38)) as pilot:
        for _ in range(6):
            await pilot.pause()
        DOCS.mkdir(exist_ok=True)
        await _demo_read_mix(app, pilot)
        _emit(app, "screenshot-zhihu")
        await pilot.press("tab")  # Tab 切到 X
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await _demo_read_mix(app, pilot)
        _emit(app, "screenshot-x")
        await pilot.press("enter")  # 进首条推文详情（外文 + 译文 + 引用块）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-detail")
        await pilot.press("escape")  # 回 X 列表
        await pilot.press("tab")  # Tab 切到 Reddit
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await _demo_read_mix(app, pilot)
        _emit(app, "screenshot-reddit")
        await pilot.press("enter")  # 进帖子详情（markdown 自述 + 置顶/赞数评论楼层）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-reddit-detail")
        await pilot.press("escape")  # 回 Reddit 列表
        await pilot.press("tab")  # Tab 切到 HN
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await _demo_read_mix(app, pilot)
        _emit(app, "screenshot-hn")
        await pilot.press("enter")  # 进 story 详情（原文一行 + 评论楼层）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-hn-detail")
        await pilot.press("enter")  # 进首条评论的回复树（@ 父作者拍平）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-hn-thread")
        await pilot.press("escape")  # 回 HN 详情
        await pilot.press("escape")  # 回 HN 列表
        await pilot.press("tab")  # Tab 切到 V2EX
        for _ in range(6):
            await pilot.pause()
        await pilot.press("enter")  # 切站落边栏：Enter 回列表
        for _ in range(2):
            await pilot.pause()
        await _demo_read_mix(app, pilot)
        _emit(app, "screenshot-v2ex")
        await pilot.press("enter")  # 进主题详情（markdown 正文 + 回复楼层）
        for _ in range(8):
            await pilot.pause()
        _emit(app, "screenshot-v2ex-detail")


async def _demo_read_mix(app: app_mod.FeedScopeApp, pilot) -> None:
    """列表截图的已读混合态：每隔三条标一条已读再重排。

    截图要同时展示圆点三态（● 新 / ○ 未读 / 空 已读）——全新状态全屏 ●
    看不出「已读会变空」，全已读又看不出特性；隔条混合最像真实使用痕迹。
    """
    main = app.main
    if main is None or not main.items:
        return
    for it in main.items[::3]:
        app.state.mark_read(it.source, it.id)
    await main.rebuild_items()
    for _ in range(2):
        await pilot.pause()


def _emit(app: app_mod.FeedScopeApp, name: str) -> None:
    """导出一张：SVG -> 修 CJK 格宽 -> 栅格化 PNG。"""
    svg, fixed = _fix_cjk_metrics(app.export_screenshot())
    png = DOCS / f"{name}.png"
    _rasterize(svg, png)
    print(f"{png}  {png.stat().st_size // 1024} KB（修正 {fixed} 个 run 的格宽）")


def main() -> None:
    # 隔离本机用户配置（隐藏流会让侧栏条目数漂移）；预写 enabled 跳过首启引导
    # （引导是交互弹层，自动化流程过不去）；FEEDSCOPE_NO_PREWARM 关掉不拉真 Chrome。
    # 已读状态/列表缓存同样钉在仓库根（gitignore），先清再跑：读/缓存都会让截图
    # 漂移（混合已读态由 _demo_read_mix 脚本化生成，不靠上一次运行的残留）
    cfg = DOCS.parent / ".screenshot-sources.toml"
    cfg.write_text('enabled = ["zhihu", "x", "reddit", "hn", "v2ex"]\n', encoding="utf-8")
    state = DOCS.parent / ".screenshot-state.json"
    state.unlink(missing_ok=True)
    cache = DOCS.parent / ".screenshot-feeds"
    if cache.exists():
        for p in cache.iterdir():
            p.unlink()
    os.environ["FEEDSCOPE_CONFIG"] = str(cfg)
    os.environ["FEEDSCOPE_STATE"] = str(state)
    os.environ["FEEDSCOPE_FEED_CACHE"] = str(cache)
    os.environ["FEEDSCOPE_NO_PREWARM"] = "1"
    saved = source_classes()
    install_sources([DemoZhihu, DemoX, DemoReddit, DemoHn, DemoV2ex])
    try:
        asyncio.run(_shots())
    finally:
        install_sources(saved)


if __name__ == "__main__":
    main()
