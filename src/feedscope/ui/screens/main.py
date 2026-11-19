"""主屏（默认屏）：顶部站点 tab 条 + 左侧信息流边栏 + 单栏列表。

继承链四层（各层只留自己的职责，纯搬家零行为变化）：
MainSetupScreen（源启用编排）→ MainNavScreen（导航与切站切流）→
MainReadScreen（行集与已读/过滤）→ MainScreen（本模块：取数上屏、
边栏渲染、Enter 动线与杂项转发动作）。从 app.py 下沉为主屏后，键位
归属与守卫关系自然理顺：主屏专属键绑在屏级——弹层与详情屏在前台时
这些绑定根本不命中；App 只留全局退出与生命周期（见 app.py）。主屏
状态也都在这条链上，外部（AccountFlow 等）经 app.main 访问。
"""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import TYPE_CHECKING, ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListView, Static

from feedscope.core import config, feed_cache
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, LoginRequired, Source
from feedscope.core.text import is_cjk as _is_cjk
from feedscope.ui import Bindings, feedback
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.layout import (
    META_COL_SPARE,
    list_title_width,
    score_col_width,
    tag_col_width,
    time_col_width,
)
from feedscope.ui.list_render import LIST_ROW_CSS, build_item
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.help import HelpScreen, global_rows, group_rows, screen_rows
from feedscope.ui.screens.main_read import MainReadScreen
from feedscope.ui.widgets import (
    FeedListView,
    FeedNavItem,
    FeedNavPressed,
    SourceTab,
    SourceTabPressed,
)

_CJK_HINT_INTERVAL = 30.0  # 中文输入指路提示的节流间隔：连打中文只提示一次，不刷屏

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass  # 运行时导入会与 app.py 成环


class MainScreen(MainReadScreen):
    """站点 tab 条 + 信息流边栏 + 条目列表；取数上屏与边栏渲染。

    继承链：MainSetupScreen（源启用编排，_tui 收窄属性也在这层）→
    MainNavScreen（导航）→ MainReadScreen（行集与已读/过滤）→ 本类。
    本类只留渲染、取数与 Enter/账号/收藏等转发动作。
    """

    # 导航键位（方向键六件）声明在 MainNavScreen、已读/过滤键位在
    # MainReadScreen——Textual 沿 MRO 合并，帮助页沿 MRO 收集，不因分层丢失。
    # 页脚只亮 F1（帮助入口自身必须可见可发现，其余键全在 F1/README）：
    # 日常键绑定照常命中，提示退入帮助页——换来每屏一行正文与干净的视觉。
    # show=False 只是不占页脚，不影响命中
    BINDINGS: ClassVar[Bindings] = [
        Binding("f1", "help", "bindings.help"),
        Binding("f5", "refresh", "bindings.refresh", show=False),
        Binding("ctrl+o", "open_browser", "bindings.browser", show=False),
        Binding("f2", "manage_sources", "bindings.manage_sources", show=False),
        Binding("ctrl+u", "account", "bindings.account", show=False),
        Binding("ctrl+n", "more", "bindings.more", show=False),
        Binding("ctrl+f", "favorite", "bindings.favorite", show=False),
        Binding("f3", "favorites", "bindings.favorites", show=False),
        # Ctrl+Y 复制当前条目链接（vim 的 yank 惯例），提示在 F1/README
        Binding("ctrl+y", "copy_link", "bindings.copy", show=False),
        # description 存文案键、挂载时解析（见 ui/labels.py）：类体求值时语言未定
        Binding("ctrl+l", "language", "bindings.language", show=False),
        Binding("ctrl+t", "theme", "bindings.theme", show=False),
    ]

    # 用 DEFAULT_CSS 而非 CSS：Textual 8.x 的默认屏创建路径不装载 Screen.CSS
    # （只有 push/switch 路径装载），自定义默认屏的 CSS 会被静默丢弃；DEFAULT_CSS
    # 随挂件类注册、不依赖装载路径。本屏选择器与 App/其他屏零冲突，低优先级无害。
    # 列表行的样式与收藏屏共享（见 list_render.LIST_ROW_CSS），这里只留本屏私有
    DEFAULT_CSS = LIST_ROW_CSS + """
    #feed.pane-aside > ListItem.-highlight .item-mark { color: $primary; }

    /* 顶部站点 tab 条：active 仿编辑器打开中的文件 tab（加粗下划线），其余降灰 */
    #source-tabs { height: 1; background: $surface; padding: 0 1; }
    .source-tab { width: auto; padding: 0 1; color: $text-muted; }
    .source-tab-active { color: $text; text-style: bold underline; }

    /* 左侧信息流边栏：选中与焦点分离——未聚焦的选中态是灰带（VS Code 同款：
    未聚焦选中为灰、蓝只留给焦点，$boost=#37373d 悬浮灰），聚焦态见 pane-focus */
    #main { height: 1fr; }
    #feed-nav { width: 16; height: 1fr; background: $surface; padding: 0 1; }
    /* 撑满列宽：选中带是整行（Static 默认 auto 只铺到文字宽） */
    .feed-nav-item { height: 1; width: 1fr; }
    .feed-nav-active { background: $nav-sel-background; color: $text; text-style: bold; }

    /* 全屏方向键导航的聚焦区域（pane-focus 挂容器，见 _enter_pane）：不给整
       个区域铺色块（克制主题，层级靠灰阶）——聚焦只把该区域的选中项升满编
       辑器蓝，与列表光标同语言；全屏任何时刻蓝色标记只有聚焦这一处 */
    #source-tabs.pane-focus .source-tab-active { background: $primary; }
    #feed-nav.pane-focus .feed-nav-active { background: $primary; }

    /* 非列表态的列表（pane-aside）：焦点标记只在聚焦区域亮一处——列表光标
       整体隐去（选中位仍记在 index 上，回列表即恢复原位），不留第二个记号 */
    #feed.pane-aside { background-tint: $foreground 0%; }
    #feed.pane-aside > ListItem.-highlight {
        background: transparent;
        color: $foreground;
        text-style: none;
    }

    #empty-hint { display: none; padding: 1 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.sources = self._enabled_sources()
        self.source_idx = 0
        self.feed_idx = 0
        # 区域焦点 _pane 与 _feed_memory 在 MainNavScreen.__init__ 初始化（导航族状态）
        self.items: list[FeedItem] = []
        self.feed_loader: FeedLoader | None = None  # 持有活页签，切流/退出前要 close
        # 本批「新抓到」的条目数（note_fetched 返回集的长度）：只喂顶部状态行
        # 的「N 新」计数。行首圆点不看它——未读是持久态，直到打开才消失
        # （传统阅读器语义；「新」是瞬态信息，归状态行）
        self._new_count = 0
        # 过滤态（_unread_only/_title_filter）在 MainReadScreen.__init__ 初始化
        # 边栏刷新的合并式调度状态（见 _refresh_sidebar）：在途 + 期间有人再要
        self._sidebar_busy = False
        self._sidebar_dirty = False
        # 元信息列宽（随当前批内容自适应，见 score_col_width；首渲染前是占位值）
        self._score_width = 1 + META_COL_SPARE
        self._tag_width = 0  # 源名列宽（聚合流标订阅/频道名；单源流 0 不占列）
        self._time_width = 0  # 时间列宽（批内有 published 才成列；无时间的源 0）
        self._subs_dirty = False  # 订阅管理屏改过信息流（F2 收尾时消费，见 _manage_sources）
        self._cjk_hint_at = 0.0  # 上次「输入法没切英文」提示的时刻（on_key 节流用）

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(id="source-tabs")  # 顶部站点 tab 条（子项由 _render_source_tabs 挂载）
        with Horizontal(id="main"):
            yield Vertical(id="feed-nav")  # 左侧信息流边栏（子项由 _render_feed_sidebar 挂载）
            yield FeedListView(id="feed")
        yield Label("", id="empty-hint")  # 空态/失败态常驻提示
        yield Footer()

    async def on_mount(self) -> None:
        # 键式键位文案解析（bindings.*，见 ui/labels.py）：主屏自己的键 + 列表
        # 控件的键——tab/shift+tab 切站声明在 FeedListView 上（压过 Screen 焦点
        # 切换），列表是页脚的数据源之一，控件不翻译页脚就露原始键名
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#feed", FeedListView))
        self.query_one("#feed", ListView).focus()
        self.run_worker(self._start())  # 启动序列（首启引导 + 初始渲染）在 MainSetupMixin

    # ---- 顶部 tab 条 / 左侧信息流边栏（点击与键盘共用同一条动作路径）----

    async def _render_source_tabs(self) -> None:
        """按注册表重建顶部站点 tab 条，高亮当前源。

        重建必须先等旧子项摘除（remove_children 的删除是异步落地的），否则
        新子项带着重复 id 挂载会触发 DuplicateIds。
        """
        tabs = self.query_one("#source-tabs", Horizontal)
        await tabs.remove_children()
        for idx, source in enumerate(self.sources):
            await tabs.mount(SourceTab(idx, source.label_text, active=(idx == self.source_idx)))

    async def _render_feed_sidebar(self) -> None:
        """按当前源重建左侧信息流边栏（含未读角标），高亮当前流（←/→ 依序切换）。"""
        nav = self.query_one("#feed-nav", Vertical)
        await nav.remove_children()
        items: list[Widget] = []
        if self.sources:
            source = self.current_source
            items = [
                FeedNavItem(
                    idx,
                    source.feed_label(name),
                    active=(idx == self.feed_idx),
                    unread=self._tui.state.unread_count(source.source_id, name),
                )
                for idx, name in enumerate(source.feeds)
            ]
        if not items:
            hint = (
                t("main.sidebar_no_sources") if not self.sources else t("main.sidebar_no_feeds")
            )
            items.append(Static(f"[dim]{hint}[/dim]"))
        await nav.mount(*items)

    def _refresh_sidebar(self) -> None:
        """边栏角标随未读数变化重建（抓取后/读到一条/清仓后调用；几项重建无压力）。

        合并式调度：在途时只标脏不并跑——remove_children 的删除异步落地，
        两个重建交错挂载会撞 DuplicateIds（真机快操作可复现）；跑完那份见
        脏标志自动再来一轮，最新计数最终生效。exclusive 取消也能防并发，
        但被取代的协程从未启动会留 never-awaited 警告。收尾期不排程同理。
        """
        if not self.is_running:
            return
        if self._sidebar_busy:
            self._sidebar_dirty = True
            return
        self._sidebar_busy = True
        self.run_worker(self._sidebar_cycle(), group="sidebar")

    async def _sidebar_cycle(self) -> None:
        try:
            while True:
                await self._render_feed_sidebar()
                if not self._sidebar_dirty:
                    return
                self._sidebar_dirty = False  # 在途期间又有人要刷新：再来一轮
        finally:
            self._sidebar_busy = False

    async def on_source_tab_pressed(self, event: SourceTabPressed) -> None:
        await self._goto_source(event.idx)

    async def on_feed_nav_pressed(self, event: FeedNavPressed) -> None:
        await self.action_select_feed(event.idx)

    # ---- 数据加载（当前源/流属性与切站回停在 MainNavScreen）----

    def action_refresh(self) -> None:
        if self._tui.accounts.in_progress:
            feedback.info(
                self, t("account.login_refresh_later")
            )  # 有头登录独占 profile，此时别碰浏览器
            return
        # 详情屏在前台时不做隐形后台刷新（会顶掉详情标题、重开隐藏列表的页签）
        if not self.is_current:
            return
        # 先停在途的增量加载：旧 loader 的迟到写入会把条目混进新列表（索引错位）
        self.workers.cancel_group(self, "more")
        self.run_worker(self._refresh(), exclusive=True)

    async def release_feed_loader(self) -> None:
        """关掉当前流 loader 持有的活页签并复位（切流/登录前让出资源）。"""
        if self.feed_loader is not None:
            await self.feed_loader.close()
            self.feed_loader = None

    async def _refresh(self) -> None:
        await self.release_feed_loader()  # 切流前关掉旧页签（await 完成再继续）
        if not self.sources:  # 全部停用：常驻空态指路（F2 再开），不退出
            view = self.query_one("#feed", ListView)
            view.clear()
            self.items = []
            self.app.sub_title = ""
            self.show_empty(t("main.empty_no_sources"))
            return
        source = self.current_source

        open_coro = source.open_feed(self.current_feed)

        # Header 副标题 = 状态行（站点/信息流位置由 tab 条与边栏承担）；纯文本
        # 不写 markup（Header 的 Content 解析行为不确定）
        self.app.sub_title = t("read.loading")
        view = self.query_one("#feed", ListView)
        view.clear()
        self.items = []
        # 离线兜底先画缓存：重启秒开、断网可翻（详情自带正文的 RSS/Telegram
        # 离线也能读）；刷新成功整批替换，失败/未登录保留缓存不清屏。缓存
        # 内容在抓取时已登记过已见，不是「新」——快照清空
        cached = feed_cache.load(source.source_id, self.current_feed)
        if cached:
            self._new_count = 0
            await self._paint_items(cached, source, t("main.status_cached", n=len(cached)))
        try:
            loader = await open_coro
        except LoginRequired as exc:
            if not self.items:
                self.app.sub_title = t("main.status_signed_out")
            hint = t("main.hint_login", error=exc)
            self._tui.accounts.mark_stale(source)  # Ctrl+U 此后直接给登录框（判登正被骗着）
            if source.source_id in self._tui.accounts.refused:
                # 用户已拒绝过：不再自动弹框追问，常驻提示保留登录入口
                if not self.items:
                    self.show_empty(hint)
                return
            if not self.items:
                self.show_empty(str(exc))
            # 撞墙即给入口：弹登录框与后续动线在 AccountFlow（_refresh 本就
            # 在 worker 里，其 wait_for_dismiss 合法）
            await self._tui.accounts.on_login_wall(source, hint)
            return
        except Exception as exc:
            # 缓存行保留（有内容时不清屏不亮空态）：断网/改版时旧内容仍可翻
            logger.warning(
                "刷新失败 source=%s feed=%s", source.source_id, self.current_feed,
                exc_info=True,
            )
            self.app.sub_title = t("read.status_failed")
            feedback.fail(self, source, exc)
            if not self.items:
                self.show_empty(
                    t("main.empty_load_failed", text=feedback.failure_text(source, exc))
                )
            return

        self.feed_loader = loader
        # 先定格「新」计数（渲染不依赖它，但状态行要）+ 整流重数未读角标，再重写缓存
        self._new_count = len(
            self._tui.state.note_fetched(loader.items, self.current_feed, recount=True)
        )
        feed_cache.save(source.source_id, self.current_feed, loader.items)
        await self._paint_items(loader.items, source)  # 状态行走 _count_sub（含新计数）
        self._refresh_sidebar()

    async def _paint_items(
        self, items: list[FeedItem], source: Source, sub: str | None = None
    ) -> None:
        """整批渲染列表行（缓存先画与刷新成功共用一条路径）：清-置-画-选中-收空态。

        sub 缺省在置完 self.items 后自算（_count_sub 读它）——调用方传进来的
        实参会在赋值前求值，缓存态的「刷新中」文案走显式入参。行画的是
        _shown（F4 过滤态 = 未读子集），self.items 保持全量。
        """
        view = self.query_one("#feed", ListView)
        view.clear()
        self.items = items
        self._score_width = score_col_width(source, self.items)  # 列宽按本批最长元信息定
        self._tag_width = tag_col_width(self.items)  # 聚合流的源名列（单源流 0）
        self._time_width = time_col_width(self.items)  # 相对时间列（无时间批 0）
        shown = self._shown()
        width = self._wrap_width()
        await view.extend(
            [
                build_item(
                    it,
                    width,
                    self._score_width,
                    source,
                    self._row_mark(it),
                    tag=it.tag,
                    tag_width=self._tag_width,
                    time_width=self._time_width,
                )
                for it in shown
            ]
        )  # 单次挂载 = 单次布局
        if shown:
            view.index = 0  # 自动选中第一条，Enter 可直接进详情
            self._hide_empty()
        elif self.items:
            self.show_empty(self._empty_hint())  # 有数据但被过滤光：指路去关过滤
        else:
            self.show_empty(t("main.empty_no_content"))
        self.app.sub_title = sub if sub is not None else self._count_sub()

    # 行集 _shown 与过滤空态 _empty_hint 在 MainReadScreen（行集族）

    def _count_sub(self) -> str:
        """状态行计数文案：本批有「新」带新计数（刷新与 Ctrl+N 共用）。"""
        n = len(self._shown())
        return (
            t("main.status_with_new", n=n, new=self._new_count)
            if self._new_count
            else t("main.status_count", n=n)
        )

    def show_empty(self, text: str) -> None:
        """空态/失败态：列表区无内容时的常驻屏内提示（notify 是瞬时反馈，这是常驻状态）。"""
        hint = self.query_one("#empty-hint", Label)
        hint.update(f"[dim]{escape(text)}[/dim]")
        hint.display = True

    def _hide_empty(self) -> None:
        self.query_one("#empty-hint", Label).display = False

    async def _load_more_feed(self) -> None:
        loader = self.feed_loader
        if loader is None:
            return
        # 列表屏的加载更多没有独立加载行：进行中/结果全部落进常驻状态行
        # （Cloudflare 顶跳最长能到数十秒，没有进行中提示会像按键失灵）
        self.app.sub_title = t("main.status_more_loading", n=len(self.items))
        try:
            new = await loader.load_more()
        except Exception as exc:
            logger.warning(
                "加载更多失败 source=%s feed=%s", self.current_source.source_id, self.current_feed,
                exc_info=True,
            )
            self.app.sub_title = t("main.status_more_failed", n=len(self.items))
            if isinstance(exc, LoginRequired):
                self._tui.accounts.mark_stale(self.current_source)  # 会话失效记账
            feedback.fail(
                self, self.current_source, exc, action="feedback.action_more_failed"
            )
            return
        if loader is not self.feed_loader:
            return  # 期间切流/刷新：旧 loader 的迟到结果不能混进新列表
        if not new:
            if loader.done:
                self.app.sub_title = t("main.status_no_more", n=len(self.items))
                feedback.info(self, t("main.toast_no_more"))
            else:
                self.app.sub_title = t("main.status_no_new", n=len(self.items))
                feedback.info(self, t("main.toast_no_new_retry"))
            return
        self.items = loader.items
        # 新批次的首见数累进「新」计数与角标（不重数整流——批是局部的），缓存重写；
        # store 计数变了但部件不会自己动——边栏角标要跟着刷新（漏了这里会停在旧值）
        self._new_count += len(self._tui.state.note_fetched(new, self.current_feed))
        feed_cache.save(self.current_source.source_id, self.current_feed, self.items)
        self._refresh_sidebar()
        view = self.query_one("#feed", ListView)
        new_width = score_col_width(self.current_source, self.items)
        new_tag = tag_col_width(self.items)
        new_time = time_col_width(self.items)
        if (
            new_width > self._score_width
            or new_tag > self._tag_width
            or new_time > self._time_width
            or self._unread_only
        ):
            # 新批次带来更长的元信息/源名/成列时间（或过滤态下行集变化）：
            # 列变宽或行集重算——全量重建，不只是追加新行
            self._score_width = new_width
            self._tag_width = new_tag
            self._time_width = new_time
            await self.rebuild_items()
        else:
            width = self._wrap_width()
            await view.extend(
                [
                    build_item(
                        it,
                        width,
                        self._score_width,
                        self.current_source,
                        self._row_mark(it),
                        tag=it.tag,
                        tag_width=self._tag_width,
                        time_width=self._time_width,
                    )
                    for it in new
                ]
            )  # 单次挂载 = 单次布局
        self._hide_empty()
        self.app.sub_title = self._count_sub()

    # ---- 列表项构建与折行 ----

    def _wrap_width(self) -> int:
        """标题可用宽：折行算术与布局常量的对应关系集中在 ui/layout.py。"""
        return list_title_width(
            self.query_one("#feed", ListView).region.width,
            self._score_width,
            self._tag_width,
            self._time_width,
        )

    async def rebuild_items(self) -> None:
        """按当前宽度/过滤态重排列表（只重排版，不重新抓取；行集 = _shown）。"""
        if not self.items:
            return
        view = self.query_one("#feed", ListView)
        idx = view.index
        view.clear()
        width = self._wrap_width()
        src = self.current_source
        shown = self._shown()
        await view.extend(
            [
                build_item(
                    it,
                    width,
                    self._score_width,
                    src,
                    self._row_mark(it),
                    tag=it.tag,
                    tag_width=self._tag_width,
                    time_width=self._time_width,
                )
                for it in shown
            ]
        )
        if shown:
            view.index = 0 if idx is None else min(idx, len(shown) - 1)
            self._hide_empty()
        else:
            view.index = None
            self.show_empty(self._empty_hint())
        self.app.sub_title = self._count_sub()

    # ---- 收藏：Ctrl+F 标记 / F3 列表（屏在 ui/screens/favorites.py）----

    def action_favorite(self) -> None:
        """Ctrl+F：收藏/取消当前条目（全量快照防失效，F3 查看列表）。

        主列表不加收藏列——toast 即反馈，收藏态的去处是 F3 那张跨源列表。
        """
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        fav = self._tui.state.toggle_fav(item)
        feedback.info(self, t("fav.toast_added" if fav else "fav.toast_removed"))

    def action_favorites(self) -> None:
        """F3：收藏列表（读本地快照零请求，见 ui/screens/favorites.py）。"""
        self.app.push_screen(FavoritesScreen())

    def action_theme(self) -> None:
        """Ctrl+T：主题选择。纯显示层即时生效（对照 Ctrl+L 语言的重启模型），
        选完当场换肤 + 落盘；wait_for_dismiss 要在 worker 里（语言屏同款）。"""
        self.app.run_worker(self._theme_flow(), group="theme")

    async def _theme_flow(self) -> None:
        from feedscope.ui.screens.theme import ThemeScreen

        name = await self.app.push_screen(
            ThemeScreen(highlight=self.app.theme), wait_for_dismiss=True
        )
        if not name or name == self.app.theme:
            return
        self.app.theme = name  # Textual 即时应用全屏换肤
        config.save_theme(name)
        feedback.info(self, t("theme.toast_applied", name=name))

    def action_copy_link(self) -> None:
        """Ctrl+Y：复制当前条目链接（详情/回答层的 Ctrl+Y 复制的是正在读的）。"""
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        self.app.copy_to_clipboard(item.url)
        feedback.info(self, t("main.toast_copied"))

    def action_help(self) -> None:
        """F1：键位帮助（页脚装不下的键都在这里，从本屏声明自动生成、按任务分组）。"""
        feed_widget = self.query_one("#feed", FeedListView)  # tab 切站声明在控件上
        self.app.push_screen(
            HelpScreen(
                [
                    (t("help.global_section"), global_rows()),
                    *group_rows(screen_rows(feed_widget, self, skip={"f1", "ctrl+q"})),
                ]
            )
        )

    def action_open_browser(self) -> None:
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))  # 空态/加载中按 Ctrl+O 也绝不无声
            return
        self._mark_read(item)  # Ctrl+O 也是「打开了这条」：与 Enter 的浏览器兜底同语义
        webbrowser.open(item.url)

    def action_more(self) -> None:
        if self.feed_loader is None:
            feedback.info(self, t("main.toast_nothing_to_load"))  # 失败/空态下按 Ctrl+N 也绝不无声
            return
        self.run_worker(self._load_more_feed(), group="more", exclusive=True)

    # ---- 账号：Ctrl+U（动线在 ui/account.AccountFlow）----

    def action_account(self) -> None:
        """Ctrl+U 的薄转发：分派与登录/登出过程在 AccountFlow。

        走 worker 因为 push_screen 的 wait_for_dismiss 只能在 worker 里用。
        group 独立于列表加载的 default 组，Ctrl+U 不会打断在途的 _refresh。
        """
        if not self.sources:
            feedback.info(self, t("main.toast_no_sources"))
            return
        self.app.run_worker(
            self._tui.accounts.open(self.current_source), group="account", exclusive=True
        )

    # ---- Enter：进详情或跳浏览器 ----

    def current_item(self) -> FeedItem | None:
        shown = self._shown()
        if not shown:
            return None
        view = self.query_one("#feed", ListView)
        if view.index is None or view.index >= len(shown):
            return None
        return shown[view.index]

    def _opens_detail(self, item: FeedItem) -> bool:
        """Enter 的分派谓词：进终端详情屏还是跳浏览器，由源的类型词汇表决定
        （Source.supports_detail + detail_kinds）。Enter 与 [/] 换篇共用。"""
        source = self.current_source
        return source.supports_detail and item.kind in source.detail_kinds

    def step_readable_item(self, delta: int) -> FeedItem | None:
        """列表选中项沿 delta 方向推进到下一篇可进终端详情的条目，并同步列表选中。

        详情屏 [/] 换篇的供给端：跳过 Enter 会跳浏览器的类型（阅读流要留在终端里，
        阅读中突然弹出浏览器是事故）；沿方向没有可读条目时不动选中、返回 None。
        推进沿 _shown（过滤态下跳过已读行——它们本来就不在屏上）。
        """
        view = self.query_one("#feed", ListView)
        shown = self._shown()
        if not shown or view.index is None:
            return None
        for idx in range(view.index + delta, len(shown) if delta > 0 else -1, delta):
            it = shown[idx]
            if self._opens_detail(it):
                view.index = idx
                # ←/→ 换篇的静默推进同样计已读：详情屏拿目标必经这里，主屏
                # 上下文里就地清圆点，不必跨屏回写
                self._mark_read(it)
                return it
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "feed":  # 只认主列表：楼层选择弹层等也会发同名事件
            return
        item = self.current_item()
        if item is None:
            return
        self._mark_read(item)  # 打开即已读：进详情与浏览器兜底都是「打开」
        if self._opens_detail(item):
            self.app.push_screen(DetailScreen(item, self.current_source, self.step_readable_item))
        else:
            webbrowser.open(item.url)

    # ---- 输入法没切英文的自救指路 ----

    def on_key(self, event: Key) -> None:
        """中文输入落地 → 提示哪些键不受输入法影响。

        输入法 composing 期间按键根本到不了终端（拦在候选框里），应用侧无从
        得知；只有用户把中文「打出来」（上屏字符经 pty 送进应用）才是输入法
        没切英文的可靠信号。主屏没有文本输入框，中文落地到这里必是误输入
        （订阅管理屏的输入框自己消费字符键，不会冒泡到这里）。
        到达这里的键都已被绑定链筛过（无任何绑定命中才冒泡），节流防连打刷屏。
        """
        ch = event.character or event.key  # 模拟按键（测试）只有 key，真终端两者同值
        if not any(_is_cjk(c) for c in ch):
            return
        now = time.monotonic()
        if now - self._cjk_hint_at < _CJK_HINT_INTERVAL:
            return
        self._cjk_hint_at = now
        feedback.info(self, t("main.toast_cjk_input"))

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        await self.release_feed_loader()  # 浏览器会话与连接池由 App.on_unmount 收
