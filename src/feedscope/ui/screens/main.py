"""主屏（默认屏）：顶部站点 tab 条 + 左侧信息流边栏 + 单栏列表。

从 app.py 下沉为主屏后，键位归属与守卫关系自然理顺：主屏专属键
（刷新/翻页/方向导航/F2/Ctrl+U）绑在屏级——弹层与详情屏在前台时这些
绑定根本不命中，原先 App 级动作里的「是否在主屏」守卫不再是必需品；
App 只留全局退出与生命周期（见 app.py）。主屏状态（选中位/区域焦点/
列表数据）也都在这里，外部（AccountFlow 等）经 app.main 访问。
"""

from __future__ import annotations

import time
import webbrowser
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, Resize
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListView, Static

from feedscope.core import feed_cache
from feedscope.core.models import FeedItem
from feedscope.core.source import FeedLoader, LoginRequired, Source
from feedscope.core.i18n import t
from feedscope.core.text import is_cjk as _is_cjk
from feedscope.ui import Bindings, feedback
from feedscope.ui.layout import (
    META_COL_SPARE,
    RESIZE_DEBOUNCE,
    list_title_width,
    score_col_width,
    tag_col_width,
)
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.list_render import LIST_ROW_CSS, MARK_UNREAD, build_item
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.main_setup import MainSetupScreen

_CJK_HINT_INTERVAL = 30.0  # 中文输入指路提示的节流间隔：连打中文只提示一次，不刷屏

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环


class Pane(StrEnum):
    """主屏的区域焦点（纯逻辑层，Textual 焦点始终留在列表）。"""

    LIST = "list"
    SIDEBAR = "sidebar"
    TABS = "tabs"


class SourceTabPressed(Message):
    """顶部站点 tab 被点击：与 Tab 循环共用同一条切源路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class FeedNavPressed(Message):
    """左侧信息流项被点击：与 ←/→ 共用同一条切流路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class SourceTab(Static):
    """顶部站点 tab：显示源名，点击切到该源；高亮由 CSS class 决定。

    不可聚焦（can_focus=False 默认）：点击不抢列表焦点，Tab 键语义完全不变。
    """

    def __init__(self, idx: int, label: str, active: bool) -> None:
        super().__init__(
            label,
            id=f"source-tab-{idx}",
            classes="source-tab" + (" source-tab-active" if active else ""),
        )
        self.idx = idx

    def on_click(self) -> None:
        self.post_message(SourceTabPressed(self.idx))


class FeedNavItem(Static):
    """左侧信息流项：流名，点击切流（键盘走 ←/→）；高亮由 CSS class 决定。"""

    def __init__(self, idx: int, label: str, active: bool) -> None:
        super().__init__(
            label,
            id=f"feed-nav-{idx}",
            classes="feed-nav-item" + (" feed-nav-active" if active else ""),
        )
        self.idx = idx

    def on_click(self) -> None:
        self.post_message(FeedNavPressed(self.idx))


class FeedListView(ListView):
    """列表视图：Tab/Shift+Tab 切网站（widget 层绑定压过 Screen 焦点切换）。

    导航键改派为裸动作名（不带 app. 前缀）：沿焦点链冒泡到主屏的
    action_nav_*（列表是主屏唯一可聚焦控件，主屏常态下必经此路）。
    """

    BINDINGS: ClassVar[Bindings] = [
        # 切站进页脚（与 ←/→ 切流同款）：Tab 是切站唯一键位，页脚要给出来路
        Binding("tab", "screen.next_source", "bindings.switch_source"),
        Binding("shift+tab", "screen.prev_source", "bindings.switch_source"),
        # ↑/↓/Enter 同键改派主屏导航动作（压过 ListView 原生选中/移动）：
        # 焦点在列表上是主屏常态，区域焦点 _pane 决定这组键到哪——
        # 逻辑集中在主屏，这里只负责把键活着送过去
        Binding("up", "screen.nav_up", show=False),
        Binding("down", "screen.nav_down", show=False),
        Binding("enter", "screen.nav_enter", show=False),
        # home/end/pageup/pagedown 原生是列表视口滚动：键盘在边栏/源栏时
        # 滚一个看不见的列表违反「焦点在哪键就作用在哪」，一并改派分派
        Binding("home", 'screen.nav_scroll("home")', show=False),
        Binding("end", 'screen.nav_scroll("end")', show=False),
        Binding("pageup", 'screen.nav_scroll("pageup")', show=False),
        Binding("pagedown", 'screen.nav_scroll("pagedown")', show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_width = 0
        self._resize_timer: Timer | None = None

    def on_resize(self, event: Resize) -> None:
        if event.size.width == self._last_width:
            return
        self._last_width = event.size.width
        screen = self.screen
        if not isinstance(screen, MainScreen) or not screen.items:
            return  # 首布局（尚无内容）不算；空列表重排也是空转
        # 拖拽改宽连发 Resize，稳定后再整屏重排（防抖与详情屏同款）；宽度
        # 与 _last_width 比对（初值 0），若列表竟在首布局前建好（窄宽挤压
        # 在左），首个 Resize 也会触发重排自愈——详情屏的 rendered-width
        # 自愈同思路，这里列表无渲染态可记，用 0 初值起比对
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, screen.rebuild_items)

    def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None


class MainScreen(MainSetupScreen):
    """站点 tab 条 + 信息流边栏 + 条目列表；全部主屏键位与状态。

    源启用编排（首启引导 / F2 管理 / 启用集生效）在父类 MainSetupScreen
    （screens/main_setup.py），本类只留渲染、导航与取数。
    """

    @property
    def _tui(self) -> FeedScopeApp:
        """self.app 的收窄视图：本应用的 App 恒为 FeedScopeApp。

        账号动线（accounts）等自定义成员经它获得静态类型；不直接覆写
        Screen.app（MessagePump.app 是可写 property，覆写签名过不了检）。
        """
        # cast 用字符串形式：FeedScopeApp 只在 TYPE_CHECKING 下导入（运行时导入会成环）
        return cast("FeedScopeApp", self.app)

    # 两轴导航：Tab/Shift+Tab 切站、←/→ 切流。键位全部非打印键（输入法免疫：
    # 中文输入法只拦字母/数字进候选框）。屏级绑定只在本屏前台时命中——
    # 弹层/详情屏在前台时这些键归它们管，主屏动作不需要守卫。
    # CLI 惯例的空间语义：↑/↓ 在纵向列表里移动选中；首行再 ↑ 上探源栏
    # （顶部区域在所有内容之上；源栏 ↓ 下探边栏，与边栏首流 ↑ 互逆）；
    # 其余区域移动走横向（列表 ← 进边栏、边栏 → 回列表 / ← 去源栏）；
    # 列表 → 抵屏幕右缘无事。详情/评论屏的 ←/→ 换篇在各屏自己的绑定。
    BINDINGS: ClassVar[Bindings] = [
        Binding("f5", "refresh", "bindings.refresh"),
        Binding("ctrl+o", "open_browser", "bindings.browser"),
        Binding("f2", "manage_sources", "bindings.manage_sources"),
        Binding("ctrl+u", "account", "bindings.account", show=False),
        Binding("ctrl+n", "more", "bindings.more"),
        Binding("ctrl+r", "toggle_read", "bindings.toggle_read"),
        Binding("ctrl+f", "favorite", "bindings.favorite"),
        Binding("f3", "favorites", "bindings.favorites"),
        # description 存文案键、挂载时解析（见 ui/labels.py）：类体求值时语言未定
        Binding("ctrl+l", "language", "bindings.language"),
        Binding("left", "nav_left", "bindings.sidebar"),
        Binding("right", "nav_right", show=False),
        Binding("up", "nav_up", show=False),
        Binding("down", "nav_down", show=False),
        Binding("enter", "nav_enter", show=False),
        Binding("escape", "nav_back", show=False),
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
        # 主屏区域焦点（默认列表），方向键边界跃迁（见 _enter_pane）
        self._pane = Pane.LIST
        # source_id -> 上次选的信息流（切站回到上次看的板块）
        self._feed_memory: dict[str, int] = {}
        self.items: list[FeedItem] = []
        self.feed_loader: FeedLoader | None = None  # 持有活页签，切流/退出前要 close
        # 本批「新抓到」的条目数（note_fetched 返回集的长度）：只喂顶部状态行
        # 的「N 新」计数。行首圆点不看它——未读是持久态，直到打开才消失
        # （传统阅读器语义；「新」是瞬态信息，归状态行）
        self._new_count = 0
        # 元信息列宽（随当前批内容自适应，见 score_col_width；首渲染前是占位值）
        self._score_width = 1 + META_COL_SPARE
        self._tag_width = 0  # 源名列宽（聚合流标订阅/频道名；单源流 0 不占列）
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
        """按当前源重建左侧信息流边栏，高亮当前流（←/→ 依序切换）。"""
        nav = self.query_one("#feed-nav", Vertical)
        await nav.remove_children()
        items: list[Widget] = []
        if self.sources:
            source = self.current_source
            items = [
                FeedNavItem(idx, source.feed_label(name), active=(idx == self.feed_idx))
                for idx, name in enumerate(source.feeds)
            ]
        if not items:
            hint = (
                t("main.sidebar_no_sources") if not self.sources else t("main.sidebar_no_feeds")
            )
            items.append(Static(f"[dim]{hint}[/dim]"))
        await nav.mount(*items)

    async def on_source_tab_pressed(self, event: SourceTabPressed) -> None:
        await self._goto_source(event.idx)

    async def on_feed_nav_pressed(self, event: FeedNavPressed) -> None:
        await self.action_select_feed(event.idx)

    # ---- 数据加载 ----

    @property
    def current_source(self) -> Source:
        return self.sources[self.source_idx]

    @property
    def current_feed(self) -> str:
        feeds = self.current_source.feeds
        return feeds[self.feed_idx] if feeds else ""

    def _restore_feed_idx(self) -> None:
        """切站后停回该站上次的信息流（_feed_memory）；越界（流被隐藏/删除）回 0。"""
        feeds = self.current_source.feeds if self.sources else []
        mem = self._feed_memory.get(self.current_source.source_id, 0) if self.sources else 0
        self.feed_idx = mem if feeds and 0 <= mem < len(feeds) else 0

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
            self.app.sub_title = t("read.status_failed")
            feedback.fail(self, source, exc)
            if not self.items:
                self.show_empty(
                    t("main.empty_load_failed", text=feedback.failure_text(source, exc))
                )
            return

        self.feed_loader = loader
        # 先定格「新」计数（渲染不依赖它，但状态行要），再重写缓存
        self._new_count = len(self._tui.state.note_fetched(loader.items))
        feed_cache.save(source.source_id, self.current_feed, loader.items)
        await self._paint_items(loader.items, source)  # 状态行走 _count_sub（含新计数）

    async def _paint_items(
        self, items: list[FeedItem], source: Source, sub: str | None = None
    ) -> None:
        """整批渲染列表行（缓存先画与刷新成功共用一条路径）：清-置-画-选中-收空态。

        sub 缺省在置完 self.items 后自算（_count_sub 读它）——调用方传进来的
        实参会在赋值前求值，缓存态的「刷新中」文案走显式入参。
        """
        view = self.query_one("#feed", ListView)
        view.clear()
        self.items = items
        self._score_width = score_col_width(source, self.items)  # 列宽按本批最长元信息定
        self._tag_width = tag_col_width(self.items)  # 聚合流的源名列（单源流 0）
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
                )
                for it in self.items
            ]
        )  # 单次挂载 = 单次布局
        if self.items:
            view.index = 0  # 自动选中第一条，Enter 可直接进详情
            self._hide_empty()
        else:
            self.show_empty(t("main.empty_no_content"))
        self.app.sub_title = sub if sub is not None else self._count_sub()

    def _count_sub(self) -> str:
        """状态行计数文案：本批有「新」带新计数（刷新与 Ctrl+N 共用）。"""
        return (
            t("main.status_with_new", n=len(self.items), new=self._new_count)
            if self._new_count
            else t("main.status_count", n=len(self.items))
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
            self.app.sub_title = t("main.status_more_failed", n=len(self.items))
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
        # 新批次的首见数累进「新」计数，缓存整流重写（列表变长了）
        self._new_count += len(self._tui.state.note_fetched(new))
        feed_cache.save(self.current_source.source_id, self.current_feed, self.items)
        view = self.query_one("#feed", ListView)
        new_width = score_col_width(self.current_source, self.items)
        new_tag = tag_col_width(self.items)
        if new_width > self._score_width or new_tag > self._tag_width:
            # 新批次带来更长的元信息/源名（更长的署名、聚合流翻出新订阅）：
            # 列变宽，旧条目的列宽与标题折行全都要重排——全量重建，不只是追加新行
            self._score_width = new_width
            self._tag_width = new_tag
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
            self.query_one("#feed", ListView).region.width, self._score_width, self._tag_width
        )

    async def rebuild_items(self) -> None:
        """终端宽度变化后按新宽度重新折行（只重排版，不重新抓取）。"""
        if not self.items:
            return
        view = self.query_one("#feed", ListView)
        idx = view.index
        view.clear()
        width = self._wrap_width()
        src = self.current_source
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
                )
                for it in self.items
            ]
        )
        view.index = 0 if idx is None else min(idx, len(self.items) - 1)

    # ---- 已读/未读（状态在 core/store.py，行首圆点见 ui/list_render.py）----

    def _row_mark(self, it: FeedItem) -> str:
        """行首圆点二态：未读 ●（编辑器蓝）、已读空。

        未读是持久态——刷新/重启/缓存先画共用同一条规则，圆点只随「打开」
        消失（传统阅读器语义）；「本批新了几条」不进行标记，归状态行计数。
        """
        return "" if self._tui.state.is_read(it.source, it.id) else MARK_UNREAD

    def _mark_read(self, item: FeedItem) -> None:
        """打开即已读（Enter 进详情 / 浏览器兜底 / ←/→ 换篇 / Ctrl+O）：
        状态落库 + 该行圆点就地清掉。"""
        self._tui.state.mark_read(item.source, item.id)
        self._update_row_mark(item)

    def _update_row_mark(self, item: FeedItem) -> None:
        """单行圆点就地重算（标记/切换不重建整表；重建路径自会走 _row_mark）。

        按 (source, id) 定位而不是对象相等：收藏屏回写主屏时传的是快照
        副本（meta 已加源名前缀），等值比对会失配；行身份本来就是键。
        """
        idx = next(
            (i for i, it in enumerate(self.items) if it.source == item.source and it.id == item.id),
            None,
        )
        if idx is None:
            return
        view = self.query_one("#feed", ListView)
        if idx >= len(view.children):
            return  # 行还没画上（在途）：下批渲染自然对
        view.children[idx].query_one(".item-mark", Label).update(self._row_mark(item))

    def action_toggle_read(self) -> None:
        """Ctrl+R：当前条目手动切换已读/未读（想重看的标回未读）。"""
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))  # 空态/加载中按键不无声
            return
        read = self._tui.state.toggle_read(item.source, item.id)
        self._update_row_mark(item)
        feedback.info(self, t("main.toast_marked_read" if read else "main.toast_marked_unread"))

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

    # ---- 导航多路合一：顶部 tab 点击 / Tab 循环切站，侧栏点击 / ←→ 切流 ----

    async def _goto_source(self, target: int) -> None:
        """切到第 target 个源：Tab 循环（±1）与 tab 点击共用。

        两个入口都只在主屏生效（列表绑定 / 主屏点击），不会在详情屏下触发。
        只有一个源或已在目标源时不做任何事。
        """
        if len(self.sources) < 2:
            return
        target %= len(self.sources)
        if target == self.source_idx:
            return
        self.source_idx = target
        self._restore_feed_idx()  # 回到该站上次的信息流
        await self._render_source_tabs()  # 高亮跟随（键盘/点击都走这里）
        await self._render_feed_sidebar()
        if self._pane != Pane.TABS:
            self._enter_pane(Pane.SIDEBAR)  # 切站落边栏：先挑流再回列表（源栏态连切不被拽走）
        self.action_refresh()

    async def action_next_source(self) -> None:
        await self._goto_source(self.source_idx + 1)

    async def action_prev_source(self) -> None:
        await self._goto_source(self.source_idx - 1)

    async def action_select_source(self, idx: int) -> None:
        """顶部 tab 点击的落点：直接切到第 idx 个源。"""
        await self._goto_source(idx)

    async def action_select_feed(self, idx: int) -> None:
        feeds = self.current_source.feeds
        if not feeds or not 0 <= idx < len(feeds):
            return
        if self.feed_idx == idx:
            return  # 已在该流，不重复刷新
        self.feed_idx = idx
        self._feed_memory[self.current_source.source_id] = idx  # 切站回来还在这个板块
        await self._render_feed_sidebar()  # 高亮跟随（←/→ /点击都走这里）
        self.action_refresh()

    async def action_nav_left(self) -> None:
        """← 的单点分派：源栏态切上一个站；边栏态去源栏；列表态进边栏——
        对齐当前流、不换流（空间语义：← 永远是向左的区域移动）。"""
        if self._pane == Pane.TABS:
            await self.action_prev_source()  # 与 Tab 同一条切站路径
        elif self._pane == Pane.SIDEBAR:
            self._enter_pane(Pane.TABS)
        else:
            self._enter_pane(Pane.SIDEBAR)

    async def action_nav_right(self) -> None:
        """→ 的单点分派：源栏态切下一个站；边栏态回列表；列表态抵屏幕右缘，
        静默无事（与源栏顶、列表底的同款边界行为）。"""
        if self._pane == Pane.TABS:
            await self.action_next_source()
        elif self._pane == Pane.SIDEBAR:
            self._enter_pane(Pane.LIST)

    async def _step_feed(self, delta: int) -> None:
        """边栏 ↑/↓ 切流：末流到边给反馈；首个流再 ↑ 上探源栏（与列表首条
        ↑ 同规——顶部区域在所有内容之上；源栏 ↓ 下探与之互逆）。

        列表态不再切流——← 是区域移动（进边栏停当前流），切流只发生在
        边栏的 ↑/↓（纵向列表的本职）。
        """
        if not self.sources:
            return
        target = self.feed_idx + delta
        if not 0 <= target < len(self.current_source.feeds):
            if delta < 0:
                self._enter_pane(Pane.TABS)  # 首个流再 ↑：上探源栏
            else:
                feedback.info(self, t("main.toast_last_feed"))
            return
        await self.action_select_feed(target)

    # ---- 全屏方向键导航：列表 ⇄ 左侧边栏 ⇄ 顶部源栏（边界跃迁）----

    def _enter_pane(self, pane: Pane) -> None:
        """区域焦点切换（撞到边界继续往相邻区域走，键位表见 README）。

        pane 是纯逻辑层：Textual 焦点始终留在列表（主屏唯一可聚焦控件，
        Tab 切站的 widget 绑定才能随时命中、连按不退化），非列表态给 #feed
        挂 pane-aside 让列表光标转「暗」（仿 unfocused，见 CSS）；容器
        pane-focus 标记聚焦区域，_render_* 全量重建子项不受影响。
        """
        if pane == self._pane:
            return
        if pane == Pane.TABS and len(self.sources) < 2:
            return  # 单源无站可切，跃迁进去只剩空操作（与 Tab 切站同款静默）
        self._pane = pane
        self.query_one("#source-tabs", Horizontal).set_class(pane == Pane.TABS, "pane-focus")
        self.query_one("#feed-nav", Vertical).set_class(pane == Pane.SIDEBAR, "pane-focus")
        self.query_one("#feed", FeedListView).set_class(pane != Pane.LIST, "pane-aside")
        if pane != Pane.LIST:
            feedback.info(
                self,
                t("main.hint_top_bar") if pane == Pane.TABS else t("main.hint_sidebar"),
            )

    async def action_nav_up(self) -> None:
        """↑ 的单点分派：边栏态切上一个流（首个流再 ↑ 上探源栏，由
        _step_feed 分派）；源栏态抵屏幕上缘；列表态移文章选中（首条再
        ↑ 上探源栏——顶部区域在所有内容之上）。焦点丢了先找回。"""
        if self._pane == Pane.SIDEBAR:
            await self._step_feed(-1)
            return
        if self._pane == Pane.TABS:
            return
        view = self.query_one("#feed", FeedListView)
        if not view.has_focus:
            view.focus()  # 焦点丢失（测试置空/竞态）：↑ 先把焦点找回来
            return
        if view.index in (None, 0):
            self._enter_pane(Pane.TABS)  # 首条再 ↑：上探源栏
        else:
            view.action_cursor_up()

    async def action_nav_down(self) -> None:
        """↓ 的单点分派：边栏态切下一个流（末流到边给反馈）；源栏态下探
        边栏（唯一的纵向区域移动：顶部 → 边栏）；列表态移文章选中。"""
        if self._pane == Pane.SIDEBAR:
            await self._step_feed(1)
            return
        if self._pane == Pane.TABS:
            self._enter_pane(Pane.SIDEBAR)
            return
        view = self.query_one("#feed", FeedListView)
        if not view.has_focus:
            view.focus()
            return
        view.action_cursor_down()

    def action_nav_enter(self) -> None:
        """Enter 的单点分派：边栏/源栏态回列表（流/站已随行走就地切换）；
        列表态 = 原生选中（进详情/开浏览器，与改派前同一条路径）。

        详情屏 Enter 进评论层是屏级绑定（先命中），到不了这。
        """
        if self._pane == Pane.LIST:
            self.query_one("#feed", FeedListView).action_select_cursor()
        else:
            self._enter_pane(Pane.LIST)

    def action_nav_back(self) -> None:
        """屏级 Esc：边栏/源栏态先回列表（逐层返回的最内层）。

        弹层/详情屏的 Esc 由各自屏级绑定先命中，落不到这。
        """
        if self._pane != Pane.LIST:
            self._enter_pane(Pane.LIST)

    async def action_nav_scroll(self, key: str) -> None:
        """home/end/pageup/pagedown：列表视口滚动，只在列表态生效——键盘
        在边栏/源栏时去滚一个看不见的列表，违反「焦点在哪键就作用在哪」。
        """
        if self._pane != Pane.LIST:
            return
        view = self.query_one("#feed", FeedListView)
        fn = {
            "home": view.action_scroll_home,
            "end": view.action_scroll_end,
            "pageup": view.action_page_up,
            "pagedown": view.action_page_down,
        }[key]
        maybe = fn()  # 新版 Textual 的滚动动作返回 AwaitComplete，可等可不等
        if maybe is not None and hasattr(maybe, "__await__"):
            await maybe

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
        if not self.items:
            return None
        view = self.query_one("#feed", ListView)
        if view.index is None:
            return None
        return self.items[view.index]

    def _opens_detail(self, item: FeedItem) -> bool:
        """Enter 的分派谓词：进终端详情屏还是跳浏览器，由源的类型词汇表决定
        （Source.supports_detail + detail_kinds）。Enter 与 [/] 换篇共用。"""
        source = self.current_source
        return source.supports_detail and item.kind in source.detail_kinds

    def step_readable_item(self, delta: int) -> FeedItem | None:
        """列表选中项沿 delta 方向推进到下一篇可进终端详情的条目，并同步列表选中。

        详情屏 [/] 换篇的供给端：跳过 Enter 会跳浏览器的类型（阅读流要留在终端里，
        阅读中突然弹出浏览器是事故）；沿方向没有可读条目时不动选中、返回 None。
        """
        view = self.query_one("#feed", ListView)
        if not self.items or view.index is None:
            return None
        for idx in range(view.index + delta, len(self.items) if delta > 0 else -1, delta):
            it = self.items[idx]
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
