"""问题详情屏：题目 + 描述 + 回答流，纯终端阅读（M1 核心）。

多层递进的第二层：列表 -> 这里（回答流）-> 回答详情屏（评论层）。
Enter 进「视口正在阅读」的那个回答（读到哪进哪）；评论不随首批抓取
（首载要快），楼层头部只带评论数引导。与回答屏共享的阅读屏机械件
（resize 重排 / hint 生命周期 / 清理）在 ReadingScreen。
"""

from __future__ import annotations

import dataclasses
import webbrowser
from collections.abc import Callable
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, Markdown, Static

from feedscope.core.models import Answer, DetailDoc, FeedItem
from feedscope.core.source import DetailLoader, Source
from feedscope.ui import feedback
from feedscope.ui.layout import md_body_width
from feedscope.ui.screens.answer import AnswerScreen
from feedscope.ui.screens.reading import READING_CSS, ReadingScreen, dim_line, divider
from feedscope.ui.wrap import wrap_markdown


class DetailScreen(ReadingScreen):
    """全屏阅读一个问题：Esc 返回（列表位置不变），Ctrl+N 加载更多回答，[/] 换篇。"""

    BINDINGS: ClassVar[list[Binding]] = [
        # 键位全部非打印键（输入法免疫）：↑/↓ 滚动走焦点容器（VerticalScroll）
        # 原生键，无需声明；Esc 返回继承自 ReadingScreen
        Binding("ctrl+n", "more", "更多"),
        # Enter 的标签是占位：__init__ 换源词汇表；无评论层的源隐藏该键位
        Binding("enter", "pick_answer", "评论"),
        Binding("ctrl+o", "open_browser", "浏览器"),
        Binding("right", "next_article", "下一篇"),
        Binding("left", "prev_article", "上一篇"),
    ]

    CSS = READING_CSS + """
    #detail-title { text-style: bold; padding: 1 2; border: ascii; width: 1fr; text-wrap: wrap; }
    #detail-meta { padding: 0 2; }
    #detail-desc { padding: 0 2; }
    .answers-header { padding: 0 2; text-style: bold; }
    .answer-head { padding: 1 2 0 2; text-wrap: wrap; }  /* 窄终端长楼层头折行不裁剪 */
    .answer-body { padding: 0 2; }
    #more-hint, #more-loading, #no-more { padding: 1; }
    """

    @property
    def _unit(self) -> str:
        """楼层单位词：源词汇表（知乎「回答」、X「回复」），详情屏不硬编码。"""
        return self.source.detail_unit_label

    def __init__(
        self,
        item: FeedItem,
        source: Source,
        step_item: Callable[[int], FeedItem | None],
    ) -> None:
        super().__init__()
        self.item = item
        self.source = source
        # Enter 的键位文案随源词汇表（知乎「评论」、HN「回复」）；无评论层的源
        # （supports_comments=False，如 X/RSS）Footer 不预告——键位隐藏但保留，
        # 误按仍走 action_pick_answer 的「暂不支持」反馈，不无声（与尾行预告
        # 同一能力判定）。
        self._bindings.key_to_bindings["enter"] = [
            dataclasses.replace(
                b, description=source.comment_unit_label, show=b.show and source.supports_comments
            )
            for b in self._bindings.key_to_bindings.get("enter", [])
        ]
        self._step_item = step_item  # 向 App 要相邻的可读条目（[/] 换篇，同步推进列表选中）
        self.loader: DetailLoader | None = None
        self._exhausted = False       # 已到回答末尾，n 不再触发
        self._answer_count = 0        # 回答序号，跨 load_more 递增

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="detail-body")
        yield Footer()

    def _body_scroll(self) -> VerticalScroll:
        return self.query_one("#detail-body", VerticalScroll)

    async def _mount_body(self) -> None:
        self._body_scroll().focus()
        # 组内互斥：换篇/重试可打断在途首载
        self.run_worker(self._load(), group="load", exclusive=True)

    def _reset_render_state(self) -> None:
        self._answer_count = 0

    async def _close_loader(self) -> None:
        loader, self.loader = self.loader, None
        if loader is not None:
            await loader.close()

    # ---- 加载与渲染 ----

    async def _load(self) -> None:
        body = self._body_scroll()
        loading = Label("加载中…", id="detail-loading")
        await body.mount(loading)
        try:
            self.loader = await self.source.open_detail(self.item)
        except Exception as exc:
            # notify 给细节（瞬时），屏内留一行常驻失败态：notify 会消失，屏幕不能空着
            feedback.fail(self.app, self.source, exc)
            self.app.sub_title = "加载失败"  # 状态行常驻标记失败（与列表屏面包屑同一手法）
            loading.update(
                f"[red]加载失败：{escape(feedback.failure_text(self.source, exc))}"
                " · Ctrl+N 重试[/red]"
            )
            return
        loading.remove()
        await self._render_doc(self.loader.doc)
        self._rendered = True
        self.app.sub_title = self.loader.doc.title[:50]

    def _md_width(self) -> int:
        """正文折行宽：折行算术与 CSS 常量的对应关系集中在 ui/layout.py。"""
        return md_body_width(self._body_scroll().region.width)

    async def _render_doc(self, doc: DetailDoc | None = None) -> None:
        """把整个文档渲染上屏（单次 mount = 单次布局）；重排（resize）时也走这里。"""
        if doc is None:
            doc = self.loader.doc  # 重排路径：doc 即已加载的全部内容
        widgets: list[Widget] = [Static(escape(doc.title), id="detail-title")]
        if doc.meta or doc.time:
            meta = " · ".join(x for x in (doc.meta, doc.time) if x)  # 正文发布时间并入元信息行
            widgets.append(Static(f"[dim]{escape(meta)}[/dim]", id="detail-meta"))
        if doc.description_md:
            widgets.append(
                Markdown(
                    wrap_markdown(doc.description_md, self._md_width()),
                    id="detail-desc",
                )
            )
        if doc.answers:
            # 正文与楼层之间的边界：分隔线 + 区块标题
            widgets.extend([divider(), Static(f"[b]{self._unit}[/b]", classes="answers-header")])
            for ans in doc.answers:
                widgets.extend(self._answer_widgets(ans))
        if self.loader.done and not doc.answers:
            # 天生无楼层的完整文档（RSS 全文 / Telegram 帖）：没有「更多」
            # 也没有评论层，末尾只放纯 EOF 标记「（完）」——键位预告是常驻
            # Footer 的职责，尾行复读键位会被短帖读者当成正文的一部分；
            # 置 exhausted 让 Ctrl+N 走「没有更多」反馈而不是空跑 load_more
            self._exhausted = True
            widgets.append(dim_line("（完）", id="no-more"))
        elif self._exhausted:
            widgets.append(dim_line(f"（没有更多{self._unit}了）", id="no-more"))
        else:
            self._hint = self._hint_widget()
            widgets.append(self._hint)
        await self._body_scroll().mount(*widgets)

    def _hint_widget(self) -> Static:
        """尾行动线预告：只留 Footer 没有的语义增量（按了会加载/进入什么）；
        纯键位复读（换篇/返回）是常驻 Footer 的职责——尾行复读会被短帖
        读者当成正文的一部分。Enter 评论段只在源有评论层时列出。"""
        segs = [f"Ctrl+N 加载更多{self._unit}"]
        if self.source.supports_comments:
            segs.append(f"Enter 看{self._unit}的{self.source.comment_unit_label}")
        return Static(f"[dim]── {' · '.join(segs)} ──[/dim]", id="more-hint")

    def _answer_widgets(self, ans: Answer) -> list[Widget]:
        """一个楼层的完整部件（分隔线 + 头部 + 正文）；楼层序号在此推进。

        头部 name 记楼层号：Enter 按可视位置定位楼层时用它取回。
        """
        self._answer_count += 1
        parts = [f"[b]{self._unit} {self._answer_count}[/b]", f"[b]{escape(ans.author)}[/b]"]
        if ans.votes:
            parts.append(f"[dim]{escape(ans.votes)}[/dim]")
        if ans.comment_count:
            parts.append(f"[dim]{escape(ans.comment_count)}[/dim]")
        if ans.time:
            parts.append(f"[dim]{escape(ans.time)}[/dim]")
        head = Static("  ".join(parts), classes="answer-head", name=str(self._answer_count - 1))
        return [
            divider(),
            head,
            Markdown(wrap_markdown(ans.content_md, self._md_width()), classes="answer-body"),
        ]

    # ---- Enter -> 回答详情屏（多层递进的第三层入口）----

    def action_pick_answer(self) -> None:
        """Enter：进「视口正在阅读」的那个回答的评论层（读到哪进哪，评论那时才请求）。"""
        if self.loader is None or not self._rendered:
            feedback.info(self.app, "内容还在加载")
            return
        if not self.source.supports_comments:
            # 键位已隐藏，这里只剩误按兜底：给一句反馈而不是无声（X/RSS 的详情没有评论层）
            feedback.info(self.app, f"{self.source.label} 暂不支持进入单条{self._unit}")
            return
        if not self.loader.doc.answers:
            feedback.info(self.app, "还没有可进入的回答")
            return
        self.open_answer(self._visible_answer_index())

    def _visible_answer_index(self) -> int:
        """视口顶部所在的楼层：首个底部越过视口顶的楼层。

        视口还在标题/描述区时，第一个楼层自然命中（Enter 即进回答 1，
        新手按键就有反馈，不用先滚）。
        """
        top = self._body_scroll().region.y
        for head in self.query(".answer-head"):
            if head.region.bottom > top:
                return int(head.name)
        return 0

    def open_answer(self, index: int) -> None:
        """进第 index 条回答（问题屏保持在栈底，Esc 回来阅读位置不变）。"""
        self.app.push_screen(AnswerScreen(self.source, self.item, self.loader, index))

    # ---- 键位 ----

    def action_open_browser(self) -> None:
        webbrowser.open(self.item.url)

    def action_more(self) -> None:
        if self.loader is None:
            # 首载失败（或还在途）时 Ctrl+N = 原地重试：比起 Esc 退出再进，动线不断
            self.run_worker(self._retry(), group="load", exclusive=True)
            return
        if self._exhausted:
            feedback.info(self.app, f"没有更多{self._unit}了")  # 常驻行之外再给瞬时反馈：按键不无声
            return
        self.run_worker(self._load_more(), group="more", exclusive=True)

    async def _retry(self) -> None:
        """首载失败后的原地重试：摘掉失败行，重走一遍 _load。"""
        for w in self.query("#detail-loading"):
            await w.remove()
        await self._load()

    async def action_next_article(self) -> None:
        await self._retarget(1)

    async def action_prev_article(self) -> None:
        await self._retarget(-1)

    async def _retarget(self, delta: int) -> None:
        """[/] 换篇：向列表要相邻的可读条目，原地重载（不换屏、不闪黑）。

        列表选中已由 step_readable_item 同步推进，Esc 回列表时正好停在读过的
        那一篇；沿方向没有可读条目时不动任何状态，只给反馈。
        """
        item = self._step_item(delta)
        if item is None:
            feedback.info(self.app, "后面没有可读的了" if delta > 0 else "前面没有可读的了")
            return
        self.workers.cancel_group(self, "load")  # 首载还在途：丢弃（页签由 run_page 取消兜底关闭）
        loader, self.loader = self.loader, None
        if loader is not None:
            await loader.close()
        self.item = item
        self._exhausted = False
        self._reset_render_state()
        self._rendered = False
        self._remove_hint()
        body = self._body_scroll()
        await body.remove_children()
        self.run_worker(self._load(), group="load", exclusive=True)

    async def _load_more(self) -> None:
        body = self._body_scroll()
        self._remove_hint()
        loading = Label("[dim]加载更多…[/dim]", id="more-loading")
        await body.mount(loading)
        try:
            new = await self.loader.load_more()
        except Exception as exc:
            # 不再静默：失败要告诉用户（否则按 Ctrl+N 无反馈，误以为按键失灵）
            feedback.fail(self.app, self.source, exc, action="加载更多失败")
            new = []
        if loading.parent is not None:  # 期间可能整屏重排过，loading 已被清掉
            loading.remove()
        if not new:
            if self.loader.done:
                self._exhausted = True
                self._remove_hint()
                await body.mount(dim_line(f"（没有更多{self._unit}了）", id="no-more"))
            else:
                # 拉空但没到头（网慢/懒加载没触发）：不判死，可以再按 Ctrl+N
                await self._mount_hint()
            return
        widgets: list[Widget] = []
        for ans in new:
            widgets.extend(self._answer_widgets(ans))
        await body.mount(*widgets)  # 单次挂载 = 单次布局
        await self._mount_hint()
        # 不主动滚动：新楼层就追加在旧内容正下方，视口停在原阅读断点不动，
        # 「衔接」靠内容连续而非视口跳转。滚动到底（scroll_end）或顶对齐
        # （scroll_to_widget top=True）都会把刚读到的楼层顶出视野、整屏跳动，
        # 逼用户再滚回去找位置——踩过的坑，别再引入。
