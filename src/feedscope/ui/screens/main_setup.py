"""主屏继承链的根：启用集与当前位置（含其派生视图）+ 首启 / F2 的生效流程。

继承链四层（Setup → Nav → Read → MainScreen）都以本类为根，状态归属见
ui/screens/__init__.py。本层拥有并初始化「当前位置」一族：启用的源列表、
选中的源 / 流下标、各源上次停的流；current_source / current_feed 是它们的
只读视图，_restore_feed_idx / action_refresh 是建立在它们上的动作——位置
归谁，位置的派生物就归谁，下层只管挪位置。

取数上屏与续读位置是两个协作者（ui/screens/main_feed.FeedFlow、
main_resume.ResumeTracker）：它们要调用全链成员，所以由叶子类组装；本层只
声明类型，各层经 self.flow / self.resume 使用。仍然只能由叶子实现的，是三个
渲染钩子（两侧导航的重建 + 边栏刷新）——控件是叶子 compose 的。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from textual.screen import Screen

from feedscope.core import config, i18n
from feedscope.core.i18n import t
from feedscope.core.registry import all_sources, get_class
from feedscope.core.source import Source
from feedscope.ui import feedback, tui_of
from feedscope.ui.screens.language import LanguageScreen
from feedscope.ui.screens.source_setup import (
    ManageSourcesScreen,
    OnboardingScreen,
    source_entry_text,
)

if TYPE_CHECKING:
    from feedscope.ui.app import FeedScopeApp  # 运行时导入会与 app.py 成环
    from feedscope.ui.screens.main_feed import FeedFlow
    from feedscope.ui.screens.main_resume import ResumeTracker


class MainSetupScreen(Screen[None]):
    """主屏继承链的根：启用集与当前位置 + 首启 / F2 的生效流程。

    继承链：MainSetupScreen（本模块）→ MainNavScreen（导航）→
    MainReadScreen（行集与已读）→ MainScreen（取数上屏与渲染）。
    """

    # 协作者（由叶子类 MainScreen.__init__ 创建——它们要调用全链成员）
    flow: FeedFlow
    resume: ResumeTracker

    def __init__(self) -> None:
        super().__init__()
        # 当前位置：启用的源列表（展示顺序 = enabled 的书写顺序）与选中的源 / 流下标
        self.sources: list[Source] = self._enabled_sources()
        self.source_idx = 0
        self.feed_idx = 0
        # source_id -> 上次选的信息流（切站回到上次看的板块；F2 收尾时按新启用集清洗）
        self._feed_memory: dict[str, int] = {}
        self._subs_dirty = False  # 订阅管理屏改过信息流（F2 收尾时消费，见 _manage_sources）

    @property
    def _tui(self) -> FeedScopeApp:
        """self.app 的收窄视图（cast 与说明收口在 ui.tui_of，全 app 唯一出处）。"""
        return tui_of(self)

    # ---- 当前位置的只读视图（位置归本层，派生物也归本层）----

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

    # ---- 刷新（建立在当前位置上的动作；取数本体在 self.flow）----

    def action_refresh(self) -> None:
        # 有头登录独占 profile：走浏览器通道的源（声明了登录页的那些）此时别碰
        # 浏览器；纯 httpx 的源（HN / V2EX / RSS / Telegram）不受登录影响，照常刷新
        if self.sources and self.current_source.login_url and self._tui.accounts.in_progress:
            feedback.info(self, t("account.login_refresh_later"))
            return
        # 详情屏在前台时不做隐形后台刷新（会顶掉详情标题、重开隐藏列表的页签）
        if not self.is_current:
            return
        # 先停在途的增量加载：旧 loader 的迟到写入会把条目混进新列表（索引错位）
        self.workers.cancel_group(self, "more")
        self.run_worker(self.flow.refresh(), exclusive=True)

    # ---- 只有叶子能实现的渲染钩子（控件是叶子 compose 的）----

    @abstractmethod
    async def _render_source_tabs(self) -> None:
        """重建顶部站点 tab 条（高亮当前源；实现见 MainScreen）。"""

    @abstractmethod
    async def _render_feed_sidebar(self) -> None:
        """重建左侧信息流边栏（高亮当前流；实现见 MainScreen）。"""

    @abstractmethod
    def _refresh_sidebar(self) -> None:
        """请求边栏角标重建（实现见 MainScreen）。"""

    def _enabled_sources(self) -> list[Source]:
        """按配置取启用的源，展示顺序 = enabled 的书写顺序（= 用户勾选顺序）。

        enabled 缺省 = 全部启用（注册序 = 字母正序，老配置文件只有 hide 的不受影响）；
        不在注册表的 id 静默略过——卸载 / 改名后的残留条目无害，doctor 会报告
        配置概况。
        """
        return self._live_sources(config.enabled_ids())

    @staticmethod
    def _live_sources(ids: list[str] | None) -> list[Source]:
        """实例化 ids（None = 全部注册源），只留当前有可选信息流的源。

        无可选信息流的源不进 Tab（零订阅的 rss / 内置流全被隐藏的源）：
        空 tab 只有噪音；配置好订阅 / 取消隐藏后 F2 重勾即回来。
        """
        if ids is None:
            made = all_sources()
        else:
            made = [cls() for sid in ids if (cls := get_class(sid)) is not None]
        return [s for s in made if s.feeds]

    async def _start(self) -> None:
        """启动序列：首启（配置文件不存在）先选语言、再引导选源，确认落盘后才进正常渲染。

        语言屏在渲染前完成，选中即 set_language 立即生效（本会话后续文案
        全是新语言）；Esc = 沿用缺省继续，完成首启时随 enabled 一并落定，
        之后不再问。勾选是使用源的前提：引导取消（None）= 放弃本次使用，
        不默认全开。_start 本就在 worker 里，push_screen 的 wait_for_dismiss 合法。
        """
        if not config.is_configured():
            lang = await self.app.push_screen(
                LanguageScreen(highlight=i18n.default_language()), wait_for_dismiss=True
            )
            if lang is not None:
                i18n.set_language(lang)
            chosen = await self.app.push_screen(
                OnboardingScreen(self._setup_entries(), [s.source_id for s in self.sources]),
                wait_for_dismiss=True,
            )
            if chosen is None:
                self.app.exit(message=t("app.exit_onboarding_cancel"))
                return
            config.save_enabled(chosen)
            if config.get_language() is None:
                config.save_language(i18n.language())  # 语言屏被跳过也随首启落定
            # 只重建状态，渲染统一交给 _bootstrap：_start 在 default 组里跑，
            # 若此处走 _apply_enabled 的 action_refresh（default 组 exclusive），
            # 会把还在 await 的自己取消在「旧子项已摘、新子项未挂」的中间态
            self._rebuild_enabled(chosen)
            self._hint_empty_subscribables(chosen)
        await self._bootstrap()

    async def _bootstrap(self) -> None:
        """初始渲染：先回到上次停下的位置（tab 条 / 边栏一画出来高亮就是对的），
        再重建它们（异步，等旧子项摘除），随后才开始抓取。"""
        self.resume.restore()
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
        行文案统一由 source_entry_text 生成（订阅管理返回后的同步也用它）。
        """
        return [
            (source.source_id, text)
            for source in all_sources()
            if (text := source_entry_text(source))
        ]

    def _rebuild_enabled(self, ids: list[str]) -> None:
        """按启用列表重建源列表与选中位（纯状态，不碰 DOM）。

        展示顺序 = ids 顺序（弹屏按勾选顺序产出，落盘 enabled 同序）；尽量停
        在原来的源——前提是它还在 self.sources 里（勾了但零订阅的源会被
        feeds 过滤出局，「停在原源」按过滤后的实际名单找，找不到回 0 位，
        不能按 chosen 找：否则 next() 落空直接崩，保存后的重建全断）。
        """
        chosen = set(ids)
        prev_id = self.sources[self.source_idx].source_id if self.sources else None
        self.sources = self._live_sources(ids)
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
                feedback.info(self, t("main.hint_no_subscriptions", label=cls().label_text))

    # ---- F2 源管理：勾选启用的源，保存即生效 ----

    def action_manage_sources(self) -> None:
        """F2 源管理：勾选启用的源，保存即生效——增加 / 删除 / 修改启用集都走这里。

        走 worker 因为 push_screen 的 wait_for_dismiss 只能在 worker 里用，
        group 独立于列表加载，F2 不打断在途的 _refresh。
        """
        self.run_worker(self._manage_sources(), group="sources", exclusive=True)

    async def _manage_sources(self) -> None:
        current_ids = [s.source_id for s in self.sources]
        chosen = await self.app.push_screen(
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
            feedback.info(self, t("main.saved_n_sources", n=len(chosen)))
        else:
            feedback.info(self, t("main.saved_zero_sources"))

    def _mark_subs_dirty(self) -> None:
        """订阅管理屏回调：改过信息流。F2 收尾（无论确认取消）都要重建。"""
        self._subs_dirty = True

    # ---- Ctrl+L 语言设置：确认落盘，重启后生效 ----

    def action_language(self) -> None:
        """Ctrl+L 语言设置：与首启同一屏，改的是落盘值。

        生效要重启（不是当场切）：解析期已把数字/单位文案烙进数据（语言随
        进程固定是最稳的模型，见 core/i18n 模块头），当场重渲染全 UI 必漏。
        走 worker 因为 push_screen 的 wait_for_dismiss 只能在 worker 里用；
        独立分组不打断在途的列表刷新。
        """
        self.run_worker(self._pick_language(), group="language", exclusive=True)

    async def _pick_language(self) -> None:
        code = await self.app.push_screen(
            LanguageScreen(highlight=i18n.language()), wait_for_dismiss=True
        )
        if code is None:
            return  # 取消：不动盘
        if code == i18n.language():
            # 原语言重选不落盘，但要出声：静默会让「以为切了」的用户误判成功
            feedback.info(self, t("main.language_unchanged", name=i18n.name_of(code)))
            return
        config.save_language(code)
        feedback.info(self, t("main.language_saved_restart"))
