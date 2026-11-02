"""离线 TUI 冒烟：stub 源驱动整条 UI 链路。

覆盖：列表加载 / 元信息列（[类型标签] 与作者/热度并列，统一按显示宽截断）/ 方向键导航 / Ctrl+N 加载更多 /
详情屏渲染 / 详情加载更多与到头 / 详情 [/] 换篇（跳过浏览器类型）/ 详情失败 Ctrl+N 重试 /
resize 整屏重排（列表 + 详情）/ Enter 行为分派（详情 vs 浏览器）/
每源信息流记忆 / 主屏守卫键反馈 / 首启源选择引导 / F2 源管理（启停用、
直达键动态寻址、零启用空态）/ 非打印键位（←/→ 切流、Ctrl+N/O/F5/F2；字母/数字键已废弃）/
中文落地指路提示。不碰网络、不开浏览器。

改契约（FeedItem / Source / Loader）或 TUI 行为后必须全绿。
"""

import asyncio
import tomllib
import webbrowser
from pathlib import Path
from typing import ClassVar

import pytest
from textual.containers import VerticalScroll
from textual.widgets import ListItem, Markdown, SelectionList

import feedscope.sources  # noqa: F401  先触发真实源注册
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import DetailLoader, FeedLoader, Source, SourceError
from feedscope.ui.app import FeedScopeApp, meta_col_text, score_col_width
from feedscope.ui.layout import META_COL_MAX
from feedscope.ui.screens.answer import AnswerScreen
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.source_setup import ManageSourcesScreen, OnboardingScreen


def test_meta_col_text_unified_rule() -> None:
    """元信息列统一规则（所有源/流同一条）：[类型标签] 与作者并列；任何情况下不截断。"""
    src = StubSource()
    # 有标签：标签与作者并列，不再顶掉作者
    assert meta_col_text(src, _item(0, "video", "up主")) == "[视频] up主"
    # 无标签（知乎热榜热度 / 知乎信息流与 X 的署名同此路径）：元信息原样
    assert meta_col_text(src, _item(0, "", "42热度")) == "42热度"
    # 长署名整体保留（列宽随批内容自适应，X 的「名字 @handle」完整可读）
    assert meta_col_text(src, _item(0, "", "Chrome开发团队 @ChromeDevTools")) == "Chrome开发团队 @ChromeDevTools"
    # 病态长文本也不截断：超列宽上限的在列内折行（换行可以，缩略不行）
    assert meta_col_text(src, _item(0, "video", "一" * 60)) == "[视频] " + "一" * 60


def test_score_col_width_adapts_to_batch() -> None:
    """元信息列宽 = 批内最长文本 + 2 余量；列宽自适应才放得下完整署名，短批不浪费标题列。"""
    src = StubSource()
    # 批内取最长（X 署名「宝玉 @dotey」11 列决定宽度），右对齐由此对齐
    assert score_col_width(src, [_item(0, "", "宝玉 @dotey"), _item(1, "", "42热度")]) == 13
    # 空列表给最小占位
    assert score_col_width(src, []) == 3
    # 超上限的长署名不加宽列（封顶 META_COL_MAX + 余量）：文本不截，
    # 在列内折行（名字一行、@账号一行）
    assert score_col_width(src, [_item(0, "", "一" * 60)]) == META_COL_MAX + 2


def _item(i: int, kind: str = "", meta: str = "") -> FeedItem:
    title = f"第{i}条标题，附带一段足够长用来触发折行的中文文本……"
    return FeedItem(
        source="stub",
        id=f"i{i}",
        title=title * 2,
        url=f"https://example.com/{i}",
        kind=kind,
        meta=meta,
    )


class StubFeedLoader(FeedLoader):
    def __init__(self, items: list[FeedItem], more: list[FeedItem]) -> None:
        self.items = list(items)
        self._more = more
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[FeedItem]:
        if not self._more:
            self._done = True
            return []
        new, self._more = self._more, []
        self.items.extend(new)
        return new

    async def close(self) -> None:
        pass


class StubCommentsLoader:
    """评论层 stub：首批 2 条（含子回复），n 再给 1 条，之后到头。"""

    def __init__(self) -> None:
        self.comments = [
            Comment("评论甲", "赞 3", "评论内容甲。", "5 小时前",
                    replies=[Comment("楼中楼", "", "子回复内容。", "4 小时前")]),
            Comment("评论乙", "", "评论内容乙。", "3 小时前"),
        ]
        self._more = [Comment("评论丙", "赞 1", "翻页评论内容。", "1 小时前")]
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def load_more(self) -> list[Comment]:
        if not self._more:
            self._done = True
            return []
        new, self._more = self._more, []
        self.comments.extend(new)
        return new

    async def close(self) -> None:
        pass


class StubDetailLoader(DetailLoader):
    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="存根问题标题",
            meta="42 个回答 · 1234 浏览",
            description_md="描述正文，**包含粗体**与[链接](https://example.com)，验证折行不拆标记。",
            answers=[
                Answer(f"作者{i}", f"赞 {i}", f"回答正文第 {i} 条。" + "很长很长的内容，" * 60,
                       comment_count=f"{10 + i} 条评论")
                for i in range(3)
            ],
        )
        # 首批已溢出视口（可滚到末尾再按 Ctrl+N）；第二批也高，确保「Ctrl+N 后视口不动」的
        # 断言能把「不滚动」与「scroll_end / 顶对齐」两类跳屏行为区分开
        self._batches = [
            [Answer(f"作者{3 + i}", "", "第二批回答。" + "很长的后续内容，" * 200) for i in range(2)]
        ]

    @property
    def done(self) -> bool:
        return not self._batches

    async def load_more(self) -> list[Answer]:
        if not self._batches:
            return []
        batch = self._batches.pop(0)
        self.doc.answers.extend(batch)
        return batch

    async def open_comments(self, index: int) -> StubCommentsLoader:
        return StubCommentsLoader()

    async def close(self) -> None:
        pass


class StubSource(Source):
    """注册由 stub_registry fixture 手动完成：模块级 @register 会把测试源泄漏进
    全局注册表，污染其他测试模块。"""
    source_id = "stub"
    label = "存根"
    supports_detail = True
    supports_comments = True  # 驱动第三层（回答+评论）动线
    feeds: ClassVar[list[str]] = ["one", "two"]
    feed_labels: ClassVar[dict[str, str]] = {"one": "一流", "two": "二流"}
    kind_labels: ClassVar[dict[str, str]] = {"video": "视频"}
    detail_kinds: ClassVar[frozenset[str]] = frozenset({"", "topic"})

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        return [_item(0, "topic", "3回复"), _item(1, "video", "up主"), _item(2, "", "42热度")]

    async def open_feed(self, name: str) -> FeedLoader:
        items = await self.fetch_feed(name)
        more = [_item(10 + i, "topic", "5回复") for i in range(2)]
        return StubFeedLoader(items, more)

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return StubDetailLoader()


class StubSourceTwo(StubSource):
    """第二个 stub 源：声明一键直达键 z，驱动「一键直达 + Tab 循环共存」。"""

    source_id = "stub2"
    label = "存根二"
    source_key = "z"
    feeds: ClassVar[list[str]] = ["only"]
    feed_labels: ClassVar[dict[str, str]] = {"only": "唯一流"}

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        return [_item(50, "topic", "9回复")]


class StubSourceBadKey(StubSource):
    """声明了历史上被 UI 占用的直达键（n 曾是「加载更多」）：UI 键位全部
    非打印键后，字母键位整个让给直达键——n 应可绑定，不再跳过警告。"""

    source_id = "stub3"
    label = "存根三"
    source_key = "n"


@pytest.fixture()
def stub_registry():
    """只留 stub 源（仅影响本测试进程），结束后恢复真实注册表。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def opened(monkeypatch):
    """记录而非真开浏览器。"""
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: urls.append(url) or True)
    yield urls


class FailingFeedLoader(StubFeedLoader):
    async def load_more(self) -> list[FeedItem]:
        raise RuntimeError("模拟增量网络中断")  # 非 SourceError：验证出口补「源标签 + 动作」前缀


class FailingSource(Source):
    """失败注入源：驱动错误出口（列表首载 / 列表增量 / 详情首载 + Ctrl+N 重试）。
    注册由 failing_registry fixture 手动完成（同 StubSource，不泄漏）。"""

    source_id = "fail"
    label = "失败源"
    supports_detail = True
    feeds: ClassVar[list[str]] = ["ok", "err"]
    feed_labels: ClassVar[dict[str, str]] = {"ok": "正常流", "err": "爆炸流"}
    detail_kinds = frozenset({"", "topic"})

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        return [_item(0, "topic", "")]

    async def open_feed(self, name: str) -> FeedLoader:
        if name == "err":
            raise SourceError("失败源 请求失败（HTTP 500）：/boom")
        return FailingFeedLoader([_item(0, "topic", "")], [])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        # 首次失败、重试成功：驱动详情屏「n 原地重试」
        self._detail_tries = getattr(self, "_detail_tries", 0) + 1
        if self._detail_tries == 1:
            raise SourceError("失败源 详情打开失败（HTTP 500）")
        return StubDetailLoader()


@pytest.fixture()
def failing_registry():
    """只留失败注入源（仅影响本测试进程），结束后恢复真实注册表。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[FailingSource.source_id] = FailingSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def two_source_registry():
    """两个 stub 源：驱动一键直达键 z 与 Tab 循环共存。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    _REGISTRY[StubSourceTwo.source_id] = StubSourceTwo
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def bad_key_registry():
    """含历史冲突键声明的两个 stub 源：驱动「被释放的字母可绑定直达键」出口。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    _REGISTRY[StubSourceBadKey.source_id] = StubSourceBadKey
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


async def _settle(pilot, cond, timeout: float = 3.0) -> bool:
    """轮询等条件成立：worker / 事件循环需要喘息。"""
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        await pilot.pause()
        if cond():
            return True
    return cond()


async def _run_flow(opened: list[str]) -> None:
    app = FeedScopeApp()
    assert app.theme == "feedscope-editor", "克制的编辑器风主题已启用"
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3), "列表加载"

        rows = app.query(ListItem)
        assert rows.nodes[0].outer_size.height > 1, "长标题折行（行高 > 1）"

        video_meta = str(rows.nodes[1].query_one(".item-score").content)
        assert "视频" in video_meta and "up主" in video_meta, (
            f"类型标签与作者并列（video -> [视频] up主），实际 {video_meta.strip()}"
        )
        hot_meta = str(rows.nodes[2].query_one(".item-score").content)
        assert "42热度" in hot_meta, f"元信息列（热度文本），实际 {hot_meta.strip()}"

        await pilot.press("down")
        assert app.query_one("#feed").index == 1, "↓ 下移"
        await pilot.press("up")
        assert app.query_one("#feed").index == 0, "↑ 上移"

        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(app.items) == 5), "Ctrl+N 加载更多（列表）"

        await pilot.press("ctrl+n")
        assert await _settle(
            pilot,
            lambda: "没有更多了" in app.sub_title and len(app.items) == 5,
        ), "Ctrl+N 到头：状态行常驻提示（不只瞬时 toast）"

        await pilot.resize_terminal(120, 40)
        await asyncio.sleep(0.4)  # 列表 resize 已防抖（与详情屏一致），等稳定重排
        await pilot.pause()
        assert len(app.query(ListItem)) == 5, "列表 resize 重排（条目不丢）"

        # topic 进详情
        await pilot.press("enter")
        assert isinstance(app.screen, DetailScreen), "Enter(topic) -> 详情屏"
        detail = app.screen
        assert await _settle(pilot, lambda: detail._rendered), "详情渲染"
        assert len(detail.loader.doc.answers) == 3, "首批回答 3 条"

        # [/] 换篇：下一篇跳过 Enter 会跳浏览器的类型（video），列表选中同步推进
        await pilot.press("right")
        assert await _settle(
            pilot, lambda: detail._rendered and detail.item.id == "i2"
        ), "] 跳过 video 换到下一篇可读的"
        assert app.query_one("#feed").index == 2, "换篇同步推进列表选中"
        await pilot.press("left")
        assert await _settle(
            pilot, lambda: detail._rendered and detail.item.id == "i0"
        ), "[ 换回上一篇（再跳过 video）"
        await pilot.press("left")
        assert await _settle(
            pilot, lambda: "前面没有可读的了" in _last_notification(app)
        ), "已在首篇：反馈而非静默"
        assert detail.item.id == "i0", "边界处不换篇"

        # 读到末尾再按 Ctrl+N：视口应停在原阅读断点（新楼层从底部接上），不整屏跳走
        body = detail.query_one("#detail-body", VerticalScroll)
        body.scroll_end(animate=False)
        assert await _settle(
            pilot, lambda: body.max_scroll_y > 0 and body.scroll_y >= body.max_scroll_y - 0.5
        ), "读到末尾（首批溢出视口，scroll_y > 0）"
        scroll_before = body.scroll_y

        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(detail.loader.doc.answers) == 5), "Ctrl+N 加载更多回答"
        assert body.scroll_y == scroll_before, "Ctrl+N 后视口停在原阅读断点（不整屏滚动）"
        assert body.max_scroll_y > scroll_before, "新批次确实加长了内容（断言有意义）"

        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: detail._exhausted), "到头判死"
        await pilot.press("ctrl+n")
        assert await _settle(
            pilot, lambda: "没有更多回答" in _last_notification(app)
        ), "到头后再按 Ctrl+N 有反馈（不静默）"

        before = len(detail.query(Markdown))
        await pilot.resize_terminal(130, 40)
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert len(detail.query(Markdown)) == before, (
            f"详情 resize 重排（内容不丢），{before} -> {len(detail.query(Markdown))}"
        )

        await pilot.press("escape")
        assert not isinstance(app.screen, DetailScreen), "Esc 返回列表"

        # 非详情类型 -> 浏览器
        await pilot.press("down")
        await pilot.press("enter")
        assert opened and opened[-1].endswith("/1"), f"Enter(video) -> 浏览器，实际 {opened}"


def test_tui_full_flow(stub_registry, opened) -> None:
    asyncio.run(_run_flow(opened))


def _last_notification(app) -> str:
    notes = list(getattr(app, "_notifications", []))
    return str(notes[-1].message) if notes else ""


async def _run_failure_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        # 增量失败：通知带源标签与消息体（不再是裸异常类名）
        assert await _settle(pilot, lambda: len(app.items) == 1)
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: "失败源 加载更多失败" in _last_notification(app)), (
            f"增量失败要有带源标签的通知，实际 {_last_notification(app)!r}"
        )
        assert "模拟增量网络中断" in _last_notification(app), "通知要带消息体"

        # 首载失败：面包屑「加载失败」+ 列表区常驻空态提示
        await pilot.press("right")
        assert await _settle(pilot, lambda: "加载失败" in app.sub_title), "面包屑标记加载失败"
        hint = str(app.query_one("#empty-hint").content)
        assert "失败源 请求失败（HTTP 500）" in hint, f"空态常驻提示，实际 {hint}"
        await pilot.press("ctrl+o")
        assert await _settle(
            pilot, lambda: "没有选中" in _last_notification(app)
        ), "空列表按 Ctrl+O 有反馈（不静默）"

        # 详情首载失败：屏内常驻失败态（notify 是瞬时的，屏幕不能空着）
        await pilot.press("left")
        assert await _settle(pilot, lambda: len(app.items) == 1)
        await pilot.press("enter")
        assert await _settle(
            pilot,
            lambda: "失败源 详情打开失败" in str(app.screen.query_one("#detail-loading").content),
        ), "详情失败屏内提示"
        assert "重试" in str(app.screen.query_one("#detail-loading").content), "失败行预告 Ctrl+N 重试"
        assert app.sub_title == "加载失败", "失败态常驻状态行"

        # Ctrl+N 原地重试：动线不断（不退出详情屏重进）
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: app.screen._rendered), "Ctrl+N 重试成功渲染"
        assert len(app.screen.loader.doc.answers) == 3, "重试后正常内容上屏"
        await pilot.press("escape")


def test_error_outlets_unified(failing_registry) -> None:
    asyncio.run(_run_failure_flow())


async def _run_guard_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        await pilot.press("enter")
        assert isinstance(app.screen, DetailScreen)
        assert await _settle(pilot, lambda: app.screen._rendered)
        title = app.sub_title  # 详情标题（_load 设置）

        await pilot.press("f5")  # 主屏守卫：详情屏上 F5 不做隐形后台刷新，但有反馈
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert app.sub_title == title, "详情屏上 r 不应顶掉详情标题"
        assert "非列表屏" in _last_notification(app), "F5 被挡下要有反馈（不静默）"

        await pilot.press("escape")
        assert not isinstance(app.screen, DetailScreen), "Esc 返回列表"


def test_app_keys_guarded_on_detail(stub_registry) -> None:
    asyncio.run(_run_guard_flow())


async def _run_source_jump_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        assert app.source_idx == 0 and app.current_source.source_id == "stub"

        await pilot.press("z")  # 一键直达第二个源
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub2" and len(app.items) == 1,
        ), "z 一键直达源"
        assert app.query_one("#source-tab-1").has_class("source-tab-active"), "顶部 tab 高亮新源"
        assert app.query_one("#feed-nav-0").has_class("feed-nav-active"), "侧栏高亮当前流"

        await pilot.press("shift+tab")  # Tab 循环仍可用：回第一个源
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub" and len(app.items) == 3,
        ), "Tab 循环与直达键共存"

        # 鼠标点击：顶部 tab 切站、左侧边栏切流（与键盘共用同一条动作路径）
        await pilot.click("#source-tab-1")
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub2" and len(app.items) == 1,
        ), "点击顶部 tab 切站"
        await pilot.click("#source-tab-0")
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub" and len(app.items) == 3,
        ), "点击顶部 tab 回站"
        await pilot.click("#feed-nav-1")
        assert await _settle(pilot, lambda: app.feed_idx == 1), "点击侧栏切流"
        assert app.query_one("#feed-nav-1").has_class("feed-nav-active"), "侧栏高亮跟随点击"

        await pilot.click("#source-tab-1")
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub2" and len(app.items) == 1,
        ), "点击 tab 切源"
        await pilot.click("#source-tab-0")
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub" and len(app.items) == 3,
        ), "回到第一个源再进详情"
        assert app.feed_idx == 1, "切回站点恢复上次的信息流（每源记忆）"
        assert app.query_one("#feed-nav-1").has_class("feed-nav-active"), "记忆的流高亮"

        # 详情屏上直达键也生效：先退回列表再切源（z 直切知乎，不强迫先 Esc）
        await pilot.press("enter")
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("z")
        assert await _settle(
            pilot,
            lambda: not isinstance(app.screen, DetailScreen)
            and app.current_source.source_id == "stub2"
            and len(app.items) == 1,
        ), "详情屏上 z 直达键退回列表并切源"
        assert app.query_one("#source-tab-1").has_class("source-tab-active"), "切源后顶部 tab 高亮新源"


def test_source_key_jump(two_source_registry) -> None:
    asyncio.run(_run_source_jump_flow())


async def _run_freed_key_flow() -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        # UI 键位已全部非打印键：历史上被「加载更多」占用的 n 如今是合法直达键
        assert not _last_notification(app), f"合法声明不该有警告，实际 {_last_notification(app)!r}"

        await pilot.press("n")  # n 直达 stub3（字母键位整个让给直达键）
        assert await _settle(
            pilot,
            lambda: app.current_source.source_id == "stub3" and len(app.items) == 3,
        ), "被释放的 n 可绑定直达键"

        await pilot.press("shift+tab")  # Tab 循环回到 stub
        assert await _settle(pilot, lambda: app.current_source.source_id == "stub")
        await pilot.press("ctrl+n")  # Ctrl+N 仍是加载更多，与直达键互不干扰
        assert await _settle(pilot, lambda: len(app.items) == 5), "Ctrl+N 仍是加载更多"


def test_freed_letter_claimable_by_source(bad_key_registry) -> None:
    asyncio.run(_run_freed_key_flow())


# ---- 首启源选择引导 + F2 源管理（启停用 / 直达键动态寻址 / 零启用空态） ----


async def _run_onboarding_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "fresh" / "sources.toml"  # 不存在 → 首启
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: isinstance(app.screen, OnboardingScreen)), (
            "配置缺失时先弹首启引导"
        )
        sl = app.screen.query_one(SelectionList)
        assert set(sl.selected) == {"stub", "stub2"}, "引导默认全勾"
        assert "1 存根" in str(sl.get_option_at_index(0).prompt), "勾选行带序号"
        assert "2 存根二" in str(sl.get_option_at_index(1).prompt)
        await pilot.press("down")
        await pilot.press("space")  # 取消第二项（stub2）
        assert set(sl.selected) == {"stub"}
        assert "存根二" in str(sl.get_option_at_index(1).prompt) and "2 存根二" not in str(
            sl.get_option_at_index(1).prompt
        ), "取消勾选即去掉序号"
        await pilot.click("#setup-confirm")

        assert await _settle(pilot, lambda: len(app.items) == 3), "确认后进主界面并加载"
        assert not isinstance(app.screen, OnboardingScreen)
        assert len(app.query(".source-tab")) == 1, "只勾一个源：顶部 tab 只剩一个"
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["enabled"] == ["stub"], (
            "勾选落盘"
        )


def test_onboarding_first_run(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_onboarding_flow(monkeypatch, tmp_path))


async def _run_onboarding_cancel_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "fresh2" / "sources.toml"
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        await pilot.press("escape")  # 引导取消 = 放弃使用，不默认全开
        assert await _settle(pilot, lambda: not app.is_running), "App 退出"
        assert app.return_code == 0
        assert not cfg.exists(), "取消不落盘（下次启动再次引导）"


def test_onboarding_cancel_exits(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_onboarding_cancel_flow(monkeypatch, tmp_path))


async def _run_manage_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "m" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text("", encoding="utf-8")  # 已配置（无 enabled → 全启用），不触发引导
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        assert len(app.query(".source-tab")) == 2

        # 停用 stub2：F2 打开管理 → 空格取消第二项 → 保存
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        sl = app.screen.query_one(SelectionList)
        assert set(sl.selected) == {"stub", "stub2"}, "当前启用的源默认勾上"
        await pilot.press("down")
        await pilot.press("space")
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: len(app.query(".source-tab")) == 1), "保存后 tab 即时重建"
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["enabled"] == ["stub"], "落盘"
        assert await _settle(
            pilot, lambda: app.current_source.source_id == "stub" and len(app.items) == 3
        ), "停在原源继续可读"

        # 停用源的直达键：反馈不静默、不切站
        await pilot.press("z")
        assert await _settle(pilot, lambda: "未启用" in _last_notification(app)), (
            "停用源直达键要有反馈"
        )
        assert app.current_source.source_id == "stub"

        # 再启用：直达键即刻恢复
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("down")
        await pilot.press("space")  # 勾回 stub2
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: len(app.query(".source-tab")) == 2)
        await pilot.press("z")
        assert await _settle(
            pilot, lambda: app.current_source.source_id == "stub2" and len(app.items) == 1
        ), "重新启用后直达键恢复"

        # 管理取消：不落盘不动状态
        before = cfg.read_text(encoding="utf-8")
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("space")  # 勾掉第一项也无所谓——取消
        await pilot.click("#setup-cancel")
        assert await _settle(pilot, lambda: not isinstance(app.screen, ManageSourcesScreen))
        assert cfg.read_text(encoding="utf-8") == before, "取消不落盘"
        assert len(app.query(".source-tab")) == 2


def test_manage_sources_toggle(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_manage_flow(monkeypatch, tmp_path))


async def _run_zero_enabled_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "z0" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)

        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("space")  # 停用 stub
        await pilot.press("down")
        await pilot.press("space")  # 停用 stub2：全不勾也允许保存
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: not app.sources), "全部停用"
        hint = str(app.query_one("#empty-hint").content)
        assert "没有启用的源" in hint and "F2" in hint, "空态常驻提示指路 F2"
        assert app.is_running, "零启用不退出 App（s 随时再开）"

        await pilot.press("ctrl+u")  # 空态守卫：有反馈不崩溃
        assert await _settle(pilot, lambda: "没有启用的源" in _last_notification(app))

        # F2 再开：从空态恢复
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("space")  # 勾回 stub
        await pilot.click("#setup-confirm")
        assert await _settle(
            pilot, lambda: app.current_source.source_id == "stub" and len(app.items) == 3
        ), "空态可恢复"


def test_zero_enabled_empty_state(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_zero_enabled_flow(monkeypatch, tmp_path))


# ---- 展示顺序 = 勾选顺序（enabled 落盘同序，重启保持） ----


async def _run_check_order_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "ord" / "sources.toml"
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))  # 不存在 → 首启
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        # 取消再勾回第一项（stub）：勾上的排末尾 → 顺序 [stub2, stub]
        await pilot.press("space")
        await pilot.press("space")
        assert app.screen.selected_ids == ["stub2", "stub"]
        sl = app.screen.query_one(SelectionList)
        assert "1 存根二" in str(sl.get_option_at_index(1).prompt), "序号跟随勾选顺序实时重排"
        assert "2 存根" in str(sl.get_option_at_index(0).prompt)
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: len(app.items) == 3)
        assert [s.source_id for s in app.sources] == ["stub2", "stub"], "展示顺序 = 勾选顺序"
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["enabled"] == ["stub2", "stub"], (
            "落盘顺序与勾选顺序一致"
        )
        first_tab = str(app.query(".source-tab").nodes[0].content)
        assert "存根二" in first_tab, "顶部 tab 首位是后仍勾选着的存根二"


def test_onboarding_check_order(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_check_order_flow(monkeypatch, tmp_path))


async def _run_config_order_flow(monkeypatch, tmp_path: Path) -> None:
    """手改/落盘的 enabled 书写顺序在重启后原样生效。"""
    cfg = tmp_path / "saved" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text('enabled = ["stub2", "stub"]\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 1), "首源是 stub2（1 条）"
        assert [s.source_id for s in app.sources] == ["stub2", "stub"]
        assert app.query(".source-tab").nodes[0].has_class("source-tab-active"), "首位源即当前源"


def test_config_order_respected_on_restart(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_config_order_flow(monkeypatch, tmp_path))


async def _run_manage_reorder_flow(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "re" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        # 取消再勾回第一项（stub）：移到末尾 → [stub2, stub]
        await pilot.press("space")
        await pilot.press("space")
        assert app.screen.selected_ids == ["stub2", "stub"]
        sl = app.screen.query_one(SelectionList)
        assert "1 存根二" in str(sl.get_option_at_index(1).prompt), "管理界面同样实时标序号"
        assert "2 存根" in str(sl.get_option_at_index(0).prompt)
        await pilot.click("#setup-confirm")
        assert await _settle(
            pilot, lambda: [s.source_id for s in app.sources] == ["stub2", "stub"]
        ), "保存后按新顺序重建站点栏"
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["enabled"] == ["stub2", "stub"]
        assert await _settle(
            pilot, lambda: app.current_source.source_id == "stub" and len(app.items) == 3
        ), "顺序变了但停在原源继续可读"


def test_manage_reorder(two_source_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_manage_reorder_flow(monkeypatch, tmp_path))


async def _run_answer_layer_flow(opened: list[str]) -> None:
    """第三层动线：问题屏 Enter 按可视位置进回答（读到哪进哪）-> 评论按需 -> Ctrl+N 翻页 -> [/] 换回答 -> Esc 返回。"""
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)
        await pilot.press("enter")
        detail = app.screen
        assert isinstance(detail, DetailScreen)
        assert await _settle(pilot, lambda: detail._rendered)

        # 视口还在顶部（标题/描述区）：Enter 落到回答 1，不用先滚
        await pilot.press("enter")
        assert await _settle(pilot, lambda: isinstance(app.screen, AnswerScreen)), "Enter -> 回答详情屏"
        answer = app.screen
        assert answer.index == 0, "顶部 Enter 进第一个回答"
        assert await _settle(pilot, lambda: answer.cloader is not None), "评论此刻才请求（按需）"
        assert "作者0 的回答" in str(answer.query_one("#answer-title").content), "正文标题来自已加载 doc"
        assert await _settle(pilot, lambda: len(answer.query(".comment-head")) >= 3), "评论楼层（含子回复）上屏"
        assert any(
            "楼中楼" in str(w.content) for w in answer.query(".comment-head")
        ), "子回复渲染"

        # Ctrl+N 翻页评论；再到头
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(answer.cloader.comments) == 3), "Ctrl+N 加载更多评论"
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: answer._exhausted), "评论到头"

        # [/] 换回答：换到下一条，评论重新按需请求
        await pilot.press("right")
        assert await _settle(pilot, lambda: answer.index == 1 and answer.cloader is not None), "] 换回答并重开评论"
        assert "作者1 的回答" in str(answer.query_one("#answer-title").content)

        # Ctrl+O 打开该回答直达链接
        await pilot.press("ctrl+o")
        assert opened and opened[-1].endswith("/0"), f"Ctrl+O -> 回答 URL，实际 {opened}"

        # Esc 逐层返回：回答屏 -> 问题屏（位置不变）-> 列表
        await pilot.press("escape")
        assert app.screen is detail, "Esc 回问题屏（实例不变，位置不丢）"

        # 滚到第三个回答再 Enter：读到哪进哪（楼层定位）
        heads = detail.query(".answer-head")
        detail.query_one("#detail-body", VerticalScroll).scroll_to_widget(heads[2], top=True, animate=False)
        await pilot.pause()
        await pilot.press("enter")
        assert await _settle(pilot, lambda: isinstance(app.screen, AnswerScreen)), "滚动后 Enter 仍进回答屏"
        assert app.screen.index == 2, "Enter 进的是视口所在的回答 3"
        await pilot.press("escape")

        await pilot.press("escape")
        assert not isinstance(app.screen, DetailScreen), "再 Esc 回列表"


def test_answer_layer_flow(stub_registry, opened) -> None:
    asyncio.run(_run_answer_layer_flow(opened))

# ---- 非打印键位：中文输入法开启也不受影响；字母/数字键已废弃 ----


async def _run_ime_safe_flow(opened: list[str]) -> None:
    """键位全部非打印键（方向键/F 键/Ctrl 组合），一个动作只留一个键。

    顺带验证：字母/数字旧键位确已废弃（按了不动），以及中文落地指路——
    应用无法探测输入法状态，上屏字符是唯一可靠信号（本应用没有文本输入框，
    中文必是误输入）。
    """
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)

        # ←/→ 相对切流，到边界给反馈不静默
        await pilot.press("right")
        assert await _settle(pilot, lambda: app.feed_idx == 1 and len(app.items) == 3), "→ 切流"
        assert app.query_one("#feed-nav-1").has_class("feed-nav-active"), "侧栏高亮跟随"
        await pilot.press("right")
        assert await _settle(pilot, lambda: "已是最后一个流" in _last_notification(app)), "到边界有反馈"
        await pilot.press("left")
        assert await _settle(pilot, lambda: app.feed_idx == 0 and len(app.items) == 3), "← 切回流"

        # 旧字母/数字键已废弃：按下无任何效果（不被任何绑定命中）
        await pilot.press("n")
        await pilot.press("r")
        await pilot.press("1")
        await pilot.pause()
        assert len(app.items) == 3 and app.feed_idx == 0, "n/r/1 均已废弃（无副作用）"
        await pilot.press("j")
        await pilot.pause()
        assert app.query_one("#feed").index == 0, "j 已废弃（选中不动，移动走 ↑/↓）"

        # Ctrl+N 加载更多；F5 刷新回首批
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(app.items) == 5), "Ctrl+N 加载更多"
        await pilot.press("f5")
        assert await _settle(pilot, lambda: len(app.items) == 3), "F5 刷新回首批"

        # Ctrl+O 浏览器打开当前选中条目
        await pilot.press("ctrl+o")
        assert opened and opened[-1].endswith("/0"), f"Ctrl+O -> 浏览器，实际 {opened}"

        # F2 源管理弹层；Ctrl+U 账号（stub 源免登录的提示出口）
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen)), "F2 源管理"
        await pilot.press("escape")
        await pilot.press("ctrl+u")
        assert await _settle(pilot, lambda: "无需登录" in _last_notification(app)), "Ctrl+U 账号"

        # 中文落地 → 指路提示（键位已免疫，提示只点明 z/x/r 直达键仍需英文输入法）
        await pilot.press("你")
        assert await _settle(pilot, lambda: "输入法" in _last_notification(app)), "中文输入要指路"

        # 详情/评论屏的 Ctrl+N / Ctrl+O 在屏级（进各屏自己的动作，不走 App 的列表路径）
        await pilot.press("enter")
        detail = app.screen
        assert isinstance(detail, DetailScreen)
        assert await _settle(pilot, lambda: detail._rendered)
        before = len(detail.loader.doc.answers)
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(detail.loader.doc.answers) == before + 2), "详情屏 Ctrl+N"
        await pilot.press("ctrl+o")
        assert opened[-1].endswith("/0"), "详情屏 Ctrl+O -> 浏览器"

        await pilot.press("enter")  # 进回答层（评论此刻才请求）
        answer = app.screen
        assert isinstance(answer, AnswerScreen)
        assert await _settle(pilot, lambda: answer.cloader is not None)
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(answer.cloader.comments) == 3), "评论层 Ctrl+N"


def test_ime_safe_keys(stub_registry, opened) -> None:
    asyncio.run(_run_ime_safe_flow(opened))


async def _run_focus_free_flow() -> None:
    """切流 / Ctrl+N 在 App 级：焦点不在列表上也必须生效（曾为 FeedListView
    控件级绑定，焦点落位竞态时整个失灵）。"""
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await _settle(pilot, lambda: len(app.items) == 3)

        app.set_focus(None)  # 模拟焦点丢失/未落位
        assert app.focused is None
        await pilot.press("right")
        assert await _settle(pilot, lambda: app.feed_idx == 1 and len(app.items) == 3), (
            "焦点不在列表上 → 仍能切流"
        )
        await pilot.press("ctrl+n")
        assert await _settle(pilot, lambda: len(app.items) == 5), "焦点不在列表上 → Ctrl+N 仍生效"
        await pilot.press("left")
        assert await _settle(pilot, lambda: app.feed_idx == 0 and len(app.items) == 3)


def test_keys_focus_free(stub_registry) -> None:
    asyncio.run(_run_focus_free_flow())
