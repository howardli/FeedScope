"""回答详情屏：多层递进的第三层（列表 -> 问题 -> 回答+评论）。

从问题屏 Enter 进「视口正在阅读」的那个回答（问题屏 detail.py 定位）。
评论按需抓取：进这一屏才调问题 loader 的 open_comments（用户不进就不花
一次请求）；正文秒显（数据已在问题 loader 的 doc 里），评论异步补上。
评论层与问题层共享页签：评论 loader 的 close() 只收评论区，不关底座。
"""

from __future__ import annotations

import webbrowser
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, Markdown, Static

from feedscope.core.models import Comment, FeedItem
from feedscope.core.source import CommentLoader, DetailLoader, Source
from feedscope.ui import feedback
from feedscope.ui.layout import RESIZE_DEBOUNCE, md_body_width
from feedscope.ui.widgets import divider
from feedscope.ui.wrap import wrap_markdown


class AnswerScreen(Screen[None]):
    """单条回答全文 + 它的评论区（评论此刻才请求）。

    Esc 返回问题屏（阅读位置不变）；Ctrl+N 加载更多评论（评论多时一层层翻）；
    [/] 在已加载的回答间换（要更多回答回问题屏按 Ctrl+N）。
    """

    BINDINGS: ClassVar[list[Binding]] = [
        # 键位全部非打印键（输入法免疫）：↑/↓ 滚动走焦点容器（VerticalScroll）
        # 原生键，无需声明
        Binding("escape", "back", "返回"),
        Binding("ctrl+n", "more", "更多评论"),
        Binding("ctrl+o", "open_browser", "浏览器"),
        Binding("right", "next_answer", "下一回答"),
        Binding("left", "prev_answer", "上一回答"),
    ]

    def __init__(
        self, source: Source, item: FeedItem, qloader: DetailLoader, index: int
    ) -> None:
        super().__init__()
        self.source = source
        self.item = item        # 问题条目（Ctrl+O 回退用它）
        self.qloader = qloader  # 问题 loader（共享页签的底座）
        self.index = index
        self.cloader: CommentLoader | None = None  # 评论 loader（契约见 core.source.CommentLoader）
        self._unit = source.comment_unit_label
        self._exhausted = False        # 评论到头，n 不再触发
        self._floor_count = 0          # 评论楼层序号，跨 load_more 递增
        self._prev_sub_title: str | None = None
        self._rendered = False         # 正文已上屏（resize 重排的门槛）
        self._last_width = 0
        self._resize_timer: Timer | None = None
        self._hint: Static | None = None

    @property
    def _answer(self):
        return self.qloader.doc.answers[self.index]

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="answer-body-scroll")
        yield Footer()

    async def on_mount(self) -> None:
        self._prev_sub_title = self.app.sub_title
        self.query_one("#answer-body-scroll", VerticalScroll).focus()
        await self._render_doc()
        self._rendered = True
        self.run_worker(self._load_comments(), group="comments", exclusive=True)

    # ---- 渲染 ----

    def _md_width(self, indent: int = 0) -> int:
        w = md_body_width(self.query_one("#answer-body-scroll", VerticalScroll).region.width)
        return max(w - indent, 20)

    async def _render_doc(self) -> None:
        """整个回答详情上屏（单次 mount = 单次布局）；评论未到先占位。"""
        ans = self._answer
        body = self.query_one("#answer-body-scroll", VerticalScroll)
        widgets: list[Widget] = [Static(escape(f"{ans.author} 的回答"), id="answer-title")]
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
            widgets.append(Static(f"[dim]（没有{self._unit}）[/dim]", id="cm-empty"))
        else:
            widgets.append(Label(f"{self._unit}加载中…", id="cm-loading"))
        if self._exhausted:
            widgets.append(Static(f"[dim]（没有更多{self._unit}了）[/dim]", id="cm-no-more"))
        else:
            self._hint = self._hint_widget()
            widgets.append(self._hint)
        await body.mount(*widgets)

    def _hint_widget(self) -> Static:
        # class 不用 id：hint 会随楼层增删反复挂载，id 会在摘除延迟时撞重
        return Static(
            f"[dim]── Ctrl+N 加载更多{self._unit} · ← → 换回答 · Esc 返回 ──[/dim]", classes="cm-hint"
        )

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
        self.app.sub_title = f"{self._answer.author} 的回答"[:50]
        try:
            self.cloader = await self.qloader.open_comments(self.index)
        except Exception as exc:
            feedback.fail(self.app, self.source, exc, action="评论加载失败")
            self._swap_status("cm-loading", f"[red]评论加载失败：{escape(feedback.failure_text(self.source, exc))} · Ctrl+N 重试[/red]")
            return
        comments = self.cloader.comments
        self._exhausted = self.cloader.done and not comments
        await self._mount_floors(comments, first=True)

    async def _mount_floors(self, comments: list[Comment], *, first: bool) -> None:
        body = self.query_one("#answer-body-scroll", VerticalScroll)
        if first:
            for sel in ("#cm-loading",):
                for w in self.query(sel):
                    await w.remove()
            if not comments:
                self._exhausted = True
                self._remove_hint()
                await body.mount(
                    Static(f"[dim]（没有{self._unit}）[/dim]", id="cm-empty"),
                    Static(f"[dim]（没有更多{self._unit}了）[/dim]", id="cm-no-more"),
                )
                return
        widgets: list[Widget] = []
        for c in comments:
            widgets.extend(self._comment_floor(c))
        await body.mount(*widgets)
        await self._mount_hint()

    async def _swap_status(self, sel: str, text: str) -> None:
        for w in self.query(sel):
            w.update(text)

    def _remove_hint(self) -> None:
        if self._hint is not None:
            w, self._hint = self._hint, None
            try:
                w.remove()  # 同步发起摘除；同 id 重复挂载的防护见 _mount_hint
            except Exception:
                pass

    async def _mount_hint(self) -> None:
        self._remove_hint()
        self._hint = self._hint_widget()
        await self.query_one("#answer-body-scroll", VerticalScroll).mount(self._hint)

    # ---- 键位 ----

    def action_back(self) -> None:
        self.app.pop_screen()

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
        self.run_worker(self._load_more(), group="comments")

    async def _retry_comments(self) -> None:
        for w in self.query("#cm-loading"):
            await w.update(f"{self._unit}加载中…")
        await self._load_comments()

    async def _load_more(self) -> None:
        body = self.query_one("#answer-body-scroll", VerticalScroll)
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
                await body.mount(Static(f"[dim]（没有更多{self._unit}了）[/dim]", id="cm-no-more"))
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
        """←/→ 换回答：同问题内相邻楼层（更多回答回问题屏按 Ctrl+N 加载）。"""
        n_answers = len(self.qloader.doc.answers)
        nxt = self.index + delta
        if not 0 <= nxt < n_answers:
            feedback.info(self.app, "后面没有已加载的回答了" if delta > 0 else "前面没有回答了")
            return
        self.workers.cancel_group(self, "comments")  # 评论还在途：丢弃
        loader, self.cloader = self.cloader, None
        if loader is not None:
            await loader.close()
        self.index = nxt
        self._exhausted = False
        self._floor_count = 0
        self._rendered = False
        self._hint = None
        body = self.query_one("#answer-body-scroll", VerticalScroll)
        await body.remove_children()
        await self._render_doc()
        self._rendered = True
        self.run_worker(self._load_comments(), group="comments", exclusive=True)

    def action_scroll_down(self) -> None:
        self.query_one("#answer-body-scroll", VerticalScroll).scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#answer-body-scroll", VerticalScroll).scroll_up(animate=False)

    # ---- resize 重排（与问题屏同手法）----

    def on_resize(self, event: Resize) -> None:
        if event.size.width == self._last_width:
            return
        first_layout = self._last_width == 0
        self._last_width = event.size.width
        if first_layout or not self._rendered:
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self._rerender)

    def _rerender(self) -> None:
        if not self._rendered:
            return
        scroll_y = self.query_one("#answer-body-scroll", VerticalScroll).scroll_y
        self.run_worker(self._rerender_body(scroll_y))

    async def _rerender_body(self, scroll_y: float) -> None:
        body = self.query_one("#answer-body-scroll", VerticalScroll)
        await body.remove_children()
        self._hint = None
        self._floor_count = 0
        try:
            await self._render_doc()
        except Exception:
            return  # 屏幕正在被关闭：放弃重排
        body.call_after_refresh(body.scroll_to, y=scroll_y, animate=False)

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None
        if self._prev_sub_title is not None:
            self.app.sub_title = self._prev_sub_title
        loader, self.cloader = self.cloader, None
        if loader is not None:
            await loader.close()  # 只收评论区（Esc 关 Modal），问题页签由问题屏管
