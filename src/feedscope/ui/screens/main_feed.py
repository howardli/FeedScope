"""主屏的取数上屏协作者：刷新 / 加载更多 / 整批上屏 / 按宽度重排。

从 MainScreen 抽出（仿 ui/account.AccountFlow 的「一个宿主、一份动线」）：屏只留
「何时触发」（键位、切站切流、resize 消息），「怎么取、怎么画」在这里。

状态归属（主屏四层继承链 + 两个协作者，各管一摊，见 ui/screens/__init__.py）：
- 本对象独占取数族的渲染状态：当前流 loader（持有活页签，切流/退出前要 close）、
  本批「新」计数、三列宽度（元信息 / 源名 / 时间）
- 行集的原料 items 与过滤态归 MainReadScreen（行集是它的职责）；当前位置归
  MainSetupScreen；两侧导航的重建与边栏刷新仍是 MainScreen 的渲染钩子
- 本对象经 screen 访问以上各件，自己不持有第二份行集或位置
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.widgets import ListItem, ListView

from feedscope.core import feed_cache
from feedscope.core.errors import LoginRequired
from feedscope.core.i18n import t
from feedscope.core.loaders import FeedLoader
from feedscope.core.models import FeedItem
from feedscope.core.source import Source
from feedscope.ui import feedback
from feedscope.ui.layout import (
    META_COL_SPARE,
    list_title_width,
    score_col_width,
    tag_col_width,
    time_col_width,
)
from feedscope.ui.list_render import build_item

if TYPE_CHECKING:  # 仅注解用：运行时 main.py 导入本模块
    from feedscope.ui.screens.main import MainScreen

logger = logging.getLogger(__name__)


class FeedFlow:
    """主屏的取数上屏协作者（一个屏一份；由 MainSetupScreen.__init__ 创建）。"""

    def __init__(self, screen: MainScreen) -> None:
        self.screen = screen
        self.loader: FeedLoader | None = None  # 持有活页签，切流/退出前要 close
        # 本批「新抓到」的条目数（note_fetched 返回集的长度）：只喂顶部状态行
        # 的「N 新」计数。行首圆点不看它——未读是持久态，直到打开才消失
        # （传统阅读器语义；「新」是瞬态信息，归状态行）
        self.new_count = 0
        # 元信息列宽（随当前批内容自适应，见 score_col_width；首渲染前是占位值）
        self.score_width = 1 + META_COL_SPARE
        self.tag_width = 0  # 源名列宽（聚合流标订阅/频道名；单源流 0 不占列）
        self.time_width = 0  # 时间列宽（批内有 published 才成列；无时间的源 0）

    def _view(self) -> ListView:
        return self.screen.query_one("#feed", ListView)

    async def release_loader(self) -> None:
        """关掉当前流 loader 持有的活页签并复位（切流/登录前让出资源）。"""
        if self.loader is not None:
            await self.loader.close()
            self.loader = None

    # ---- 刷新（当前流的首批：缓存先画 → 抓取定稿）----

    async def refresh(self) -> None:
        """刷新当前流：关掉旧 loader → 缓存先画（离线兜底）→ 抓首批定稿上屏。

        未登录走账号动线（弹登录框）、其他失败保留缓存行并报错，都不清屏。"""
        await self.release_loader()  # 切流前关掉旧页签（await 完成再继续）
        if not self.screen.sources:  # 全部停用：常驻空态指路（F2 再开），不退出
            view = self._view()
            view.clear()
            self.screen.items = []
            self.screen.app.sub_title = ""
            self.screen.show_empty(t("main.empty_no_sources"))
            return
        source = self.screen.current_source

        open_coro = source.open_feed(self.screen.current_feed)

        # Header 副标题 = 状态行（站点/信息流位置由 tab 条与边栏承担）；纯文本
        # 不写 markup（Header 的 Content 解析行为不确定）
        self.screen.app.sub_title = t("read.loading")
        view = self._view()
        view.clear()
        self.screen.items = []
        # 离线兜底先画缓存：重启秒开、断网可翻（详情自带正文的 RSS/Telegram
        # 离线也能读）；刷新成功整批替换，失败/未登录保留缓存不清屏。缓存
        # 内容在抓取时已登记过已见，不是「新」——快照清空
        cached = feed_cache.load(source.source_id, self.screen.current_feed)
        if cached:
            self.new_count = 0
            await self.paint(cached, source, t("main.status_cached", n=len(cached)))
        try:
            loader = await open_coro
        except LoginRequired as exc:
            if not self.screen.items:
                self.screen.app.sub_title = t("main.status_signed_out")
            hint = t("main.hint_login", error=exc)
            if not self.screen.items:
                self.screen.show_empty(str(exc))  # 先按原始消息占位（弹框期间屏不空）
            # 撞墙即给入口：记失效 + 未拒绝过才弹框（动线收口在 AccountFlow；
            # 拒绝过的留常驻提示——hint 带登录入口，不再被追问打断）
            prompted = await self.screen._tui.accounts.maybe_prompt(source, hint)
            if not prompted and not self.screen.items:
                self.screen.show_empty(hint)
            return
        except Exception as exc:
            # 缓存行保留（有内容时不清屏不亮空态）：断网/改版时旧内容仍可翻
            logger.warning(
                "刷新失败 source=%s feed=%s", source.source_id, self.screen.current_feed,
                exc_info=True,
            )
            self.screen.app.sub_title = t("read.status_failed")
            feedback.fail(self.screen, source, exc)
            if not self.screen.items:
                self.screen.show_empty(
                    t("main.empty_load_failed", text=feedback.failure_text(source, exc))
                )
            return

        self.loader = loader
        # 先定格「新」计数（渲染不依赖它，但状态行要）+ 整流重数未读角标，再重写缓存
        state = self.screen._tui.state
        self.new_count = len(
            state.note_fetched(loader.items, self.screen.current_feed, recount=True)
        )
        feed_cache.save(source.source_id, self.screen.current_feed, loader.items)
        # 状态行走 count_text（含新计数）；这是抓取成功后的定稿上屏（settle）
        await self.paint(loader.items, source, settle=True)
        self.screen._refresh_sidebar()

    # ---- 整批上屏 ----

    async def paint(
        self, items: list[FeedItem], source: Source, sub: str | None = None, *, settle: bool = False
    ) -> None:
        """整批渲染列表行（缓存先画与刷新成功共用一条路径）：清-置-画-选中-收空态。

        sub 缺省在置完行集原料后自算（count_text 读它）——调用方传进来的实参
        会在赋值前求值，缓存态的「刷新中」文案走显式入参。行画的是行集
        _shown()（F4 过滤态 = 未读子集），全量原料 items 不动。

        settle = 抓取成功后的定稿上屏：续读目标在这一轮用掉（见 ResumeTracker.index_in）；
        缓存先画那一轮不算数——缓存可能比定稿旧，目标条目要等定稿才找得到。
        """
        view = self._view()
        view.clear()
        self.screen.items = items
        self.score_width = score_col_width(source, self.screen.items)  # 列宽按本批最长元信息定
        self.tag_width = tag_col_width(self.screen.items)  # 聚合流的源名列（单源流 0）
        self.time_width = time_col_width(self.screen.items)  # 相对时间列（无时间批 0）
        shown = self.screen._shown()
        await view.extend(self.build_rows(shown, source))  # 单次挂载 = 单次布局
        if shown:
            # 续读位置：上次停下的那条还在行集里就选中它；没有目标 / 已不在 = 第一条
            view.index = self.screen.resume.index_in(shown, settle=settle) or 0
            self.screen._hide_empty()
        elif self.screen.items:
            self.screen.show_empty(self.screen._empty_hint())  # 有数据但被过滤光：指路去关过滤
        else:
            self.screen.show_empty(t("main.empty_no_content"))
        self.screen.app.sub_title = sub if sub is not None else self.count_text()

    def count_text(self) -> str:
        """状态行计数文案：本批有「新」带新计数（刷新与 Ctrl+N 共用）。"""
        n = len(self.screen._shown())
        return (
            t("main.status_with_new", n=n, new=self.new_count)
            if self.new_count
            else t("main.status_count", n=n)
        )

    # ---- Ctrl+N：增量加载 ----

    async def load_more(self) -> None:
        """Ctrl+N：沿当前 loader 取下一批接在列表后面；列宽变了或处在过滤态就整表重排。"""

        loader = self.loader
        if loader is None:
            return
        state = self.screen._tui.state
        # 列表屏的加载更多没有独立加载行：进行中/结果全部落进常驻状态行
        # （Cloudflare 顶跳最长能到数十秒，没有进行中提示会像按键失灵）
        self.screen.app.sub_title = t("main.status_more_loading", n=len(self.screen.items))
        try:
            new = await loader.load_more()
        except Exception as exc:
            logger.warning(
                "加载更多失败 source=%s feed=%s",
                self.screen.current_source.source_id, self.screen.current_feed,
                exc_info=True,
            )
            self.screen.app.sub_title = t("main.status_more_failed", n=len(self.screen.items))
            if isinstance(exc, LoginRequired):
                self.screen._tui.accounts.mark_stale(self.screen.current_source)  # 会话失效记账
            feedback.fail(
                self.screen,
                self.screen.current_source,
                exc,
                action="feedback.action_more_failed",
            )
            return
        if loader is not self.loader:
            return  # 期间切流/刷新：旧 loader 的迟到结果不能混进新列表
        if not new:
            if loader.done:
                self.screen.app.sub_title = t("main.status_no_more", n=len(self.screen.items))
                feedback.info(self.screen, t("main.toast_no_more"))
            else:
                self.screen.app.sub_title = t("main.status_no_new", n=len(self.screen.items))
                feedback.info(self.screen, t("main.toast_no_new_retry"))
            return
        self.screen.items = loader.items
        # 新批次的首见数累进「新」计数与角标（不重数整流——批是局部的），缓存重写；
        # store 计数变了但部件不会自己动——边栏角标要跟着刷新（漏了这里会停在旧值）
        self.new_count += len(state.note_fetched(new, self.screen.current_feed))
        feed_cache.save(
            self.screen.current_source.source_id, self.screen.current_feed, self.screen.items
        )
        self.screen._refresh_sidebar()
        view = self._view()
        new_width = score_col_width(self.screen.current_source, self.screen.items)
        new_tag = tag_col_width(self.screen.items)
        new_time = time_col_width(self.screen.items)
        if (
            new_width > self.score_width
            or new_tag > self.tag_width
            or new_time > self.time_width
            or self.screen._unread_only
            or self.screen._title_filter is not None
        ):
            # 新批次带来更长的元信息/源名/成列时间（或过滤态下行集变化）：
            # 列变宽或行集重算——全量重建，不只是追加新行。F4 与 / 都在：
            # 行集 = 全量与新过滤词的 AND，直接 extend 未过滤行会让行与
            # current_item()/_row_mark() 按 _shown() 的寻址从此错位
            self.score_width = new_width
            self.tag_width = new_tag
            self.time_width = new_time
            await self.rebuild_rows()
        else:
            rows = self.build_rows(new, self.screen.current_source)
            await view.extend(rows)  # 单次挂载 = 单次布局
        self.screen._hide_empty()
        self.screen.app.sub_title = self.count_text()

    # ---- 列表项构建与折行 ----

    def build_rows(self, items: list[FeedItem], source: Source) -> list[ListItem]:
        """条目批 -> 行部件批：列宽三件套（元信息/源名/时间）与折行宽取当前态，
        首画 / 增量追加 / 过滤重建三条路同一副参数列（漂移即行与列错位）。"""
        width = self.wrap_width()
        return [
            build_item(
                it,
                width,
                self.score_width,
                source,
                self.screen._row_mark(it),
                tag=it.tag,
                tag_width=self.tag_width,
                time_width=self.time_width,
            )
            for it in items
        ]

    def wrap_width(self) -> int:
        """标题可用宽：折行算术与布局常量的对应关系集中在 ui/layout.py。"""
        return list_title_width(
            self._view().region.width,
            self.score_width,
            self.tag_width,
            self.time_width,
        )

    # ---- 按当前宽度 / 过滤态重排 ----

    async def rebuild_rows(self) -> None:
        """按当前宽度/过滤态重排列表（只重排版，不重新抓取；行集 = _shown）。"""
        if not self.screen.items:
            return
        view = self._view()
        idx = view.index
        view.clear()
        shown = self.screen._shown()
        await view.extend(self.build_rows(shown, self.screen.current_source))
        if shown:
            view.index = 0 if idx is None else min(idx, len(shown) - 1)
            self.screen._hide_empty()
        else:
            view.index = None
            self.screen.show_empty(self.screen._empty_hint())
        self.screen.app.sub_title = self.count_text()
