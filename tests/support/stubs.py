"""桩源与桩 loader：驱动 TUI 与跨源列表的测试（注册由 fixture / 用例手动完成）。

模块级不用 @register——那会把测试源泄漏进全局注册表，污染其他测试模块；
要用哪个桩，由各子树 conftest.py 的 fixture 或用例自己 install。
"""

from __future__ import annotations

from typing import ClassVar

from feedscope.core.errors import SourceError
from feedscope.core.loaders import (
    DetailLoader,
    FeedLoader,
    StaticDetailLoader,
    StaticFeedLoader,
)
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem
from feedscope.core.source import Source

# ---- 基础桩：条目 / loader / 详情（StubSource 家族）----


def stub_item(i: int, kind: str = "", meta: str = "") -> FeedItem:
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


class EmptyCommentsLoader:
    """评论首载为空但没到头（网慢）：驱动「空态在 resize 重排后不能退回加载中」。"""

    def __init__(self) -> None:
        self.comments: list[Comment] = []
        self.done = False

    async def load_more(self) -> list[Comment]:
        return []

    async def close(self) -> None:
        pass


class StubDetailLoader(DetailLoader):
    def __init__(self) -> None:
        self.doc = DetailDoc(
            title="存根问题标题",
            meta="42 个回答 · 1234 浏览",
            description_md=(
                "# 一级标题行\n## 二级标题行\n"
                "描述正文，**包含粗体**与[链接](https://example.com)，验证折行不拆标记。"
            ),
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
        return [stub_item(0, "topic", "3回复"), stub_item(1, "video", "up主"), stub_item(2, "", "42热度")]

    async def open_feed(self, name: str) -> FeedLoader:
        items = await self.fetch_feed(name)
        more = [stub_item(10 + i, "topic", "5回复") for i in range(2)]
        return StubFeedLoader(items, more)

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        return StubDetailLoader()


class StubSourceTwo(StubSource):
    """第二个 stub 源：驱动「Tab 循环 + 顶部 tab 点击」两条切源路径。"""

    source_id = "stub2"
    label = "存根二"
    feeds: ClassVar[list[str]] = ["only"]
    feed_labels: ClassVar[dict[str, str]] = {"only": "唯一流"}

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        return [stub_item(50, "topic", "9回复")]


class StubSourceNoComments(StubSource):
    """无评论层源（同 X/RSS 形态，supports_comments=False）：详情屏不该预告
    Enter 进评论——键位与尾行动线都按源能力声明退场。"""

    source_id = "stub_nc"
    label = "存根无评论"
    supports_comments = False


# ---- 失败注入桩：驱动各错误出口（列表首载 / 列表增量 / 详情首载与重试）----


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
        return [stub_item(0, "topic", "")]

    async def open_feed(self, name: str) -> FeedLoader:
        if name == "err":
            raise SourceError("失败源 请求失败（HTTP 500）：/boom")
        return FailingFeedLoader([stub_item(0, "topic", "")], [])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        # 首次失败、重试成功：驱动详情屏「n 原地重试」
        self._detail_tries = getattr(self, "_detail_tries", 0) + 1
        if self._detail_tries == 1:
            raise SourceError("失败源 详情打开失败（HTTP 500）")
        return StubDetailLoader()


# ---- 文档桩：详情 = 标题带「源:条目」标记的静态文档（跨源换篇用）----


def doc_item(source: str, i: int, kind: str = "") -> FeedItem:
    return FeedItem(source=source, id=f"i{i}", title=f"{source} 第{i}条", url=f"https://e.com/{source}/{i}", kind=kind)


class DocSource(Source):
    """开详情 = 一份标题带「源:条目」标记的静态文档：换篇后看标题就知道用对了源、开对了条目。
    opened 是类级记录（屏内用的是 App 自己建的实例，记在类上才读得到）。"""

    supports_detail = True
    supports_comments = False
    detail_kinds = frozenset({""})  # video 这类 Enter 跳浏览器，不进详情
    feeds: ClassVar[list[str]] = ["main"]
    feed_labels: ClassVar[dict[str, str]] = {"main": "主"}
    opened: ClassVar[list[str]] = []

    async def open_feed(self, name: str) -> FeedLoader:
        # KINDS 是 (kind, 序号)
        return StaticFeedLoader([doc_item(self.source_id, i, kind) for kind, i in self.KINDS])

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        type(self).opened.append(f"{self.source_id}:{item.id}")
        return StaticDetailLoader(DetailDoc(title=f"{self.source_id}:{item.id}"))


class SourceA(DocSource):
    source_id = "ka"
    label = "源甲"
    KINDS = [("", 0), ("video", 1), ("", 2), ("", 3)]
    opened: ClassVar[list[str]] = []


class SourceB(DocSource):
    source_id = "kb"
    label = "源乙"
    KINDS = [("", 0), ("", 1)]
    opened: ClassVar[list[str]] = []
