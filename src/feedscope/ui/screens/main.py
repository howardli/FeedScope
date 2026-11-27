"""主屏（默认屏）：顶部站点 tab 条 + 左侧信息流边栏 + 单栏列表。

主屏继承链四层（各层一职，状态归属见 ui/screens/__init__.py）：
MainSetupScreen（启用集与当前位置）→ MainNavScreen（导航与切站切流）→
MainReadScreen（行集、过滤与已读）→ MainScreen（本模块）。本层只做叶子
才做得了的事：compose 与样式、两侧导航的渲染、空态提示、以及把键位接到
协作者和各屏上（Enter 动线、收藏 / 搜索 / 主题 / 帮助、Ctrl+N、账号）。
取数上屏的机械件在 main_feed.FeedFlow，续读位置在 main_resume.ResumeTracker。

键位归属：主屏专属键绑在屏级——弹层与详情屏在前台时这些绑定根本不命中；
App 只留全局退出与生命周期（见 app.py）。外部（AccountFlow 等）经 app.main
访问主屏。
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListView, Static

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.text import is_cjk as _is_cjk
from feedscope.ui import Bindings, feedback
from feedscope.ui.coalesce import CoalescedScheduler
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.launch import copy_link, open_link
from feedscope.ui.list_render import LIST_ROW_CSS, step_to
from feedscope.ui.markup import escape
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.favorites import FavoritesScreen
from feedscope.ui.screens.help import push_help
from feedscope.ui.screens.main_feed import FeedFlow
from feedscope.ui.screens.main_read import MainReadScreen
from feedscope.ui.screens.main_resume import ResumeTracker
from feedscope.ui.screens.search import SearchScreen
from feedscope.ui.screens.theme import pick_theme
from feedscope.ui.widgets import (
    FeedListResized,
    FeedListView,
    FeedNavItem,
    FeedNavPressed,
    SourceTab,
    SourceTabPressed,
)

_CJK_HINT_INTERVAL = 30.0  # 中文输入指路提示的节流间隔：连打中文只提示一次，不刷屏

logger = logging.getLogger(__name__)


class MainScreen(MainReadScreen):
    """站点 tab 条 + 信息流边栏 + 条目列表；渲染与键位接线。

    继承链：MainSetupScreen（启用集与当前位置）→ MainNavScreen（导航）→
    MainReadScreen（行集与已读/过滤）→ 本类。本层自己的状态只有两个：
    边栏刷新的合并式调度器、输入法提示的节流时间戳。
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
        Binding("f6", "search", "bindings.search", show=False),
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
        super().__init__()  # 位置 / 导航 / 行集各族状态在各自那层初始化
        # 协作者（取数上屏、续读位置）：要用到全链成员，所以在叶子组装
        self.flow = FeedFlow(self)
        self.resume = ResumeTracker(self)
        # 边栏刷新的合并式调度器（见 _refresh_sidebar；alive 守卫 = 收尾期不排程）
        self._sidebar_sched = CoalescedScheduler(
            self, self._render_feed_sidebar, group="sidebar", alive=lambda: self.is_running
        )
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
        self.run_worker(self._start())  # 启动序列（首启引导 + 初始渲染）在 MainSetupScreen

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
            items.append(Static(f"[dim]{escape(hint)}[/dim]"))
        await nav.mount(*items)

    def _refresh_sidebar(self) -> None:
        """边栏角标随未读数变化重建（抓取后/读到一条/清仓后调用；几项重建无压力）。

        合并式调度收口在 ui.coalesce（busy/dirty + 重跑循环的机理说明在彼）。"""
        self._sidebar_sched.trigger()

    async def on_source_tab_pressed(self, event: SourceTabPressed) -> None:
        await self._goto_source(event.idx)

    async def on_feed_nav_pressed(self, event: FeedNavPressed) -> None:
        await self.action_select_feed(event.idx)

    def on_feed_list_resized(self, event: FeedListResized) -> None:
        """列表视口改宽（防抖在控件里）：按新宽度重排标题折行；空列表重排是空转。
        走独立组（同 _sync_after_read 的组语义），不与在途刷新互相取消。"""
        if self.items:
            self.run_worker(self.flow.rebuild_rows(), group="rebuild", exclusive=True)

    # 行集 _shown 与过滤空态 _empty_hint 在 MainReadScreen（行集族）

    def show_empty(self, text: str) -> None:
        """空态/失败态：列表区无内容时的常驻屏内提示（notify 是瞬时反馈，这是常驻状态）。"""
        hint = self.query_one("#empty-hint", Label)
        hint.update(f"[dim]{escape(text)}[/dim]")
        hint.display = True

    def _hide_empty(self) -> None:
        self.query_one("#empty-hint", Label).display = False

    # ---- 列表项构建与折行 ----

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

    def action_search(self) -> None:
        """F6：本地搜索——跨全部列表缓存与收藏找条目（标题，以及 RSS / Telegram
        的正文），零请求；见 ui/screens/search.py。"""
        self.app.push_screen(SearchScreen())

    def action_theme(self) -> None:
        """Ctrl+T：主题选择（动线在 ui/screens/theme.pick_theme：选完即时换肤 + 落盘）。"""
        self.app.run_worker(pick_theme(self), group="theme")

    def action_copy_link(self) -> None:
        """Ctrl+Y：复制当前条目链接（详情/回答层的 Ctrl+Y 复制的是正在读的）。"""
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))
            return
        copy_link(self, item.url)

    def action_help(self) -> None:
        """F1：键位帮助（页脚装不下的键都在这里，从本屏声明自动生成、按任务分组；
        入口收口在 push_help，主列表控件随行——tab 切站声明在控件上）。"""
        push_help(self, self.query_one("#feed", FeedListView), skip={"ctrl+q"})

    def action_open_browser(self) -> None:
        item = self.current_item()
        if item is None:
            feedback.info(self, t("main.toast_no_selection"))  # 空态/加载中按 Ctrl+O 也绝不无声
            return
        self._mark_read(item)  # Ctrl+O 也是「打开了这条」：与 Enter 的浏览器兜底同语义
        open_link(self, item.url)

    def action_more(self) -> None:
        if self.flow.loader is None:
            feedback.info(self, t("main.toast_nothing_to_load"))  # 失败/空态下按 Ctrl+N 也绝不无声
            return
        self.run_worker(self.flow.load_more(), group="more", exclusive=True)

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

    def _opens_detail(self, item: FeedItem) -> bool:
        """Enter 的分派谓词：进终端详情屏还是跳浏览器（判据收口在
        Source.opens_detail，与收藏屏共用）。Enter 与 [/] 换篇共用。"""
        return self.current_source.opens_detail(item)

    def step_readable_item(self, delta: int) -> FeedItem | None:
        """列表选中项沿 delta 方向推进到下一篇可进终端详情的条目，并同步列表选中。

        详情屏 ←/→ 换篇的供给端：跳过 Enter 会跳浏览器的类型（阅读流要留在
        终端里，阅读中突然弹出浏览器是事故）；沿方向没有可读条目时不动选中、
        返回 None。推进沿 _shown（过滤态下跳过已读行——它们本来就不在屏上）。
        """
        return self._step_item(delta, unread_only=False)

    def step_unread_item(self, delta: int) -> FeedItem | None:
        """同上，但只停在「未读」的可读条目上：详情屏 ]/[ 换未读篇的供给端
        （newsboat 的 next-unread：读着一篇直接跳到下一篇没读过的，不必退回
        列表）。注意不能拿 step_readable_item 一篇篇试——它推进一篇就标一篇
        已读，扫过去未读态全被抹掉了。"""
        return self._step_item(delta, unread_only=True)

    def _step_item(self, delta: int, *, unread_only: bool) -> FeedItem | None:
        view = self.query_one("#feed", ListView)
        shown = self._shown()
        if not shown or view.index is None:
            return None
        target = step_to(
            view,
            shown,
            delta,
            readable=self._opens_detail,
            is_read=lambda it: self._tui.state.is_read(it.source, it.id),
            unread_only=unread_only,
        )
        if target is not None:
            # 换篇的静默推进同样计已读：详情屏拿目标必经这里，主屏上下文里
            # 就地清圆点，不必跨屏回写
            self._mark_read(target)
        return target

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "feed":  # 只认主列表：楼层选择弹层等也会发同名事件
            return
        item = self.current_item()
        if item is None:
            return
        self._mark_read(item)  # 打开即已读：进详情与浏览器兜底都是「打开」
        if self._opens_detail(item):
            self.app.push_screen(
                DetailScreen(
                    item,
                    self.current_source,
                    self.step_readable_item,
                    step_unread=self.step_unread_item,
                )
            )
        else:
            open_link(self, item.url)

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

    # ---- 续读位置：退出时记下源 / 流 / 选中条目，下次启动回到这里 ----

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """选中项变了：把它的条目 id 镜像到 _cursor_id（续读位置退出时要读）。

        列表的 index 是响应式属性，程序的 / 键盘的 / 鼠标点击的选中变化都会发
        这个事件——退出时列表已不在树上，位置只能在列表还在时记在普通属性里。
        事件是异步的，取的是处理时刻的 index：列表重画期间的中间事件（clear
        置空再落回第一条）都按最终状态算，留下一份与屏上一致的镜像。"""
        if event.list_view.id != "feed":  # 只认主列表：楼层选择弹层等也会发同名事件
            return
        idx = event.list_view.index
        shown = self._shown()
        self.resume.cursor_id = shown[idx].id if idx is not None and 0 <= idx < len(shown) else ""

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        self.resume.save()
        await self.flow.release_loader()  # 浏览器会话与连接池由 App.on_unmount 收
