"""回答详情屏：多层递进的第三层（列表 -> 问题 -> 回答+评论）。

从问题屏 Enter 进「视口正在阅读」的那个回答（问题屏 detail.py 定位）。
评论按需抓取：进这一屏才调问题 loader 的 open_comments（用户不进就不花
一次请求）；正文秒显（数据已在问题 loader 的 doc 里），评论异步补上。
评论层与问题层共享页签：评论 loader 的 close() 只收评论区，不关底座。
翻页动线 / 尾行状态机 / ←/→ 换楼层的机械件在 PagedReadingScreen，本屏
只剩首载编排（评论按需）与整屏渲染。
"""

from __future__ import annotations

from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, Markdown, Static

from feedscope.core.models import Comment, FeedItem
from feedscope.core.source import CommentLoader, DetailLoader, Source
from feedscope.core.i18n import t
from feedscope.ui import Bindings, feedback
from feedscope.ui.screens.reading import (
    PagedReadingScreen,
    READING_CSS,
    dim_line,
    divider,
    floor_head,
)
from feedscope.ui.wrap import wrap_markdown


class AnswerScreen(PagedReadingScreen):
    """单条回答全文 + 它的评论区（评论此刻才请求）。

    Esc 返回问题屏（阅读位置不变）；Ctrl+N 加载更多评论（评论多时一层层翻）；
    ←/→ 在已加载的回答间换（要更多回答回问题屏按 Ctrl+N）。
    """

    BINDINGS: ClassVar[Bindings] = [
        # 键位全部非打印键（输入法免疫）：↑/↓ 滚动走焦点容器（VerticalScroll）
        # 原生键，无需声明。标签是占位（空串）：__init__ 按源词汇表换成
        # 「更多{评论词}/下一{楼层词}」（知乎渲染与本列表逐字相同）；
        # Esc 返回继承自 ReadingScreen。页脚只留 Esc + F1（其余提示退入 F1）
        Binding("ctrl+n", "more", "", show=False),
        Binding("ctrl+o", "open_browser", "bindings.browser", show=False),
        Binding("right", "next_answer", "", show=False),
        Binding("left", "prev_answer", "", show=False),
    ]

    # Markdown 标题规则必须 CSS 级（见 reading.READING_CSS 的说明），其余
    # 共享样式走 ReadingScreen.DEFAULT_CSS
    CSS = READING_CSS + """
    .comments-header { padding: 1 2; text-style: bold; }
    #cm-loading { padding: 1 2; }
    """

    _worker_group = "comments"  # 评论首载/重试/翻页同组互斥（换楼层整组取消）

    def __init__(
        self, source: Source, item: FeedItem, qloader: DetailLoader, index: int
    ) -> None:
        super().__init__()
        self.source = source
        self.item = item        # 问题条目（Ctrl+O 兜底用它）
        self.qloader = qloader  # 问题 loader（共享页签的底座）
        self.index = index
        self.loader: CommentLoader | None = None  # 评论 loader（契约见 core.source.CommentLoader）
        # 词汇表归源：本屏两层各有单位词——楼层（←/→ 切换的对象，知乎「回答」、
        # HN「评论」）与评论（Ctrl+N 翻的对象，知乎「评论」、HN「回复」）；键位
        # 文案随词换（类级 BINDINGS 是占位，知乎渲染与内置文案逐字相同）
        self._floor_unit = source.detail_unit
        self._relabel_bindings(
            {
                "ctrl+n": t("read.binding_more_unit", unit=self._unit),
                "right": t("read.binding_next_unit", unit=self._floor_unit),
                "left": t("read.binding_prev_unit", unit=self._floor_unit),
            }
        )
        self._floor_count = 0          # 评论楼层序号，跨 load_more 递增
        self._comments_error: str | None = None  # 首载失败文案（resize 重排时保持失败态）

    @property
    def _answer(self):
        return self.qloader.doc.answers[self.index]

    @property
    def _unit(self) -> str:
        """Ctrl+N 翻页对象的单位词（评论词，源词汇表直读，与问题屏对称）。"""
        return self.source.comment_unit

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="answer-body-scroll")
        yield Footer()

    def _body_scroll(self) -> VerticalScroll:
        return self.query_one("#answer-body-scroll", VerticalScroll)

    async def _mount_body(self) -> None:
        self._body_scroll().focus()
        await self._render_doc()
        self._render_done()
        self.run_worker(self._load_comments(), group="comments", exclusive=True)

    def _reset_render_state(self) -> None:
        self._floor_count = 0

    # 卸载清理继承 PagedReadingScreen：只换出并关评论 loader（问题页签由问题屏管）

    def _hint_widget(self) -> Static:
        # class 不用 id：hint 会随楼层增删反复挂载，id 会在摘除延迟时撞重。
        # 只留 Footer 没有的语义增量（加载什么）；换楼层/返回是 Footer 的键位职责
        return Static(
            f"[dim]── {t('read.hint_more', unit=self._unit)} ──[/dim]",
            classes="tail-line tail-hint",
        )

    # ---- 渲染 ----

    async def _render_doc(self) -> None:
        """整个回答详情上屏（单次 mount = 单次布局）；评论未到先占位。"""
        ans = self._answer
        body = self._body_scroll()
        title = Static(
            escape(t("read.answer_title", author=ans.author, unit=self._floor_unit)),
            id="answer-title",
            classes="read-title",
        )
        widgets: list[Widget] = [title]
        meta = " · ".join(x for x in (ans.votes, ans.comment_count, ans.time) if x)
        if meta:
            widgets.append(
                Static(f"[dim]{escape(meta)}[/dim]", id="answer-meta", classes="read-meta")
            )
        widgets.append(
            Markdown(wrap_markdown(ans.content_md, self._md_width()), id="answer-content",
                     classes="read-main")
        )
        widgets.extend([divider(), Static(f"[b]{self._unit}[/b]", classes="comments-header")])
        if self.loader is not None:
            if self.loader.comments:
                for c in self.loader.comments:
                    widgets.extend(self._comment_floor(c))
            else:
                # 首批已回来但为空：到没到头都显示空态，别退回「加载中」（resize 重排
                # 走的是同一渲染路径，状态必须与首次上屏一致）
                widgets.append(
                    dim_line(t("read.none", unit=self._unit), classes="tail-line tail-empty")
                )
            if self._exhausted:
                widgets.append(
                    dim_line(
                        t("read.no_more", unit=self._unit), classes="tail-line tail-eof"
                    )
                )
            else:
                self._hint = self._hint_widget()
                widgets.append(self._hint)
        elif self._comments_error is not None:
            # 首载失败：保留失败态与 Ctrl+N 重试入口（错误行本身已说明动线）
            widgets.append(
                Static(
                    f"[red]{t('read.comments_failed_retry', text=escape(self._comments_error))}[/red]",  # noqa: E501
                    id="cm-loading",
                )
            )
        else:
            widgets.append(Label(t("read.unit_loading", unit=self._unit), id="cm-loading"))
            self._hint = self._hint_widget()
            widgets.append(self._hint)
        await body.mount(*widgets)

    def _comment_floor(self, c: Comment) -> list[Widget]:
        """一条顶层评论的楼层部件（头部 + 内容 + 缩进的子回复）。"""
        self._floor_count += 1
        widgets = [
            divider(),
            floor_head(
                t("read.floor_head", unit=self._unit, n=self._floor_count),
                c.author,
                (c.votes, c.time),
                classes="floor-head",
            ),
            Markdown(wrap_markdown(c.content_md, self._md_width()), classes="floor-body"),
        ]
        for r in c.replies:
            widgets.extend(
                [
                    floor_head("", r.author, (r.votes, r.time), classes="floor-head sub"),
                    Markdown(
                        wrap_markdown(r.content_md, self._md_width(3)),
                        classes="floor-body sub",
                    ),
                ]
            )
        return widgets

    # ---- 评论加载（按需）----

    async def _load_comments(self) -> None:
        """此刻才请求：问题 loader 点开该回答的评论区（共享页签）。

        sub_title 在这设而非 on_mount：worker 晚于 Header 挂载，同步设
        不会踩 HeaderTitle 未挂的时序坑。
        """
        self.app.sub_title = t(
            "read.answer_title", author=self._answer.author, unit=self._floor_unit
        )[:50]
        self._comments_error = None
        try:
            self.loader = await self.qloader.open_comments(self.index)
        except Exception as exc:
            # 失败文案存下来：resize 重排也要还原成失败态，不能变回「加载中」
            self._comments_error = feedback.failure_text(self.source, exc)
            feedback.fail(self.app, self.source, exc, action="feedback.action_comments_failed")
            self._swap_status(
                "cm-loading",
                f"[red]{t('read.comments_failed_retry', text=escape(self._comments_error))}[/red]",
            )
            return
        # done 与有没有评论无关：最后一页即使非空也已到头，尾行该换 EOF 标记
        self._exhausted = self.loader.done
        for w in self.query("#cm-loading"):
            await w.remove()
        if self.loader.comments:
            await self._mount_batch(self.loader.comments)
            await self._mount_tail()
        else:
            await self._mount_empty_tail()

    async def _mount_empty_tail(self) -> None:
        """首批为空的空态尾行：「（没有X）」+ EOF 标记或重试预告。"""
        self._remove_hint()
        tail: list[Widget] = [
            dim_line(t("read.none", unit=self._unit), classes="tail-line tail-empty")
        ]
        if self._exhausted:
            tail.append(dim_line(t("read.no_more", unit=self._unit), classes="tail-line tail-eof"))
        else:
            # 首批拉空但没到头（网慢）：不判死，尾行保留 Ctrl+N 重试预告
            self._hint = self._hint_widget()
            tail.append(self._hint)
        await self._body_scroll().mount(*tail)

    async def _mount_batch(self, comments: list[Comment]) -> None:
        widgets: list[Widget] = []
        for c in comments:
            widgets.extend(self._comment_floor(c))
        await self._body_scroll().mount(*widgets)
        # 与问题屏同一取舍：新楼层接在底部，视口停在原阅读断点不跳

    def _swap_status(self, sel: str, text: str) -> None:
        for w in self.query(sel):
            if isinstance(w, Static):
                w.update(text)

    async def _retry(self) -> None:
        """评论首载失败后的原地重试（与问题屏首载重试同动线）。"""
        for w in self.query("#cm-loading"):
            if isinstance(w, Static):
                w.update(t("read.unit_loading", unit=self._unit))
        await self._load_comments()

    # ---- ←/→ 换楼层（机械件在 PagedReadingScreen._retarget）----

    async def action_next_answer(self) -> None:
        await self._retarget(1)

    async def action_prev_answer(self) -> None:
        await self._retarget(-1)

    def _next_target(self, delta: int) -> int | None:
        """同问题内相邻楼层（更多楼层回问题屏按 Ctrl+N 加载）。"""
        nxt = self.index + delta
        return nxt if 0 <= nxt < len(self.qloader.doc.answers) else None

    def _no_target_text(self, delta: int) -> str:
        return (
            t("read.no_next_loaded", unit=self._floor_unit)
            if delta > 0
            else t("read.no_prev_unit", unit=self._floor_unit)
        )

    async def _adopt_target(self, index: int) -> None:
        self.index = index
        self._comments_error = None
        await self._render_doc()
        self._render_done()
        self.run_worker(self._load_comments(), group="comments", exclusive=True)

    def _browser_url(self) -> str:
        return self._answer.url or self.item.url
