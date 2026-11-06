"""回答详情屏：多层递进的第三层（列表 -> 问题 -> 回答+评论）。

从问题屏 Enter 进「视口正在阅读」的那个回答（问题屏 detail.py 定位）。
评论按需抓取：进这一屏才调问题 loader 的 open_comments（用户不进就不花
一次请求）；正文秒显（数据已在问题 loader 的 doc 里），评论异步补上。
评论层与问题层共享页签：评论 loader 的 close() 只收评论区，不关底座。
与问题屏共享的阅读屏机械件（resize 重排 / hint 生命周期 / 清理）在
ReadingScreen。
"""

from __future__ import annotations

import dataclasses
import webbrowser
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, Markdown, Static

from feedscope.core.models import Comment, FeedItem
from feedscope.core.source import CommentLoader, DetailLoader, Source
from feedscope.ui import feedback
from feedscope.ui.layout import md_body_width
from feedscope.ui.screens.reading import READING_CSS, ReadingScreen, dim_line, divider
from feedscope.ui.wrap import wrap_markdown


class AnswerScreen(ReadingScreen):
    """单条回答全文 + 它的评论区（评论此刻才请求）。

    Esc 返回问题屏（阅读位置不变）；Ctrl+N 加载更多评论（评论多时一层层翻）；
    [/] 在已加载的回答间换（要更多回答回问题屏按 Ctrl+N）。
    """

    BINDINGS: ClassVar[list[Binding]] = [
        # 键位全部非打印键（输入法免疫）：↑/↓ 滚动走焦点容器（VerticalScroll）
        # 原生键，无需声明。标签是占位：__init__ 按源词汇表换成
        # 「更多{评论词}/下一{楼层词}」（知乎渲染与本列表逐字相同）；
        # Esc 返回继承自 ReadingScreen
        Binding("ctrl+n", "more", "更多评论"),
        Binding("ctrl+o", "open_browser", "浏览器"),
        Binding("right", "next_answer", "下一回答"),
        Binding("left", "prev_answer", "上一回答"),
    ]

    CSS = READING_CSS + """
    #answer-title { text-style: bold; padding: 1 2; border: ascii; width: 1fr; text-wrap: wrap; }
    #answer-meta { padding: 0 2; }
    #answer-content { padding: 0 2; }
    .comments-header { padding: 1 2; text-style: bold; }
    .comment-head { padding: 1 2 0 2; text-wrap: wrap; }  /* 与 .answer-head 同：折行不裁剪 */
    .comment-body { padding: 0 2; }
    .comment-head.comment-sub { margin-left: 3; }
    .comment-body.comment-sub { margin-left: 3; }
    #cm-loading, #cm-more-loading { padding: 1 2; }
    #cm-empty, #cm-no-more, .cm-hint { padding: 1; }
    """

    def __init__(
        self, source: Source, item: FeedItem, qloader: DetailLoader, index: int
    ) -> None:
        super().__init__()
        self.source = source
        self.item = item        # 问题条目（Ctrl+O 回退用它）
        self.qloader = qloader  # 问题 loader（共享页签的底座）
        self.index = index
        self.cloader: CommentLoader | None = None  # 评论 loader（契约见 core.source.CommentLoader）
        # 词汇表归源：本屏两层各有单位词——楼层（← → 切换的对象，知乎「回答」、
        # HN「评论」）与评论（Ctrl+N 翻的对象，知乎「评论」、HN「回复」）；键位
        # 文案随词换（类级 BINDINGS 是占位，知乎渲染与内置文案逐字相同）
        self._floor_unit = source.detail_unit_label
        self._unit = source.comment_unit_label
        for key, label in (
            ("ctrl+n", f"更多{self._unit}"),
            ("right", f"下一{self._floor_unit}"),
            ("left", f"上一{self._floor_unit}"),
        ):
            self._bindings.key_to_bindings[key] = [
                dataclasses.replace(b, description=label)
                for b in self._bindings.key_to_bindings.get(key, [])
            ]
        self._exhausted = False        # 评论到头，n 不再触发
        self._floor_count = 0          # 评论楼层序号，跨 load_more 递增

    @property
    def _answer(self):
        return self.qloader.doc.answers[self.index]

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="answer-body-scroll")
        yield Footer()

    def _body_scroll(self) -> VerticalScroll:
        return self.query_one("#answer-body-scroll", VerticalScroll)

    async def _mount_body(self) -> None:
        self._body_scroll().focus()
        await self._render_doc()
        self._rendered = True
        self.run_worker(self._load_comments(), group="comments", exclusive=True)

    def _reset_render_state(self) -> None:
        self._floor_count = 0

    async def _close_loader(self) -> None:
        loader, self.cloader = self.cloader, None
        if loader is not None:
            await loader.close()  # 只收评论区（Esc 关 Modal），问题页签由问题屏管

    def _hint_widget(self) -> Static:
        # class 不用 id：hint 会随楼层增删反复挂载，id 会在摘除延迟时撞重。
        # 只留 Footer 没有的语义增量（加载什么）；换楼层/返回是 Footer 的键位职责
        return Static(
            f"[dim]── Ctrl+N 加载更多{self._unit} ──[/dim]",
            classes="cm-hint",
        )

    # ---- 渲染 ----

    def _md_width(self, indent: int = 0) -> int:
        w = md_body_width(self._body_scroll().region.width)
        return max(w - indent, 20)

    async def _render_doc(self) -> None:
        """整个回答详情上屏（单次 mount = 单次布局）；评论未到先占位。"""
        ans = self._answer
        body = self._body_scroll()
        title = Static(escape(f"{ans.author} 的{self._floor_unit}"), id="answer-title")
        widgets: list[Widget] = [title]
        meta = " · ".join(x for x in (ans.votes, ans.comment_count, ans.time) if x)
        if meta:
            widgets.append(Static(f"[dim]{escape(meta)}[/dim]", id="answer-meta"))
        widgets.append(
            Markdown(wrap_markdown(ans.content_md, self._md_width()), id="answer-content")
        )
        widgets.extend([divider(), Static(f"[b]{self._unit}[/b]", classes="comments-header")])
        if self.cloader is not None and self.cloader.comments:
            for c in self.cloader.comments:
                widgets.extend(self._comment_floor(c))
        elif self._exhausted:
            widgets.append(dim_line(f"（没有{self._unit}）", id="cm-empty"))
        else:
            widgets.append(Label(f"{self._unit}加载中…", id="cm-loading"))
        if self._exhausted:
            widgets.append(dim_line(f"（没有更多{self._unit}了）", id="cm-no-more"))
        else:
            self._hint = self._hint_widget()
            widgets.append(self._hint)
        await body.mount(*widgets)

    def _comment_floor(self, c: Comment) -> list[Widget]:
        """一条顶层评论的楼层部件（头部 + 内容 + 缩进的子回复）。"""
        self._floor_count += 1
        parts = [f"[b]{self._unit} {self._floor_count}[/b]", f"[b]{escape(c.author)}[/b]"]
        if c.votes:
            parts.append(f"[dim]{escape(c.votes)}[/dim]")
        if c.time:
            parts.append(f"[dim]{escape(c.time)}[/dim]")
        widgets = [
            divider(),
            Static("  ".join(parts), classes="comment-head"),
            Markdown(wrap_markdown(c.content_md, self._md_width()), classes="comment-body"),
        ]
        for r in c.replies:
            sub_parts = [f"[b]{escape(r.author)}[/b]"]
            if r.votes:
                sub_parts.append(f"[dim]{escape(r.votes)}[/dim]")
            if r.time:
                sub_parts.append(f"[dim]{escape(r.time)}[/dim]")
            widgets.extend(
                [
                    Static("  ".join(sub_parts), classes="comment-head comment-sub"),
                    Markdown(
                        wrap_markdown(r.content_md, self._md_width(3)),
                        classes="comment-body comment-sub",
                    ),
                ]
            )
        # more_replies 不再提示：child 接口已拉到 is_end，残留只可能是站方
        # 计数口径差（count 含被删/折叠的子回复）或个别补全失败——重试也
        # 拿不到，提示只是阅读流里的噪音
        return widgets

    # ---- 评论加载（按需）----

    async def _load_comments(self) -> None:
        """此刻才请求：问题 loader 点开该回答的评论区（共享页签）。

        sub_title 在这设而非 on_mount：worker 晚于 Header 挂载，同步设
        不会踩 HeaderTitle 未挂的时序坑。
        """
        self.app.sub_title = f"{self._answer.author} 的{self._floor_unit}"[:50]
        try:
            self.cloader = await self.qloader.open_comments(self.index)
        except Exception as exc:
            feedback.fail(self.app, self.source, exc, action="评论加载失败")
            self._swap_status(
                "cm-loading",
                f"[red]评论加载失败：{escape(feedback.failure_text(self.source, exc))}"
                " · Ctrl+N 重试[/red]",
            )
            return
        comments = self.cloader.comments
        self._exhausted = self.cloader.done and not comments
        await self._mount_floors(comments, first=True)

    async def _mount_floors(self, comments: list[Comment], *, first: bool) -> None:
        body = self._body_scroll()
        if first:
            for sel in ("#cm-loading",):
                for w in self.query(sel):
                    await w.remove()
            if not comments:
                self._remove_hint()
                tail: list[Widget] = [dim_line(f"（没有{self._unit}）", id="cm-empty")]
                if self._exhausted:
                    # 到头且为空 = 真没有评论（EOF 标记）
                    tail.append(dim_line(f"（没有更多{self._unit}了）", id="cm-no-more"))
                else:
                    # 首批拉空但没到头（网慢）：不判死，尾行保留 Ctrl+N 重试预告
                    self._hint = self._hint_widget()
                    tail.append(self._hint)
                await body.mount(*tail)
                return
        widgets: list[Widget] = []
        for c in comments:
            widgets.extend(self._comment_floor(c))
        await body.mount(*widgets)
        await self._mount_hint()

    async def _swap_status(self, sel: str, text: str) -> None:
        for w in self.query(sel):
            w.update(text)

    # ---- 键位 ----

    def action_open_browser(self) -> None:
        webbrowser.open(self._answer.url or self.item.url)

    def action_more(self) -> None:
        if self.cloader is None:
            # 评论首载失败（或还在途）时 Ctrl+N = 原地重试（与问题屏首载重试同动线）
            self.run_worker(self._retry_comments(), group="comments", exclusive=True)
            return
        if self._exhausted:
            feedback.info(self.app, f"没有更多{self._unit}了")
            return
        self.run_worker(self._load_more(), group="comments", exclusive=True)

    async def _retry_comments(self) -> None:
        for w in self.query("#cm-loading"):
            await w.update(f"{self._unit}加载中…")
        await self._load_comments()

    async def _load_more(self) -> None:
        body = self._body_scroll()
        self._remove_hint()
        loading = Label("[dim]加载更多…[/dim]", id="cm-more-loading")
        await body.mount(loading)
        try:
            new = await self.cloader.load_more()
        except Exception as exc:
            feedback.fail(self.app, self.source, exc, action="加载更多失败")
            new = []
        if loading.parent is not None:
            loading.remove()
        if not new:
            if self.cloader.done:
                self._exhausted = True
                self._remove_hint()
                await body.mount(dim_line(f"（没有更多{self._unit}了）", id="cm-no-more"))
            else:  # 拉空没到头（网慢/懒加载没触发）：可再按 Ctrl+N
                await self._mount_hint()
            return
        await self._mount_floors(new, first=False)
        # 与问题屏同一取舍：新楼层接在底部，视口停在原阅读断点不跳

    async def action_next_answer(self) -> None:
        await self._retarget(1)

    async def action_prev_answer(self) -> None:
        await self._retarget(-1)

    async def _retarget(self, delta: int) -> None:
        """←/→ 换楼层：同问题内相邻楼层（更多楼屔回问题屏按 Ctrl+N 加载）。"""
        n_answers = len(self.qloader.doc.answers)
        nxt = self.index + delta
        if not 0 <= nxt < n_answers:
            feedback.info(
                self.app,
                f"后面没有已加载的{self._floor_unit}了"
                if delta > 0
                else f"前面没有{self._floor_unit}了",
            )
            return
        self.workers.cancel_group(self, "comments")  # 评论还在途：丢弃
        loader, self.cloader = self.cloader, None
        if loader is not None:
            await loader.close()
        self.index = nxt
        self._exhausted = False
        self._reset_render_state()
        self._rendered = False
        self._remove_hint()
        body = self._body_scroll()
        await body.remove_children()
        await self._render_doc()
        self._rendered = True
        self.run_worker(self._load_comments(), group="comments", exclusive=True)
