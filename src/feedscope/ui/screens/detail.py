"""问题详情屏：题目 + 描述 + 回答流，纯终端阅读。

多层递进的第二层：列表 -> 这里（回答流）-> 回答详情屏（评论层）。
Enter 进「视口正在阅读」的那个回答（读到哪进哪）；评论不随首批抓取
（首载要快），楼层头部只带评论数引导。翻页动线 / 尾行状态机 / ←/→ 换篇
的机械件在 PagedReadingScreen，本屏只剩首载编排、整屏渲染与 Enter 分派。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, Markdown, Static

from feedscope.core.errors import LoginRequired
from feedscope.core.i18n import t
from feedscope.core.loaders import DetailLoader, StaticDetailLoader
from feedscope.core.models import Answer, DetailDoc, FeedItem
from feedscope.core.source import Source
from feedscope.ui import Bindings, feedback
from feedscope.ui.markup import escape
from feedscope.ui.screens.answer import AnswerScreen
from feedscope.ui.screens.reading import (
    READING_CSS,
    PagedReadingScreen,
    dim_line,
    divider,
    floor_head,
)
from feedscope.ui.wrap import wrap_markdown

logger = getLogger(__name__)


class DetailScreen(PagedReadingScreen):
    """全屏阅读一个问题：Esc 返回（列表位置不变），Ctrl+N 加载更多回答，
    ←/→ 换篇，]/[ 换到下一篇未读（newsboat 的 next-unread：读着一篇直接跳到
    下一篇没读过的，不必退回列表）。"""

    BINDINGS: ClassVar[Bindings] = [
        # 键位全部非打印键（输入法免疫）：↑/↓ 滚动走焦点容器（VerticalScroll）
        # 原生键，无需声明；Esc 返回继承自 ReadingScreen；ctrl+n/ctrl+o/ctrl+r
        # 继承自 PagedReadingScreen
        # Enter 的标签是占位（空串）：__init__ 换源词汇表；无评论层的源隐藏该键位
        Binding("enter", "pick_answer", "", show=False),
        Binding("right", "next_article", "bindings.next", show=False),
        Binding("left", "prev_article", "bindings.prev", show=False),
        Binding("]", "next_unread", "bindings.next_unread", show=False),
        Binding("[", "prev_unread", "bindings.prev_unread", show=False),
    ]

    # Markdown 标题规则必须 CSS 级（见 reading.READING_CSS 的说明），其余
    # 共享样式走 ReadingScreen.DEFAULT_CSS
    CSS = READING_CSS + """
    .answers-header { padding: 0 2; text-style: bold; }
    """

    _worker_group = "load"  # 首载/重试/翻页同组互斥（换篇整组取消）

    def __init__(
        self,
        item: FeedItem,
        source: Source,
        step_item: Callable[[int], FeedItem | None],
        step_unread: Callable[[int], FeedItem | None] | None = None,
        source_of: Callable[[FeedItem], Source] | None = None,
    ) -> None:
        super().__init__()
        self.item = item
        self._step_item = step_item  # 向 App 要相邻的可读条目（←/→ 换篇，同步推进列表选中）
        self._step_unread = step_unread  # 相邻的未读条目（]/[ 换未读篇）；None = 供给端不支持
        # 换篇可能跨源（收藏屏 / 搜索结果的相邻条目来自不同源）：源对象要跟着
        # 条目走，否则拿旧源去开新条目。缺省 = 恒为构造时给的源（主屏单源流）
        self._source_of = source_of or (lambda _item: source)
        self.loader: DetailLoader | None = None
        self._answer_count = 0  # 回答序号，跨 load_more 递增
        self._archived = False  # 屏上显示的是收藏存档（原帖打不开时的回退），不是最新内容
        self._use_source(source)

    def _use_source(self, source: Source) -> None:
        """换源词汇表：Enter 的键位文案随源词汇表（知乎「评论」、HN「回复」）；
        无评论层的源（supports_comments=False，如 X/RSS）Footer 不预告——键位
        隐藏但保留，误按仍走 action_pick_answer 的「暂不支持」反馈，不无声
        （与尾行预告同一能力判定）。"""
        self.source = source
        self._relabel_bindings(
            {"enter": source.comment_unit},
            hide="" if source.supports_comments else "enter",
        )

    @property
    def _unit(self) -> str:
        """楼层单位词：源词汇表（知乎「回答」、X「回复」），详情屏不硬编码。"""
        return self.source.detail_unit

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

    # ---- 首载与整屏渲染 ----

    async def _load(self) -> None:
        body = self._body_scroll()
        loading = Label(t("read.loading"), id="detail-loading")
        await body.mount(loading)
        self._archived = False
        try:
            self.loader = await self.source.open_detail(self.item)
        except LoginRequired as exc:
            # 会话失效（知乎详情 403 的实测形态：列表 v3 端点照常、v4 详情回绝）：
            # 与列表屏同一动线给登录引导（收口在 AccountFlow.maybe_prompt——记
            # 失效 + 未拒绝过才弹框）；拒绝后留常驻提示（重登后 Ctrl+N 重试）
            hint = t("main.hint_login", error=exc)
            self.app.sub_title = t("main.status_signed_out")
            loading.update(f"[red]{escape(hint)}[/red]")
            await self._tui.accounts.maybe_prompt(self.source, hint)
            return
        except Exception as exc:
            archive = self._tui.state.archive_of(self.item)
            if archive is not None:
                # 收藏时存下的详情：原帖被删 / 下线 / 离线，回退显示存档（收藏即存档
                # 的意义就在这里——稍后读的条目不该随原帖一起消失）
                logger.info(
                    "原帖打不开，显示收藏存档 source=%s id=%s", self.source.source_id, self.item.id
                )
                loading.remove()
                self.loader = StaticDetailLoader(archive)
                self._archived = True
                await self._show(archive)
                return
            # notify 给细节（瞬时），屏内留一行常驻失败态：notify 会消失，屏幕不能空着
            logger.warning(
                "详情加载失败 source=%s id=%s", self.source.source_id, self.item.id, exc_info=True
            )
            feedback.fail(self.app, self.source, exc)
            self.app.sub_title = t("read.status_failed")  # 常驻失败标记（与列表屏面包屑同一手法）
            failure = t(
                "read.load_failed_retry",
                text=escape(feedback.failure_text(self.source, exc)),
            )
            loading.update(f"[red]{failure}[/red]")
            return
        loading.remove()
        await self._show(self.loader.doc)

    async def _show(self, doc: DetailDoc) -> None:
        """首载成功与回退存档共用的收尾：按完成态定格 → 渲染 → 记宽。"""
        loader = self.loader
        if loader is None:
            return
        # 首批即到头（V2EX 一页全量 / HN·Reddit 楼层不足一页）：立刻按完成态渲染，
        # 尾行给 EOF 标记而不是「Ctrl+N 加载更多」——按了也只会空跑一次
        self._exhausted = loader.done
        await self._render_doc(doc)
        self._render_done()
        self.app.sub_title = doc.title[:50]

    def _archive_doc(self) -> DetailDoc | None:
        """收藏时随存的详情：已加载的正文与楼层（存档里没有按需抓取的评论层）。"""
        return self.loader.doc if self.loader is not None else None

    async def _retry(self) -> None:
        """首载失败后的原地重试：摘掉失败行，重走一遍 _load。"""
        for w in self.query("#detail-loading"):
            await w.remove()
        await self._load()

    async def _render_doc(self, doc: DetailDoc | None = None) -> None:
        """把整个文档渲染上屏（单次 mount = 单次布局）；重排（resize）时也走这里。"""
        loader = self.loader
        if loader is None:
            return  # 未加载完成（换篇/关闭的竞态窗口）：无内容可渲染
        if doc is None:
            doc = loader.doc  # 重排路径：doc 即已加载的全部内容
        widgets: list[Widget] = [Static(escape(doc.title), id="detail-title", classes="read-title")]
        if doc.meta or doc.time:
            meta = " · ".join(x for x in (doc.meta, doc.time) if x)  # 正文发布时间并入元信息行
            widgets.append(
                Static(f"[dim]{escape(meta)}[/dim]", id="detail-meta", classes="read-meta")
            )
        if self._archived:
            # 不是最新内容：明说这是收藏那一刻的存档（原帖此刻打不开）
            widgets.append(dim_line(t("read.archived"), classes="read-meta"))
        if doc.description_md:
            widgets.append(
                Markdown(
                    wrap_markdown(doc.description_md, self._md_width()),
                    id="detail-desc",
                    classes="read-main",
                )
            )
        if doc.answers:
            # 正文与楼层之间的边界：分隔线 + 区块标题
            widgets.extend(
                [divider(), Static(f"[b]{self._unit}[/b]", classes="answers-header")]
            )
            for ans in doc.answers:
                widgets.extend(self._answer_widgets(ans))
        if loader.done and not doc.answers:
            # 天生无楼层的完整文档（RSS 全文 / Telegram 帖）：没有「更多」
            # 也没有评论层，末尾只放纯 EOF 标记「（完）」——键位预告是常驻
            # Footer 的职责，尾行复读键位会被短帖读者当成正文的一部分；
            # 置 exhausted 让 Ctrl+N 走「没有更多」反馈而不是空跑 load_more
            self._exhausted = True
            widgets.append(dim_line(t("read.eof"), classes="tail-line tail-eof"))
        elif self._exhausted:
            widgets.append(
                dim_line(t("read.no_more", unit=self._unit), classes="tail-line tail-eof")
            )
        else:
            self._hint = self._hint_widget()
            widgets.append(self._hint)
        await self._body_scroll().mount(*widgets)

    def _hint_widget(self) -> Static:
        """尾行动线预告：只留 Footer 没有的语义增量（按了会加载/进入什么）；
        纯键位复读（换篇/返回）是常驻 Footer 的职责——尾行复读会被短帖
        读者当成正文的一部分。Enter 评论段只在源有评论层时列出。"""
        segs = [t("read.hint_more", unit=self._unit)]
        if self.source.supports_comments and not self._archived:
            segs.append(t("read.hint_comments", unit=self._unit, cunit=self.source.comment_unit))
        return Static(
            f"[dim]── {escape(' · '.join(segs))} ──[/dim]", classes="tail-line tail-hint"
        )

    def _answer_widgets(self, ans: Answer) -> list[Widget]:
        """一个楼层的完整部件（分隔线 + 头部 + 正文）；楼层序号在此推进。

        头部 name 记楼层号：Enter 按可视位置定位楼层时用它取回。
        """
        self._answer_count += 1
        head = floor_head(
            t("read.floor_head", unit=self._unit, n=self._answer_count),
            ans.author,
            (ans.votes, ans.comment_count, ans.time),
            classes="floor-head",
            name=str(self._answer_count - 1),
        )
        return [
            divider(),
            head,
            Markdown(wrap_markdown(ans.content_md, self._md_width()), classes="floor-body"),
        ]

    async def _mount_batch(self, new: list[Answer]) -> None:
        widgets: list[Widget] = []
        for ans in new:
            widgets.extend(self._answer_widgets(ans))
        await self._body_scroll().mount(*widgets)  # 单次挂载 = 单次布局
        # 不主动滚动：新楼层就追加在旧内容正下方，视口停在原阅读断点不动，
        # 「衔接」靠内容连续而非视口跳转。滚动到底（scroll_end）或顶对齐
        # （scroll_to_widget top=True）都会把刚读到的楼层顶出视野、整屏跳动，
        # 逼用户再滚回去找位置——踩过的坑，别再引入。

    # ---- Enter -> 回答详情屏（多层递进的第三层入口）----

    def action_pick_answer(self) -> None:
        """Enter：进「视口正在阅读」的那个回答的评论层（读到哪进哪，评论那时才请求）。"""
        if self.loader is None or not self._rendered:
            feedback.info(self.app, t("read.still_loading"))
            return
        if self._archived:
            # 存档只存了收藏时已加载的楼层：按需抓取的评论层不在里面
            feedback.info(self.app, t("read.archive_no_comments"))
            return
        if not self.source.supports_comments:
            # 键位已隐藏，这里只剩误按兜底：给一句反馈而不是无声（X/RSS 的详情没有评论层）
            feedback.info(
                self.app,
                t(
                    "read.single_floor_unsupported",
                    label=self.source.label_text,
                    unit=self._unit,
                ),
            )
            return
        if not self.loader.doc.answers:
            feedback.info(self.app, t("read.no_floors_yet"))
            return
        self.open_answer(self._visible_answer_index())

    def _visible_answer_index(self) -> int:
        """视口顶部所在的楼层：首个底部越过视口顶的楼层。

        视口还在标题/描述区时，第一个楼层自然命中（Enter 即进回答 1，
        新手按键就有反馈，不用先滚）。
        """
        top = self._body_scroll().region.y
        for head in self.query(".floor-head"):
            if head.region.bottom > top:
                return int(head.name or 0)
        return 0

    def open_answer(self, index: int) -> None:
        """进第 index 条回答（问题屏保持在栈底，Esc 回来阅读位置不变）。"""
        loader = self.loader
        if loader is None:
            return  # 竞态窗口（换篇在途）：action 已拦，这里兜底不炸
        self.app.push_screen(AnswerScreen(self.source, self.item, loader, index))

    # ---- ←/→ 换篇（机械件在 PagedReadingScreen._retarget）----

    async def action_next_article(self) -> None:
        await self._retarget(1)

    async def action_prev_article(self) -> None:
        await self._retarget(-1)

    def _next_target(self, delta: int) -> FeedItem | None:
        return self._step_item(delta)

    def _no_target_text(self, delta: int) -> str:
        return t("read.no_next_readable") if delta > 0 else t("read.no_prev_readable")

    # ---- ]/[ 换未读篇（机械件同 _retarget，unread=True）----

    async def action_next_unread(self) -> None:
        await self._retarget(1, unread=True)

    async def action_prev_unread(self) -> None:
        await self._retarget(-1, unread=True)

    def _next_unread_target(self, delta: int) -> FeedItem | None:
        return self._step_unread(delta) if self._step_unread is not None else None

    async def _adopt_target(self, target: FeedItem) -> None:
        """收下相邻条目原地重载；列表选中已由 step_readable_item 同步推进，
        Esc 回列表时正好停在读过的那一篇。源对象跟着条目换（相邻条目可能
        来自别的源：收藏屏 / 搜索结果）。"""
        self.item = target
        self._use_source(self._source_of(target))
        self.run_worker(self._load(), group="load", exclusive=True)
