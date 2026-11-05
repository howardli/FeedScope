"""TUI 主应用：顶部站点 tab 条 + 左侧信息流边栏 + 单栏列表 + 全屏详情屏。"""

from __future__ import annotations

import os
import time
import webbrowser
from typing import Any, ClassVar

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, Resize
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core import config
from feedscope.core.browser import close_session, get_session
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources, get_class, source_classes
from feedscope.core.source import FeedLoader, LoginRequired, Source
from feedscope.core.text import is_cjk as _is_cjk
from feedscope.ui import feedback
from feedscope.ui.account import AccountFlow
from feedscope.ui.layout import (
    META_COL_SPARE,
    RESIZE_DEBOUNCE,
    list_title_width,
    meta_col_text,
    score_col_width,
)
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.source_setup import ManageSourcesScreen, OnboardingScreen
from feedscope.ui.theme import FEEDSCOPE_THEME
from feedscope.ui.wrap import wrap_display

# 两轴导航：Tab/Shift+Tab 切站、←/→ 切流。键位全部非打印键（输入法免疫：
# 中文输入法只拦字母/数字进候选框）

_CJK_HINT_INTERVAL = 30.0  # 中文输入指路提示的节流间隔：连打中文只提示一次，不刷屏


def _no_prewarm() -> bool:
    """禁预热开关：FEEDSCOPE_NO_PREWARM=1 时启动不拉浏览器。

    测试与截图脚本用（stub 源不需要真 Chrome，拉起来既慢又可能挂起）；
    运行时读环境变量而非模块级开关，工具侧设 env 即生效、不用掰模块属性。
    """
    return os.environ.get("FEEDSCOPE_NO_PREWARM") == "1"


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
    """列表视图：Tab/Shift+Tab 切网站（widget 层绑定压过 Screen 焦点切换）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        # 切站进页脚（与 ←/→ 切流同款）：Tab 是切站唯一键位，页脚要给出来路
        Binding("tab", "app.next_source", "切站"),
        Binding("shift+tab", "app.prev_source", "切站"),
        # ↑/↓/Enter 同键改派 App 级导航动作（压过 ListView 原生选中/移动）：
        # 焦点在列表上是主屏常态（唯一可聚焦控件），区域焦点 _pane 决定这组
        # 键到哪——逻辑集中在 App 层，这里只负责把键活着送过去
        Binding("up", "app.nav_up", show=False),
        Binding("down", "app.nav_down", show=False),
        Binding("enter", "app.nav_enter", show=False),
        # home/end/pageup/pagedown 原生是列表视口滚动：键盘在边栏/源栏时
        # 滚一个看不见的列表违反「焦点在哪键就作用在哪」，一并改派分派
        Binding("home", 'app.nav_scroll("home")', show=False),
        Binding("end", 'app.nav_scroll("end")', show=False),
        Binding("pageup", 'app.nav_scroll("pageup")', show=False),
        Binding("pagedown", 'app.nav_scroll("pagedown")', show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_width = 0
        self._resize_timer: Timer | None = None

    def on_resize(self, event: Resize) -> None:
        if event.size.width == self._last_width:
            return
        first_layout = self._last_width == 0  # 首次布局不算
        self._last_width = event.size.width
        if first_layout:
            return
        # 与详情屏一致：拖拽改宽连发 Resize，稳定后再整屏重排
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE, self.app.rebuild_items)

    def on_unmount(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None


class FeedScopeApp(App[None]):
    TITLE = "FeedScope（观流）"

    # 关闭 Textual 自带命令面板（页脚 palette 链接 + Ctrl+P）：里面只有框架默认的
    # Theme/Quit/Keys/Screenshot 系统命令，对本应用全是冗余——主题定死编辑器风、
    # 退出有 q，其余是调试入口，不该暴露给用户。
    ENABLE_COMMAND_PALETTE = False

    BINDINGS: ClassVar[list[Binding]] = [
        # 键位全部非打印键：字母/数字会被中文输入法拦进候选框，一个动作只留
        # 一个键，不给字母留别名。
        # Ctrl+C 连按退出是
        # Textual 自带兜底，不占页脚。
        # ←/→/↑/↓/Enter/Esc 是区域导航键，分派逻辑全在 App 级 nav_*：焦点在
        # 列表上（主屏常态）时由 FeedListView 的同键改派送来（压过原生选中/
        # 移动），焦点丢失时 App 级直达同一条动作；弹层/详情屏由
        # _on_main_screen 守卫静默（方向键是随手按的导航键）。
        # CLI 惯例的空间语义：↑/↓ 在纵向列表里移动选中；首行再 ↑ 上探源栏
        # （顶部区域在所有内容之上；源栏 ↓ 下探边栏，与边栏首流 ↑ 互逆）；
        # 其余区域移动走横向（列表 ← 进边栏、边栏 → 回列表 / ← 去源栏）；
        # 列表 → 抵屏幕右缘无事。
        # 详情/评论屏的 ←/→ 换篇在各屏绑定，屏级先于 App 级命中，互不干扰。
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("f5", "refresh", "刷新"),
        Binding("ctrl+o", "open_browser", "浏览器"),
        Binding("f2", "manage_sources", "源管理"),
        Binding("ctrl+u", "account", "账号", show=False),
        Binding("ctrl+n", "more", "加载更多"),
        Binding("left", "nav_left", "边栏"),
        Binding("right", "nav_right", show=False),
        Binding("up", "nav_up", show=False),
        Binding("down", "nav_down", show=False),
        Binding("enter", "nav_enter", show=False),
        Binding("escape", "nav_back", show=False),
    ]

    CSS = """
    ListItem { padding: 0 1; height: auto; }

    .item-row { height: auto; }
    /* 宽度不写死：逐行 inline 设为批内最长元信息（见 score_col_width）；
       右对齐列，超上限的长署名折行而非缩略（名字与账号必须完整可见） */
    .item-score { text-align: right; text-wrap: wrap; }
    .item-title { width: 1fr; text-wrap: wrap; padding-left: 1; }

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
        # 克制的编辑器风配色（见 ui/theme.py），产品需求而非保守
        self.register_theme(FEEDSCOPE_THEME)
        self.theme = FEEDSCOPE_THEME.name
        self.sources = self._enabled_sources()
        self.source_idx = 0
        self.feed_idx = 0
        # 主屏区域焦点 list（默认）/sidebar/tabs，方向键边界跃迁（见 _enter_pane）
        self._pane = "list"
        # source_id -> 上次选的信息流（切站回到上次看的板块）
        self._feed_memory: dict[str, int] = {}
        self.items: list[FeedItem] = []
        self.feed_loader: FeedLoader | None = None  # 持有活页签，切流/退出前要 close
        # 元信息列宽（随当前批内容自适应，见 score_col_width；首渲染前是占位值）
        self._score_width = 1 + META_COL_SPARE
        # 账号动线（撞墙自动询问 / Ctrl+U 登录登出）：状态与编排都在 ui/account.py
        self.accounts = AccountFlow(self)
        self._subs_dirty = False  # 订阅管理屏改过信息流（F2 收尾时消费，见 _manage_sources)
        self._cjk_hint_at = 0.0  # 上次「输入法没切英文」提示的时刻（on_key 节流用）

    def _enabled_sources(self) -> list[Source]:
        """按配置取启用的源，展示顺序 = enabled 的书写顺序（= 用户勾选顺序）。

        enabled 缺省 = 全部启用（注册顺序，老配置文件只有 hide 的不受影响）；
        不在注册表的 id 静默略过——卸载 / 改名后的残留条目无害，doctor 会报告
        配置概况。
        """
        enabled = config.enabled_ids()
        if enabled is None:
            made = all_sources()
        else:
            made = [cls() for sid in enabled if (cls := get_class(sid)) is not None]
        # 无可选信息流的源不进 Tab（零订阅的 rss / 内置流全被隐藏的源）：
        # 空 tab 只有噪音；配置好订阅 / 取消隐藏后 F2 重勾即回来
        return [s for s in made if s.feeds]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(id="source-tabs")  # 顶部站点 tab 条（子项由 _render_source_tabs 挂载）
        with Horizontal(id="main"):
            yield Vertical(id="feed-nav")  # 左侧信息流边栏（子项由 _render_feed_sidebar 挂载）
            yield FeedListView(id="feed")
        yield Label("", id="empty-hint")  # 空态/失败态常驻提示
        yield Footer()

    def on_mount(self) -> None:
        if not source_classes():
            self.exit(message="没有注册任何源")
            return
        self.query_one("#feed", ListView).focus()
        self.run_worker(self._start())
        if not _no_prewarm():
            self.run_worker(self._prewarm(), group="prewarm")  # 后台预热，见 _prewarm

    async def _prewarm(self) -> None:
        """后台预热浏览器会话：首条列表不用再付冷启动（Chrome 拉起几秒；
        UA 探针另有 7 天落盘缓存，多数启动只付 Chrome 拉起）。

        知乎（通道页签）与 X（profile 读 cookie）都要浏览器，预热几乎总会
        用上；失败静默——首次真正取数时自然再试并给正式报错。
        """
        try:
            await get_session().start()
        except Exception:
            pass

    async def _start(self) -> None:
        """启动序列：首启（配置文件不存在）先引导选源，确认落盘后才进正常渲染。

        勾选是使用源的前提：引导取消（None）= 放弃本次使用，不默认全开。
        _start 本就在 worker 里，push_screen 的 wait_for_dismiss 合法。
        """
        if not config.is_configured():
            chosen = await self.push_screen(
                OnboardingScreen(self._setup_entries(), [s.source_id for s in self.sources]),
                wait_for_dismiss=True,
            )
            if chosen is None:
                self.exit(message="未完成初始设置，重新运行 feedscope 再次选择")
                return
            config.save_enabled(chosen)
            # 只重建状态，渲染统一交给 _bootstrap：_start 在 default 组里跑，
            # 若此处走 _apply_enabled 的 action_refresh（default 组 exclusive），
            # 会把还在 await 的自己取消在「旧子项已摘、新子项未挂」的中间态
            self._rebuild_enabled(chosen)
            self._hint_empty_subscribables(chosen)
        await self._bootstrap()

    async def _bootstrap(self) -> None:
        """初始渲染：tab 条/边栏重建是异步的（等旧子项摘除），随后才开始抓取。"""
        await self._render_source_tabs()
        await self._render_feed_sidebar()
        self.action_refresh()

    def _setup_entries(self) -> list[tuple[str, str]]:
        """源选择弹层的行数据：(source_id, 行文案)，注册顺序即列表顺序。

        遍历实例而非类：feeds 是配置合成后的视图，rss 的流来自用户订阅表
        （类默认为空）。文案带上各源的信息流名（超 3 个截成计数，几十个
        订阅不至于刷屏）；无可选信息流的源不列出——勾上也只能得到空 tab，
        例外是可订阅源（Source.subscribable）：空订阅也列出，→ 进订阅管
        理屏就地添加（否则零订阅的 rss 永远进不了这个列表，鸡生蛋）。
        """
        entries: list[tuple[str, str]] = []
        for source in all_sources():
            labels = [source.feed_labels.get(f, f) for f in source.feeds]
            if not labels and not source.subscribable:
                continue
            if labels:
                feeds = "、".join(labels[:3]) + (f" 等 {len(labels)} 个流" if len(labels) > 3
                                                  else "")
            else:
                feeds = "无订阅 · → 添加"
            entries.append((source.source_id, f"{source.label} [dim]{feeds}[/dim]"))
        return entries

    def _rebuild_enabled(self, ids: list[str]) -> None:
        """按启用列表重建源列表与选中位（纯状态，不碰 DOM）。

        展示顺序 = ids 顺序（弹屏按勾选顺序产出，落盘 enabled 同序）；尽量停
        在原来的源——前提是它还在 self.sources 里（勾了但零订阅的源会被
        feeds 过滤出局，「停在原源」按过滤后的实际名单找，找不到回 0 位，
        不能按 chosen 找：否则 next() 落空直接崩，保存后的重建全断）。
        """
        chosen = set(ids)
        prev_id = self.sources[self.source_idx].source_id if self.sources else None
        made = [cls() for sid in ids if (cls := get_class(sid)) is not None]
        self.sources = [s for s in made if s.feeds]  # 空源不进 Tab（同 _enabled_sources）
        self._feed_memory = {k: v for k, v in self._feed_memory.items() if k in chosen}
        if prev_id is not None and any(s.source_id == prev_id for s in self.sources):
            self.source_idx = next(i for i, s in enumerate(self.sources) if s.source_id == prev_id)
        else:
            self.source_idx = 0
        self._restore_feed_idx()  # 停在该站上次的信息流（越界回 0）

    async def _apply_enabled(self, ids: list[str]) -> None:
        """启用集变化的生效路径（F2 源管理）：状态重建 + 两侧导航重渲染 + 刷新。

        只在 sources 组的 worker 里调用（该组不被 default 组的 exclusive 刷新
        取消，见 action_manage_sources）。
        """
        self._rebuild_enabled(ids)
        self._hint_empty_subscribables(ids)
        await self._render_source_tabs()
        await self._render_feed_sidebar()
        self.action_refresh()

    def _hint_empty_subscribables(self, ids: list[str]) -> None:
        """勾了零订阅的可订阅源：给下一步指引（rss/telegram 的 feeds 全来自
        订阅表，零订阅 = 保存成功也不进 Tab——没有这条提示用户只看到「没生效」）。"""
        for sid in ids:
            cls = get_class(sid)
            if cls is not None and cls.subscribable and not cls().feeds:
                feedback.info(self, f"{cls.label} 已启用但还没有订阅——F2 里选中它按 → 添加")

    # ---- 顶部 tab 条 / 左侧信息流边栏（点击与键盘共用同一条动作路径）----

    async def _render_source_tabs(self) -> None:
        """按注册表重建顶部站点 tab 条，高亮当前源。

        重建必须先等旧子项摘除（remove_children 的删除是异步落地的），否则
        新子项带着重复 id 挂载会触发 DuplicateIds。
        """
        tabs = self.query_one("#source-tabs", Horizontal)
        await tabs.remove_children()
        for idx, source in enumerate(self.sources):
            await tabs.mount(SourceTab(idx, source.label, active=(idx == self.source_idx)))

    async def _render_feed_sidebar(self) -> None:
        """按当前源重建左侧信息流边栏，高亮当前流（←/→ 依序切换）。"""
        nav = self.query_one("#feed-nav", Vertical)
        await nav.remove_children()
        items: list[Widget] = []
        if self.sources:
            source = self.current_source
            items = [
                FeedNavItem(idx, source.feed_labels.get(name, name), active=(idx == self.feed_idx))
                for idx, name in enumerate(source.feeds)
            ]
        if not items:
            hint = "（未启用任何源，F2 管理）" if not self.sources else "（无信息流）"
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
        if self.accounts.in_progress:
            feedback.info(self, "登录进行中，稍候刷新")  # 有头登录独占 profile，此时别碰浏览器
            return
        if not self._require_main_screen():
            return  # 详情屏在前台时 r 不做隐形后台刷新（会顶掉详情标题、重开隐藏列表的页签）
        # 先停在途的增量加载：旧 loader 的迟到写入会把条目混进新列表（索引错位）
        self.workers.cancel_group(self, "more")
        self.run_worker(self._refresh(), exclusive=True)

    def _on_main_screen(self) -> bool:
        """是否在列表主屏（无推入屏）。App 级绑定全局生效，动作只对主屏状态合法。"""
        return len(self.screen_stack) == 1

    def _require_main_screen(self) -> bool:
        """主屏专属动作的守卫：被挡下要给反馈，绝不静默（与空态下按 Ctrl+N 同一原则）。"""
        if self._on_main_screen():
            return True
        feedback.info(self, "非列表屏，Esc 返回后再操作")
        return False

    async def _release_feed_loader(self) -> None:
        """关掉当前流 loader 持有的活页签并复位（切流/登录前让出资源）。"""
        if self.feed_loader is not None:
            await self.feed_loader.close()
            self.feed_loader = None

    async def _refresh(self) -> None:
        await self._release_feed_loader()  # 切流前关掉旧页签（await 完成再继续）
        if not self.sources:  # 全部停用：常驻空态指路（F2 再开），不退出
            view = self.query_one("#feed", ListView)
            view.clear()
            self.items = []
            self.sub_title = ""
            self._show_empty("没有启用的源 · 按 F2 管理")
            return
        source = self.current_source

        open_coro = source.open_feed(self.current_feed)

        # Header 副标题 = 状态行（站点/信息流位置由 tab 条与边栏承担）；纯文本
        # 不写 markup（Header 的 Content 解析行为不确定）
        self.sub_title = "加载中…"
        view = self.query_one("#feed", ListView)
        view.clear()
        self.items = []
        try:
            loader = await open_coro
        except LoginRequired as exc:
            self.sub_title = "未登录"
            hint = f"{exc} · Ctrl+U 登录"
            if source.source_id in self.accounts.refused:
                # 用户已拒绝过：不再自动弹框追问，常驻提示保留登录入口
                self._show_empty(hint)
                return
            self._show_empty(str(exc))
            # 撞墙即给入口：弹登录框与后续动线在 AccountFlow（_refresh 本就
            # 在 worker 里，其 wait_for_dismiss 合法）
            await self.accounts.on_login_wall(source, hint)
            return
        except Exception as exc:
            self.sub_title = "加载失败"
            feedback.fail(self, source, exc)
            self._show_empty(f"加载失败：{feedback.failure_text(source, exc)}")
            return

        self.feed_loader = loader
        self.items = loader.items
        self._score_width = score_col_width(source, self.items)  # 列宽按本批最长元信息定
        width = self._wrap_width()
        await view.extend([self._build_item(it, width) for it in self.items])  # 单次挂载 = 单次布局
        if self.items:
            view.index = 0  # 自动选中第一条，Enter 可直接进详情
            self._hide_empty()
        else:
            self._show_empty("没有内容 · F5 刷新 · Tab 或点击顶部 tab 切换站点")
        self.sub_title = f"{len(self.items)} 条"

    def _show_empty(self, text: str) -> None:
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
        self.sub_title = f"{len(self.items)} 条 · 加载更多…"
        try:
            new = await loader.load_more()
        except Exception as exc:
            self.sub_title = f"{len(self.items)} 条 · 加载更多失败"
            feedback.fail(self, self.current_source, exc, action="加载更多失败")
            return
        if not new:
            if loader.done:
                self.sub_title = f"{len(self.items)} 条 · 没有更多了"
                feedback.info(self, "没有更多了")
            else:
                self.sub_title = f"{len(self.items)} 条 · 这轮没拉到新内容"
                feedback.info(self, "这轮没拉到新内容，稍后再按 Ctrl+N 重试")
            return
        self.items = loader.items
        view = self.query_one("#feed", ListView)
        new_width = score_col_width(self.current_source, self.items)
        if new_width > self._score_width:
            # 新批次带来更长的元信息（更长的署名）：列变宽，旧条目的列宽与
            # 标题折行全都要重排——全量重建，不只是追加新行
            self._score_width = new_width
            await self.rebuild_items()
        else:
            width = self._wrap_width()
            await view.extend([self._build_item(it, width) for it in new])  # 单次挂载 = 单次布局
        self._hide_empty()
        self.sub_title = f"{len(self.items)} 条"

    # ---- 列表项构建与折行 ----

    def _wrap_width(self) -> int:
        """标题可用宽：折行算术与布局常量的对应关系集中在 ui/layout.py。"""
        return list_title_width(self.query_one("#feed", ListView).region.width, self._score_width)

    def _meta_col(self, it: FeedItem) -> str:
        """元信息列文本（规则见 meta_col_text）。右对齐与超宽折行走 CSS（.item-score）。"""
        text = meta_col_text(self.current_source, it)
        return f"[dim]{escape(text)}[/dim]" if text else " "

    def _build_item(self, it: FeedItem, width: int) -> ListItem:
        # 规范版折行（避头尾 + 词不拆），逐行转义
        title = "\n".join(escape(line) for line in wrap_display(it.title, width))
        score = Label(self._meta_col(it), classes="item-score")
        score.styles.width = self._score_width  # 列宽随批内容自适应（layout.py 常量说明）
        return ListItem(
            Horizontal(
                score,
                Label(title, classes="item-title"),
                classes="item-row",
            )
        )

    async def rebuild_items(self) -> None:
        """终端宽度变化后按新宽度重新折行（只重排版，不重新抓取）。"""
        if not self.items:
            return
        view = self.query_one("#feed", ListView)
        idx = view.index
        view.clear()
        width = self._wrap_width()
        await view.extend([self._build_item(it, width) for it in self.items])
        view.index = 0 if idx is None else min(idx, len(self.items) - 1)

    # ---- 键位 ----

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
        if self._pane != "tabs":
            self._enter_pane("sidebar")  # 切站落边栏：先挑流再回列表（源栏态连切不被拽走）
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
        if not self._on_main_screen():
            return  # 弹层里静默（方向键是随手按的导航键，逐键弹 toast 只有噪音）
        if self._pane == "tabs":
            await self.action_prev_source()  # 与 Tab 同一条切站路径
        elif self._pane == "sidebar":
            self._enter_pane("tabs")
        else:
            self._enter_pane("sidebar")

    async def action_nav_right(self) -> None:
        """→ 的单点分派：源栏态切下一个站；边栏态回列表；列表态抵屏幕右缘，
        静默无事（与源栏顶、列表底的同款边界行为）。"""
        if not self._on_main_screen():
            return
        if self._pane == "tabs":
            await self.action_next_source()
        elif self._pane == "sidebar":
            self._enter_pane("list")

    async def _step_feed(self, delta: int) -> None:
        """边栏 ↑/↓ 切流：末流到边给反馈；首个流再 ↑ 上探源栏（与列表首条
        ↑ 同规——顶部区域在所有内容之上；源栏 ↓ 下探与之互逆）。

        列表态不再切流——← 是区域移动（进边栏停当前流），切流只发生在
        边栏的 ↑/↓（纵向列表的本职）。App 级焦点无关，弹层里按下静默返回。
        """
        if not self._on_main_screen():
            return
        if not self.sources:
            return
        target = self.feed_idx + delta
        if not 0 <= target < len(self.current_source.feeds):
            if delta < 0:
                self._enter_pane("tabs")  # 首个流再 ↑：上探源栏
            else:
                feedback.info(self, "已是最后一个流")
            return
        await self.action_select_feed(target)

    # ---- 全屏方向键导航：列表 ⇄ 左侧边栏 ⇄ 顶部源栏（边界跃迁）----

    def _enter_pane(self, pane: str) -> None:
        """区域焦点切换（撞到边界继续往相邻区域走，键位表见 README）。

        pane 是纯逻辑层：Textual 焦点始终留在列表（主屏唯一可聚焦控件，
        Tab 切站的 widget 绑定才能随时命中、连按不退化），非列表态给 #feed
        挂 pane-aside 让列表光标转「暗」（仿 unfocused，见 CSS）；容器
        pane-focus 标记聚焦区域，_render_* 全量重建子项不受影响。
        """
        if not self._on_main_screen():
            return
        if pane == self._pane:
            return
        if pane == "tabs" and len(self.sources) < 2:
            return  # 单源无站可切，跃迁进去只剩空操作（与 Tab 切站同款静默）
        self._pane = pane
        self.query_one("#source-tabs", Horizontal).set_class(pane == "tabs", "pane-focus")
        self.query_one("#feed-nav", Vertical).set_class(pane == "sidebar", "pane-focus")
        self.query_one("#feed", FeedListView).set_class(pane != "list", "pane-aside")
        if pane != "list":
            feedback.info(
                self,
                "顶部源栏：←→ 切站，↓ 进边栏"
                if pane == "tabs"
                else "左侧边栏：↑↓ 切流，→ 回列表",
            )

    async def action_nav_up(self) -> None:
        """↑ 的单点分派：边栏态切上一个流（首个流再 ↑ 上探源栏，由
        _step_feed 分派）；源栏态抵屏幕上缘；列表态移文章选中（首条再
        ↑ 上探源栏——顶部区域在所有内容之上）。焦点丢了先找回；弹层/
        详情屏被 _on_main_screen 挡下静默。"""
        if not self._on_main_screen():
            return
        if self._pane == "sidebar":
            await self._step_feed(-1)
            return
        if self._pane == "tabs":
            return
        view = self.query_one("#feed", FeedListView)
        if not view.has_focus:
            view.focus()  # 焦点丢失（测试置空/竞态）：↑ 先把焦点找回来
            return
        if view.index in (None, 0):
            self._enter_pane("tabs")  # 首条再 ↑：上探源栏
        else:
            view.action_cursor_up()

    async def action_nav_down(self) -> None:
        """↓ 的单点分派：边栏态切下一个流（末流到边给反馈）；源栏态下探
        边栏（唯一的纵向区域移动：顶部 → 边栏）；列表态移文章选中。"""
        if not self._on_main_screen():
            return
        if self._pane == "sidebar":
            await self._step_feed(1)
            return
        if self._pane == "tabs":
            self._enter_pane("sidebar")
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
        if not self._on_main_screen():
            return
        if self._pane == "list":
            self.query_one("#feed", FeedListView).action_select_cursor()
        else:
            self._enter_pane("list")

    def action_nav_back(self) -> None:
        """App 级 Esc：边栏/源栏态先回列表（逐层返回的最内层）。

        弹层/详情屏的 Esc 由各自屏级绑定先命中，落不到这。
        """
        if not self._on_main_screen():
            return
        if self._pane != "list":
            self._enter_pane("list")

    async def action_nav_scroll(self, key: str) -> None:
        """home/end/pageup/pagedown：列表视口滚动，只在列表态生效——键盘
        在边栏/源栏时去滚一个看不见的列表，违反「焦点在哪键就作用在哪」。
        """
        if not self._on_main_screen() or self._pane != "list":
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
            feedback.info(self, "当前没有选中的条目")  # 空态/加载中按 Ctrl+O 也绝不无声
            return
        webbrowser.open(item.url)

    def action_more(self) -> None:
        if not self._require_main_screen():
            return  # Ctrl+N 已提升到 App 级（焦点无关），弹层/详情屏上要有反馈
        if self.feed_loader is None:
            feedback.info(self, "当前列表没有可加载的内容")  # 失败/空态下按 Ctrl+N 也绝不无声
            return
        self.run_worker(self._load_more_feed(), group="more", exclusive=True)

    # ---- 账号：Ctrl+U 登录 / 登出（动线在 ui/account.AccountFlow）----

    def action_account(self) -> None:
        """Ctrl+U 的薄转发：主屏守卫在此，分派与登录/登出过程在 AccountFlow。

        走 worker 因为 push_screen 的 wait_for_dismiss 只能在 worker 里用。
        group 独立于列表加载的 default 组，u 不会打断在途的 _refresh。
        """
        if not self._on_main_screen():
            return
        if not self.sources:
            feedback.info(self, "没有启用的源 · F2 管理")
            return
        self.run_worker(self.accounts.open(self.current_source), group="account", exclusive=True)

    # ---- 源管理：F2 启用 / 停用 ----

    def action_manage_sources(self) -> None:
        """F2 源管理：勾选启用的源，保存即生效——增加 / 删除 / 修改启用集都走这里。

        只在列表主屏生效；走 worker 因为 push_screen 的 wait_for_dismiss 只能
        在 worker 里用，group 独立于列表加载，s 不打断在途的 _refresh。
        """
        if not self._on_main_screen():
            return
        self.run_worker(self._manage_sources(), group="sources", exclusive=True)

    async def _manage_sources(self) -> None:
        current_ids = [s.source_id for s in self.sources]
        chosen = await self.push_screen(
            ManageSourcesScreen(self._setup_entries(), current_ids, self._mark_subs_dirty),
            wait_for_dismiss=True,
        )
        if chosen is None:
            # 取消：启用集不落盘，但订阅若在二级屏改过已即时生效——重建不欠着
            if self._subs_dirty:
                self._subs_dirty = False
                ids = config.enabled_ids()
                await self._apply_enabled(ids if ids is not None else current_ids)
            return
        config.save_enabled(chosen)
        self._subs_dirty = False  # 确认路径 _apply_enabled 全量重建，无需再消费
        await self._apply_enabled(chosen)
        if chosen:
            feedback.info(self, f"已保存：{len(chosen)} 源启用")
        else:
            feedback.info(self, "已保存：当前没有启用的源（F2 可再开启）")

    def _mark_subs_dirty(self) -> None:
        """订阅管理屏回调：改过信息流。F2 收尾（无论确认取消）都要重建。"""
        self._subs_dirty = True

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
                return it
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "feed":  # 只认主列表：楼层选择弹层等也会发同名事件
            return
        item = self.current_item()
        if item is None:
            return
        if self._opens_detail(item):
            self.push_screen(DetailScreen(item, self.current_source, self.step_readable_item))
        else:
            webbrowser.open(item.url)

    # ---- 输入法没切英文的自救指路 ----

    def on_key(self, event: Key) -> None:
        """中文输入落地 → 提示哪些键不受输入法影响。

        输入法 composing 期间按键根本到不了终端（拦在候选框里），应用侧无从
        得知；只有用户把中文「打出来」（上屏字符经 pty 送进应用）才是输入法
        没切英文的可靠信号。主屏没有文本输入框，中文落地到 App 必是误输入
        （订阅管理屏的输入框自己消费字符键，不会冒泡到这里）。
        到达这里的键都已被绑定链筛过（无任何绑定命中才冒泡到 App），节流防连打刷屏。
        """
        ch = event.character or event.key  # 模拟按键（测试）只有 key，真终端两者同值
        if not any(_is_cjk(c) for c in ch):
            return
        now = time.monotonic()
        if now - self._cjk_hint_at < _CJK_HINT_INTERVAL:
            return
        self._cjk_hint_at = now
        feedback.info(self, "检测到中文输入：快捷键全部是非打印键，不受输入法影响")

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        if self.feed_loader is not None:
            await self.feed_loader.close()
            self.feed_loader = None
        from feedscope.core import http

        await close_session()
        await http.close()
