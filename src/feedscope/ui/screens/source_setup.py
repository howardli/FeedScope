"""源选择弹层：首启引导 + 日常源管理（F2）。

两屏同一套 SelectionList 勾选交互（空格切换）：确认返回勾选的源 id 列表
（**按勾选顺序**——展示顺序 = 勾选顺序，后勾上的排末尾；勾选行实时标
1、2、3… 序号 = 站点排列位置），取消 / Esc 返回 None——引导里 None =
放弃使用（App 退出），管理里 None = 不落盘。候选与初始勾选由 App 注入，
屏不读写配置：选择生效路径（保存 + 重建站点栏）集中在 App（_start /
action_manage_sources）。

可订阅源（Source.subscribable，目前 rss、reddit 与 telegram）在此层还有
二级入口：→ 进订阅管理屏（SubsStore 按源分派），改动即时落盘；返回后
这里只同步该源勾选（有订阅 = 勾上，空 = 取消）并回调 App 标脏。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, SelectionList
from textual.widgets.selection_list import Selection

from feedscope.core.registry import get_class
from feedscope.subscriptions import store_for
from feedscope.ui import feedback
from feedscope.ui.focus_nav import ArrowModalScreen, FocusNavSelectionList
from feedscope.ui.screens.subscriptions import SubscriptionsScreen


class SourceSetupScreen(ArrowModalScreen[list[str] | None]):
    """源勾选弹层基类：样式与交互共用，标题/文案/确认键由子类给出。"""

    title_text: ClassVar[str] = ""
    prompt_text: ClassVar[str] = ""
    confirm_label: ClassVar[str] = "确认"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", show=False),
        # 二级入口走 →（与主屏 → 同构）：列表上按 = 高亮源的订阅管理，
        # 按钮行上按 = 右移焦点（action_nav_right 分派）。Enter 不能用：
        # SelectionList 把它截停转成勾选切换（widget 内 event.stop()），
        # 屏级绑定与事件路径都压不过——留给勾选
    ]

    CSS = """
    #setup-dialog {
        width: auto; max-width: 64;
        background: $surface; border: ascii $primary; padding: 1 2;
    }
    #setup-title { text-style: bold; padding-bottom: 1; }
    #setup-prompt { padding-bottom: 1; }
    #setup-list { width: auto; min-width: 44; height: auto; max-height: 14; margin-bottom: 1; }
    #setup-actions { height: auto; }
    #setup-actions Button { margin-right: 1; }
    """

    def __init__(
        self,
        entries: list[tuple[str, str]],
        initial_ids: list[str],
        on_feeds_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._entries = list(entries)  # (source_id, 行文案)，注册顺序即列表顺序
        self._labels: dict[str, str] = dict(entries)  # sid -> 不带序号的基础文案
        self._initial = set(initial_ids)
        self._on_feeds_changed = on_feeds_changed  # 订阅管理屏改过东西：App 标脏
        # 勾选顺序（= 展示顺序）。初始勾选不触发 SelectionToggled（Textual 只对
        # 显式切换发消息），沿用传入顺序；之后的每次切换在 handler 里维护。
        self._order: list[str] = list(initial_ids)

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Label(self.title_text, id="setup-title")
            yield Label(self.prompt_text, id="setup-prompt")
            yield FocusNavSelectionList(
                # option id = source id：改行文案（编号）时按 id 定位
                *[Selection(text, sid, sid in self._initial, id=sid) for sid, text in self._entries]
            )
            with Horizontal(id="setup-actions"):
                yield Button(self.confirm_label, id="setup-confirm", variant="primary")
                yield Button("取消", id="setup-cancel")

    def on_mount(self) -> None:
        self._renumber()  # 初始勾选也要带序号

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        """维护勾选顺序：勾上排到末尾、取消即移出（再勾上 = 挪到末尾，这就是调序动线）。

        _toggle 先改状态后发消息，handler 里 selected 已是新状态，按成员关系
        分支即可，不必依赖事件本身携带方向。
        """
        sid = event.selection.value
        if sid in event.selection_list.selected:
            if sid not in self._order:
                self._order.append(sid)
        else:
            self._order = [s for s in self._order if s != sid]
        self._renumber()

    def _renumber(self) -> None:
        """给勾选的行标序号（= 站点排列位置），未勾选的留空对齐。

        序号随切换重排（取消 1 号后，原 2 号自动变 1）；列表行本身不动
        （注册顺序稳定，好找），只变文案。
        """
        sl = self.query_one(SelectionList)
        pos = {sid: i + 1 for i, sid in enumerate(self.selected_ids)}
        for sid, label in self._labels.items():
            n = pos.get(sid)
            sl.replace_option_prompt(sid, f"{n:>2} {label}" if n else f"   {label}")

    @property
    def selected_ids(self) -> list[str]:
        """勾选的源 id，按勾选顺序返回（初始勾选沿用传入顺序）。"""
        chosen = set(self.query_one(SelectionList).selected)
        return [sid for sid in self._order if sid in chosen]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-confirm":
            self.dismiss(self.selected_ids)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---- →：可订阅源的二级管理（订阅增删改名）----

    def action_nav_right(self) -> None:
        """→ 焦点感知分派：列表上 = 高亮源的订阅管理（原语义），按钮行 = 右移。"""
        if isinstance(self.focused, SelectionList):
            self.action_manage_feeds()
            return
        super().action_nav_right()

    def action_manage_feeds(self) -> None:
        """→：进高亮源的订阅管理屏；不可订阅的源按了要给反馈，不静默。"""
        sl = self.query_one(SelectionList)
        idx = sl.highlighted
        if idx is None or not 0 <= idx < len(self._entries):
            return
        sid = self._entries[idx][0]
        cls = get_class(sid)
        if cls is None or not cls.subscribable:
            feedback.info(
                self.app, f"{cls.label if cls else sid} 的信息流是内置的，没有可管理的订阅"
            )
            return
        self.run_worker(self._manage_feeds(sid, cls))

    async def _manage_feeds(self, sid: str, cls: type) -> None:
        changed = await self.app.push_screen(
            SubscriptionsScreen(store_for(sid)), wait_for_dismiss=True
        )
        if not changed:
            return
        self._sync_row(sid)
        if self._on_feeds_changed is not None:
            self._on_feeds_changed()

    def _sync_row(self, sid: str) -> None:
        """订阅管理返回后同步该源勾选：还有可选信息流 = 勾上（加订阅显然想
        用，与 CLI 的 enabled 联动同语义）；删空 = 取消勾选。"""
        cls = get_class(sid)
        if cls is None:
            return
        want = bool(cls().feeds)
        sl = self.query_one(SelectionList)
        if want and sid not in sl.selected:
            sl.select(sid)
            if sid not in self._order:
                self._order.append(sid)
        elif not want and sid in sl.selected:
            sl.deselect(sid)
            if sid in self._order:
                self._order.remove(sid)
        self._renumber()


class OnboardingScreen(SourceSetupScreen):
    """首启引导：勾选要启用的源，确认后进入主界面。"""

    title_text = "欢迎使用观流（FeedScope）"
    prompt_text = "选择要启用的信息源（空格勾选 / 取消）；序号即站点排列顺序"
    confirm_label = "开始使用"


class ManageSourcesScreen(SourceSetupScreen):
    """F2 源管理：勾选 = 启用、取消勾选 = 停用，保存后立即生效。"""

    title_text = "源管理"
    prompt_text = "空格/Enter 勾选；序号即站点顺序，取消再勾选 = 移到末尾；→ 管理订阅"
    confirm_label = "保存"
