"""TUI 主应用：顶部站点 tab 条 + 左侧信息流边栏 + 单栏列表 + 全屏详情屏。"""

from __future__ import annotations

import time
import webbrowser
from typing import Any, ClassVar

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, Resize
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

import feedscope.sources  # noqa: F401  import 即注册
from feedscope import login
from feedscope.core import config
from feedscope.core.browser import close_session, get_session
from feedscope.core.models import FeedItem
from feedscope.core.registry import all_sources, source_classes
from feedscope.core.source import FeedLoader, LoginRequired, Source
from feedscope.ui import feedback
from feedscope.ui.layout import META_COL_MAX, META_COL_SPARE, RESIZE_DEBOUNCE, list_title_width
from feedscope.ui.screens.detail import DetailScreen
from feedscope.ui.screens.source_setup import ManageSourcesScreen, OnboardingScreen
from feedscope.ui.theme import FEEDSCOPE_THEME
from feedscope.ui.wrap import display_width, wrap_display

# 两轴导航：Tab/Shift+Tab 切站、←/→ 切流。键位全部非打印键（输入法免疫：
# 中文输入法只拦字母/数字进候选框），字母键只留给站点直达键（z/x）
PREWARM = True  # 启动即后台预热浏览器；测试环境置 False（见 tests/conftest.py）


def _is_cjk(ch: str) -> bool:
    """是否中日韩字符（表意文字 / 中文标点 / 全角形式）：输入法上屏内容的判定范围。"""
    return (
        "\u4e00" <= ch <= "\u9fff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u3000" <= ch <= "\u303f"
        or "\uff00" <= ch <= "\uffef"
    )


_CJK_HINT_INTERVAL = 30.0  # 中文输入指路提示的节流间隔：连打中文只提示一次，不刷屏


def meta_col_text(source: Source, it: FeedItem) -> str:
    """元信息列文本：所有源、所有流同一条规则——「[类型标签] 作者/热度/回复数」。

    标签预告 Enter 的行为（非详情类型跳浏览器，词汇表归源，见 Source.kind_labels）；
    有标签时与元信息并列显示，不再顶掉作者。任何情况下不截断——列宽随批内
    最长内容自适应（见 score_col_width），超上限的长署名在列内折行（换行可以，
    缩略不行），名字与账号必须完整可见。
    """
    tag = source.kind_labels.get(it.kind, "")
    return f"[{tag}] {it.meta}".strip() if tag else it.meta


def score_col_width(source: Source, items: list[FeedItem]) -> int:
    """元信息列宽：批内最长文本的显示宽 + 2 余量（META_COL_MAX 封顶）。

    自适应让知乎热榜「2606 万热度」这类短元信息只占实际所需；超过 MAX 的
    长署名不加宽列（标题列优先），在列内折行——名字一行、@账号一行，完整
    可见。空列表给 1（空态不显示列表）。
    """
    longest = max((display_width(meta_col_text(source, it)) for it in items), default=1)
    return min(longest, META_COL_MAX) + META_COL_SPARE


class SourceTabPressed(Message):
    """顶部站点 tab 被点击：与键盘直达键共用同一条切源路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class FeedNavPressed(Message):
    """左侧信息流项被点击：与 ←/→ 共用同一条切流路径。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        super().__init__()


class SourceTab(Static):
    """顶部站点 tab：显示「名称(直达键)」，点击直达该源；高亮由 CSS class 决定。

    不可聚焦（can_focus=False 默认）：点击不抢列表焦点，Tab 键语义完全不变。
    """

    def __init__(self, idx: int, label: str, key: str | None, active: bool) -> None:
        text = f"{label}({key})" if key else label
        super().__init__(
            text,
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
        # 切站/直达键不进页脚：顶部 tab 条已标注「名称(键)」，页脚再列就重复了
        Binding("tab", "app.next_source", "切站", show=False),
        Binding("shift+tab", "app.prev_source", "切站", show=False),
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


class AccountScreen(ModalScreen[bool]):
    """账号操作弹框：登录 / 登出一个源。返回 True=确认、False=取消。

    只在「选择」阶段可见；确认后即 dismiss，真正的有头登录 / 清 cookie 由
    后台 worker 执行（期间 profile 锁冲突由 _login_in_progress 挡刷新兜底）。
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "取消", show=False)]

    def __init__(self, label: str, action: str) -> None:
        super().__init__()
        self._label = label
        self._action = action  # "登录" / "登出"

    def compose(self) -> ComposeResult:
        prompt = f"{self._label}需要登录" if self._action == "登录" else f"登出 {self._label}？"
        with Vertical(id="account-dialog"):
            yield Label(prompt, id="account-prompt")
            with Horizontal(id="account-actions"):
                yield Button(self._action, id="account-go", variant="primary")
                yield Button("取消", id="account-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "account-go")

    def action_cancel(self) -> None:
        self.dismiss(False)


class FeedScopeApp(App[None]):
    TITLE = "FeedScope（观流）"

    # 关闭 Textual 自带命令面板（页脚 palette 链接 + Ctrl+P）：里面只有框架默认的
    # Theme/Quit/Keys/Screenshot 系统命令，对本应用全是冗余——主题定死编辑器风、
    # 退出有 q，其余是调试入口，不该暴露给用户。
    ENABLE_COMMAND_PALETTE = False

    BINDINGS: ClassVar[list[Binding]] = [
        # 键位全部非打印键：字母/数字会被中文输入法拦进候选框，一个动作只留
        # 一个键，不给字母留别名（唯一例外：站点直达键 z/x，见 _bind_source_keys，
        # 仍走字母因为 Tab 循环已可达、直达键是加速位）。Ctrl+C 连按退出是
        # Textual 自带兜底，不占页脚。
        # ←/→ / Ctrl+N 放 App 级（非 FeedListView）：焦点不在列表上也必须生效
        # （切流是高频主操作，不能依赖焦点落位）；详情/评论屏的 ←/→ 换篇在
        # 各屏绑定，屏级先于 App 级命中，互不干扰。
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("f5", "refresh", "刷新"),
        Binding("ctrl+o", "open_browser", "浏览器"),
        Binding("f2", "manage_sources", "源管理"),
        Binding("ctrl+u", "account", "账号", show=False),
        Binding("ctrl+n", "more", "加载更多"),
        Binding("left", "prev_feed", "切流"),
        Binding("right", "next_feed", "切流"),
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

    /* 左侧信息流边栏：active 用编辑器蓝的暗化底（与 ListView 选中色同源） */
    #main { height: 1fr; }
    #feed-nav { width: 16; height: 1fr; background: $surface; padding: 0 1; }
    .feed-nav-item { height: 1; }
    .feed-nav-active { background: $primary-darken-2; color: $text; text-style: bold; }

    #empty-hint { display: none; padding: 1 2; }

    /* 账号弹框：居中卡片，按钮行并排 */
    #account-dialog { width: auto; max-width: 60; background: $surface; border: ascii $primary; padding: 1 2; }
    #account-prompt { padding-bottom: 1; }
    #account-actions { height: auto; }
    #account-actions Button { margin-right: 1; }

    #detail-title { text-style: bold; padding: 1 2; border: ascii; width: 1fr; text-wrap: wrap; }
    #detail-meta { padding: 0 2; }
    #detail-desc { padding: 0 2; }
    .answers-header { padding: 0 2; text-style: bold; }
    .answer-divider { width: 1fr; height: 1; }
    .answer-head { padding: 1 2 0 2; text-wrap: wrap; }  /* 窄终端长楼层头折行不裁剪 */
    .answer-body { padding: 0 2; }

    /* 回答详情屏（评论层）：正文区与问题详情同款，评论楼层为主楼层不缩进 */
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
    #more-hint, #more-loading, #no-more { padding: 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(FEEDSCOPE_THEME)  # 克制的编辑器风配色（见 ui/theme.py），产品需求而非保守
        self.theme = FEEDSCOPE_THEME.name
        self.sources = self._enabled_sources()
        self.source_idx = 0
        self.feed_idx = 0
        self._feed_memory: dict[str, int] = {}  # source_id -> 上次选的信息流（切站回到上次看的板块）
        self.items: list[FeedItem] = []
        self.feed_loader: FeedLoader | None = None  # 持有活页签，切流/退出前要 close
        # 元信息列宽（随当前批内容自适应，见 score_col_width；首渲染前是占位值）
        self._score_width = 1 + META_COL_SPARE
        self._login_in_progress = False  # 有头登录进行中：挡下刷新/切源误触发 profile 锁冲突
        # 用户已拒绝登录询问的源（本次运行内有效）：撞墙不再自动弹框，只留
        # 常驻「Ctrl+U 登录」提示。Ctrl+U 手动登录照常可用；登录成功/登出后
        # 清除，后续撞墙（登录过期、换账号）恢复自动询问。
        self._login_refused: set[str] = set()
        self._subs_dirty = False  # 订阅管理屏改过信息流（F2 收尾时消费，见 _manage_sources）
        self._source_key_skipped: list[str] = []  # 键位冲突被跳过的声明（on_mount 汇总警告）
        self._cjk_hint_at = 0.0  # 上次「输入法没切英文」提示的时刻（on_key 节流用）
        self._bind_source_keys()

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
            classes = {cls.source_id: cls for cls in source_classes()}
            made = [classes[sid]() for sid in enabled if sid in classes]
        # 无可选信息流的源不进 Tab（零订阅的 rss / 内置流全被隐藏的源）：
        # 空 tab 只有噪音；配置好订阅 / 取消隐藏后 F2 重勾即回来
        return [s for s in made if s.feeds]

    def _bind_source_keys(self) -> None:
        """按注册表给声明了 source_key 的源生成一键直达绑定（z 直切知乎、x 直切 X……）。

        UI 键位已全部非打印键（见 BINDINGS 注释），字母键整个让给直达键——历史
        上被 UI 占用的 q/r/o/n 等如今也可声明。动态绑定必须随实例走（类级
        BINDINGS 在 import 时就被 Textual 固化，测试换注册表时对不上）。绑定覆盖
        全部注册源（含停用的），动作按 source_id 动态寻址（action_source_hotkey
        查当前启用列表）——Textual 没有解绑 API，停用源若不占住键位，重新启用后
        直达键就回不来了。非法声明（非单个小写字母）与源间撞键跳过并警告，
        该源仍可 Tab 循环到达。
        """
        claimed: set[str] = set()
        for cls in source_classes():
            key = cls.source_key
            if key is None:
                continue
            if len(key) != 1 or not key.islower() or key in claimed:
                self._source_key_skipped.append(f"{cls.label}({key})")
                continue
            claimed.add(key)
            # show=False：直达键不进页脚——顶部 tab 已显示「名称(直达键)」，不重复
            self.bind(key, f"app.source_hotkey({cls.source_id!r})", description=f"切到{cls.label}", show=False)

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
        if self._source_key_skipped:
            feedback.warn(
                self,
                "、".join(self._source_key_skipped) + " 的直达键与现有键位冲突，已跳过（仍可 Tab 切换）",
                title="键位冲突",
            )
        self.run_worker(self._start())
        if PREWARM:
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
                feeds = "、".join(labels[:3]) + (f" 等 {len(labels)} 个流" if len(labels) > 3 else "")
            else:
                feeds = "无订阅 · → 添加"
            entries.append((source.source_id, f"{source.label} [dim]{feeds}[/dim]"))
        return entries

    def _rebuild_enabled(self, ids: list[str]) -> None:
        """按启用列表重建源列表与选中位（纯状态，不碰 DOM）。

        展示顺序 = ids 顺序（弹屏按勾选顺序产出，落盘 enabled 同序）；尽量停
        在原来的源（没被停用的话）；直达键绑定不用动——按 id 动态寻址（见
        _bind_source_keys）。
        """
        chosen = set(ids)
        classes = {cls.source_id: cls for cls in source_classes()}
        prev_id = self.sources[self.source_idx].source_id if self.sources else None
        made = [classes[sid]() for sid in ids if sid in classes]
        self.sources = [s for s in made if s.feeds]  # 空源不进 Tab（同 _enabled_sources）
        self._feed_memory = {k: v for k, v in self._feed_memory.items() if k in chosen}
        if prev_id is not None and prev_id in chosen:
            self.source_idx = next(i for i, s in enumerate(self.sources) if s.source_id == prev_id)
        else:
            self.source_idx = 0
        feeds = self.current_source.feeds if self.sources else []
        mem = self._feed_memory.get(self.current_source.source_id, 0) if self.sources else 0
        self.feed_idx = mem if feeds and 0 <= mem < len(feeds) else 0

    async def _apply_enabled(self, ids: list[str]) -> None:
        """启用集变化的生效路径（F2 源管理）：状态重建 + 两侧导航重渲染 + 刷新。

        只在 sources 组的 worker 里调用（该组不被 default 组的 exclusive 刷新
        取消，见 action_manage_sources）。
        """
        self._rebuild_enabled(ids)
        await self._render_source_tabs()
        await self._render_feed_sidebar()
        self.action_refresh()

    # ---- 顶部 tab 条 / 左侧信息流边栏（点击与键盘共用同一条动作路径）----

    async def _render_source_tabs(self) -> None:
        """按注册表重建顶部站点 tab 条，高亮当前源。

        重建必须先等旧子项摘除（remove_children 的删除是异步落地的），否则
        新子项带着重复 id 挂载会触发 DuplicateIds。
        """
        tabs = self.query_one("#source-tabs", Horizontal)
        await tabs.remove_children()
        for idx, source in enumerate(self.sources):
            await tabs.mount(
                SourceTab(idx, source.label, source.source_key, active=(idx == self.source_idx))
            )

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

    def action_refresh(self) -> None:
        if self._login_in_progress:
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

    async def _refresh(self) -> None:
        if self.feed_loader is not None:  # 切流前关掉旧页签
            await self.feed_loader.close()
            self.feed_loader = None
        if not self.sources:  # 全部停用：常驻空态指路（F2 再开），不退出
            view = self.query_one("#feed", ListView)
            view.clear()
            self.items = []
            self.sub_title = ""
            self._show_empty("没有启用的源 · 按 F2 管理")
            return
        source = self.current_source

        open_coro = source.open_feed(self.current_feed)

        self.sub_title = self._crumb("加载中…")
        view = self.query_one("#feed", ListView)
        view.clear()
        self.items = []
        try:
            loader = await open_coro
        except LoginRequired as exc:
            self.sub_title = self._crumb("未登录")
            hint = f"{exc} · Ctrl+U 登录"
            if source.source_id in self._login_refused:
                # 用户已拒绝过：不再自动弹框追问，常驻提示保留登录入口
                self._show_empty(hint)
                return
            self._show_empty(str(exc))
            # 撞墙即给入口：弹登录框（等用户选「登录」才开浏览器，不擅自弹窗）。
            # 每个源一次；取消即记住（本运行内不再自动问），切页/刷新不被反复
            # 打断。_refresh 本就在 worker 里，wait_for_dismiss 合法。
            proceed = await self.push_screen(
                AccountScreen(source.label, "登录"), wait_for_dismiss=True
            )
            if proceed:
                self.run_worker(self._do_login(source), group="login", exclusive=True)
            else:
                self._login_refused.add(source.source_id)
                self._show_empty(hint)
                feedback.info(self, "已取消，本次运行不再自动询问；Ctrl+U 可登录")
            return
        except Exception as exc:
            self.sub_title = self._crumb("加载失败")
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
        self.sub_title = self._crumb(f"{len(self.items)} 条")

    def _show_empty(self, text: str) -> None:
        """空态/失败态：列表区无内容时的常驻屏内提示（notify 是瞬时反馈，这是常驻状态）。"""
        hint = self.query_one("#empty-hint", Label)
        hint.update(f"[dim]{escape(text)}[/dim]")
        hint.display = True

    def _hide_empty(self) -> None:
        self.query_one("#empty-hint", Label).display = False

    def _crumb(self, state: str) -> str:
        """Header 副标题 = 状态行；站点/信息流位置已由顶部 tab 条与左侧边栏承担。

        纯文本不依赖 markup（Header 的 Content 解析行为不确定）。
        """
        return state

    async def _load_more_feed(self) -> None:
        loader = self.feed_loader
        if loader is None:
            return
        # 列表屏的加载更多没有独立加载行：进行中/结果全部落进常驻状态行
        # （Cloudflare 顶跳最长能到数十秒，没有进行中提示会像按键失灵）
        self.sub_title = self._crumb(f"{len(self.items)} 条 · 加载更多…")
        try:
            new = await loader.load_more()
        except Exception as exc:
            self.sub_title = self._crumb(f"{len(self.items)} 条 · 加载更多失败")
            feedback.fail(self, self.current_source, exc, action="加载更多失败")
            return
        if not new:
            if loader.done:
                self.sub_title = self._crumb(f"{len(self.items)} 条 · 没有更多了")
                feedback.info(self, "没有更多了")
            else:
                self.sub_title = self._crumb(f"{len(self.items)} 条 · 这轮没拉到新内容")
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
        self.sub_title = self._crumb(f"{len(self.items)} 条")

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

    # ---- 导航多路合一：顶部 tab 点击 / Tab 循环 / source_key 直达键，侧栏点击 / ←→ 切流 ----

    async def _goto_source(self, target: int) -> None:
        """切到第 target 个源：Tab 循环（±1）、source_key 直达键与 tab 点击共用。

        source_key 直达键在详情屏也要生效：先退回列表再切源（z 直切知乎，不强迫
        先 Esc）；Tab 循环与 tab 点击本就在主屏触发，不受这条路径影响。只有一个
        源或已在目标源时不做任何事。
        """
        if len(self.sources) < 2:
            return
        target %= len(self.sources)
        while not self._on_main_screen():
            self.pop_screen()  # 详情屏中按直达键：退回列表再切源（与 Esc 同一条 pop 路径）
        if target == self.source_idx:
            return
        self.source_idx = target
        feeds = self.current_source.feeds
        mem = self._feed_memory.get(self.current_source.source_id, 0)
        self.feed_idx = mem if feeds and 0 <= mem < len(feeds) else 0  # 回到该站上次的信息流
        await self._render_source_tabs()  # 高亮跟随（键盘/点击/直达键都走这里）
        await self._render_feed_sidebar()
        self.action_refresh()

    async def action_next_source(self) -> None:
        await self._goto_source(self.source_idx + 1)

    async def action_prev_source(self) -> None:
        await self._goto_source(self.source_idx - 1)

    async def action_select_source(self, idx: int) -> None:
        """source_key 绑定的一键直达：直接落到注册表第 idx 个源（绑定由 _bind_source_keys 生成）。"""
        await self._goto_source(idx)

    async def action_source_hotkey(self, source_id: str) -> None:
        """直达键的分发动作：按 source_id 在当前启用列表里寻址。

        绑定覆盖全部注册源（含停用的，见 _bind_source_keys），启用集变化不必
        重绑；停用源的直达键按下要给反馈，不静默。
        """
        for idx, source in enumerate(self.sources):
            if source.source_id == source_id:
                await self._goto_source(idx)
                return
        feedback.info(self, "该源未启用 · F2 管理")

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

    async def action_prev_feed(self) -> None:
        await self._step_feed(-1)

    async def action_next_feed(self) -> None:
        await self._step_feed(1)

    async def _step_feed(self, delta: int) -> None:
        """←/→ 切流（与详情屏 [/] 换篇同一条相对移动语义）：到边界给反馈，不静默。

        绑定在 App 级（焦点无关），弹层里按下静默返回——方向键是随手按的
        导航键，弹层里逐键弹 toast 只有噪音（详情屏不会到这：屏级 ←/→ 换篇
        绑定先命中）。
        """
        if not self._on_main_screen():
            return
        if not self.sources:
            return
        target = self.feed_idx + delta
        if not 0 <= target < len(self.current_source.feeds):
            feedback.info(self, "已是第一个流" if delta < 0 else "已是最后一个流")
            return
        await self.action_select_feed(target)

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

    # ---- 账号：Ctrl+U 登录 / 登出 ----

    def action_account(self) -> None:
        """Ctrl+U 按当前源状态分派：匿名源提示、已登录弹登出、未登录弹登录。

        只在列表主屏生效（详情屏/已有弹框时不叠加新账号框）；走 worker 因为
        push_screen 的 wait_for_dismiss 只能在 worker 里用。group 独立于列表
        加载的 default 组，u 不会打断在途的 _refresh。
        """
        if not self._on_main_screen():
            return
        if not self.sources:
            feedback.info(self, "没有启用的源 · F2 管理")
            return
        self.run_worker(self._account(), group="account", exclusive=True)

    async def _account(self) -> None:
        source = self.current_source
        if not source.login_url:
            feedback.info(self, f"{source.label} 无需登录")
            return
        if self._login_in_progress:
            feedback.info(self, "登录进行中，稍候")
            return
        logged_in = await login.is_logged_in(source.source_id)
        action = "登出" if logged_in else "登录"
        proceed = await self.push_screen(AccountScreen(source.label, action), wait_for_dismiss=True)
        if not proceed:
            if action == "登录":
                # 手动框里取消同样算拒绝：之后撞墙不再自动弹（登出框取消无此语义）
                self._login_refused.add(source.source_id)
            return
        # 后续动作放独立的 login 组：exclusive 会取消同组在途 worker，若与本
        # worker（account 组）同组，等于把调用者自己也排进取消名单——现在能
        # 活着只是因为派发后不再 await；分组错开即消除对这层时序的依赖。
        if logged_in:
            self.run_worker(self._do_logout(source), group="login", exclusive=True)
        else:
            self.run_worker(self._do_login(source), group="login", exclusive=True)

    async def _do_login(self, source: Source) -> None:
        """关 headless session 让出 profile 锁 → 有头浏览器登录 → 刷新。

        登录期间置 _login_in_progress，挡下刷新误触发 profile 锁冲突；完成后
        恢复并刷新（成功则列表出内容，失败/超时仍撞墙）。
        """
        self._login_in_progress = True
        try:
            if self.feed_loader is not None:  # 释放活页签，别跟有头登录抢 profile
                await self.feed_loader.close()
                self.feed_loader = None
            await close_session()
            try:
                ok = await login.login(source.source_id, quiet=True)
            except Exception:
                ok = False
            if ok:
                self._login_refused.discard(source.source_id)  # 后续撞墙（过期）恢复自动询问
                feedback.info(self, f"{source.label} 登录成功")
            else:
                # 未完成（超时/关窗放弃）：别再自动追问，重试入口在 toast 与常驻提示里
                self._login_refused.add(source.source_id)
                feedback.warn(self, f"{source.label} 登录未完成，可再试一次", title="登录")
        finally:
            self._login_in_progress = False
            self.action_refresh()

    async def _do_logout(self, source: Source) -> None:
        """清掉当前源登录 cookie → 刷新（撞墙弹登录框，即换账号动线）。"""
        self._login_refused.discard(source.source_id)  # 主动登出 = 想换账号：撞墙要重新弹登录框
        try:
            had = await login.logout(source.source_id)
        except Exception as exc:
            feedback.fail(self, source, exc, action="登出失败")
            return
        feedback.info(self, f"已登出 {source.label}" if had else f"{source.label} 本就没有登录")
        self.action_refresh()

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
        feedback.info(
            self, "检测到中文输入：仅 z/x 站点直达键需英文输入法，其余快捷键不受影响"
        )

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        if self.feed_loader is not None:
            await self.feed_loader.close()
            self.feed_loader = None
        from feedscope.core import http

        await close_session()
        await http.close()
