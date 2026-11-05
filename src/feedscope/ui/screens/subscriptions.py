"""订阅管理屏：F2 里 → 进入，列表 + 增删改名，改动即时落盘（源无关）。

- 键位全非打印：↑↓ 选择、Enter 改名（走 ListView 的 Selected，与主列表
  同一条路径）、Insert 添加、Delete 删除、Esc 返回
- 添加框是全应用唯一的文本输入：可输入的形态因源而异（rss 收 feed 地址 /
  站点首页 / OPML 路径，reddit 收 subreddit 名或地址），文案与分流都在
  各源的 SubsStore 里，本屏零源特判
- 改动即时落盘（config.update_feeds 原子写）：订阅是数据不是待确认的草稿，
  Esc 只退出不回滚；删除有轻确认，误删可重新添加（显示名会再拉一次）
- 业务编排全部复用顶层 subscriptions 模块，本屏只做展示与键位；源的差异
  收在 SubsStore 适配器（Source.subscribable 门控入口 + store_for 分派）
"""

from __future__ import annotations

from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, ListItem, ListView, Static

from feedscope.core.config import Subscription  # 仅类型注解用（列表行的 key/label/url 三段）
from feedscope.subscriptions import SubscriptionError, SubsStore
from feedscope.ui import feedback
from feedscope.ui.dialogs import ConfirmScreen, InputScreen
from feedscope.ui.focus_nav import ArrowModalScreen, FocusNavListView


class SubscriptionsScreen(ArrowModalScreen[bool]):
    """订阅管理：dismiss(True) = 本次有过改动（调用方据此同步勾选与刷新）。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "返回"),
        Binding("insert", "add", "添加"),
        Binding("delete", "remove", "删除"),
    ]

    CSS = """
    #subs-dialog {
        width: auto; min-width: 64; max-width: 96; height: auto;
        background: $surface; border: ascii $primary; padding: 1 2;
    }
    #subs-title { text-style: bold; padding-bottom: 1; }
    #subs-list { width: 1fr; height: auto; max-height: 14; padding: 0 1; }
    #subs-empty { padding: 1 0; color: $text-muted; }
    #subs-hint { padding-top: 1; color: $text-muted; }
    #subs-actions { height: auto; margin-top: 1; }
    #subs-actions Button { margin-right: 1; }
    """

    def __init__(self, store: SubsStore) -> None:
        super().__init__()
        self._store = store
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="subs-dialog"):
            yield Label(f"{self._store.label} 订阅管理", id="subs-title")
            yield FocusNavListView(id="subs-list")
            yield Label("", id="subs-empty")
            yield Label(self._store.hint, id="subs-hint")
            with Horizontal(id="subs-actions"):
                yield Button("添加", id="subs-add", variant="primary")
                yield Button("改名", id="subs-rename")
                yield Button("删除", id="subs-remove")

    async def on_mount(self) -> None:
        await self._rebuild()

    # ---- 列表 ----

    async def _rebuild(self) -> None:
        """按当前配置重建列表（每条操作后调用），保持选中位尽量不动。"""
        view = self.query_one("#subs-list", ListView)
        idx = view.index
        await view.remove_children()
        feeds = self._store.feeds()
        if feeds:
            await view.mount(
                *[
                    ListItem(
                        Static(
                            f"{escape(f.key)}  [b]{escape(f.label)}[/b]\n[dim]{escape(f.url)}[/dim]"
                        )
                    )
                    for f in feeds
                ]
            )
            view.index = 0 if idx is None else min(idx, len(feeds) - 1)
            view.display = True
            self.query_one("#subs-empty", Label).update("")
        else:
            view.display = False
            self.query_one("#subs-empty", Label).update(self._store.empty)
        view.focus()

    def _current(self) -> Subscription | None:
        """当前选中的订阅（列表顺序 = 配置顺序，索引即位）。"""
        view = self.query_one("#subs-list", ListView)
        feeds = self._store.feeds()
        if view.index is None or not feeds or view.index >= len(feeds):
            return None
        return feeds[view.index]

    # ---- 动作（键盘与按钮共用路径）----

    def action_add(self) -> None:
        self.run_worker(self._add_flow())

    async def _add_flow(self) -> None:
        text = await self.app.push_screen(
            InputScreen("添加订阅", placeholder=self._store.add_placeholder),
            wait_for_dismiss=True,
        )
        if not text:
            return
        try:
            _added, summary = await self._store.add(text)
        except SubscriptionError as exc:
            feedback.warn(self.app, str(exc), title="添加失败")
            return
        except Exception as exc:  # 网络等意外：订阅管理没有 Source 实例，不走 feedback.fail
            feedback.warn(self.app, f"添加失败：{exc}", title="添加失败")
            return
        self._changed = True
        await self._rebuild()
        feedback.info(self.app, summary)

    def action_remove(self) -> None:
        feed = self._current()
        if feed is None:
            feedback.info(self.app, "先选中一条订阅")
            return
        self.run_worker(self._remove_flow(feed.key, feed.label))

    async def _remove_flow(self, key: str, label: str) -> None:
        if not await self.app.push_screen(
            ConfirmScreen(f"删除订阅「{label}」？", confirm_label="删除"), wait_for_dismiss=True
        ):
            return
        if self._store.remove(key) is not None:
            self._changed = True
            await self._rebuild()
            feedback.info(self.app, f"已删除 {label}（想看回来可重新添加）")

    def action_rename(self) -> None:
        feed = self._current()
        if feed is None:
            feedback.info(self.app, "先选中一条订阅")
            return
        self.run_worker(self._rename_flow(feed.key, feed.label))

    async def _rename_flow(self, key: str, label: str) -> None:
        text = await self.app.push_screen(
            InputScreen("改显示名", initial=label), wait_for_dismiss=True
        )
        if not text:
            return
        if self._store.rename(key, text) is None:
            feedback.info(self.app, "名字没变（空名或订阅已不存在）")
            return
        self._changed = True
        await self._rebuild()
        feedback.info(self.app, f"已改名为 {text}")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "subs-list":
            self.action_rename()  # Enter = 改名（与主列表 Enter 进详情同一事件路径）

    def on_button_pressed(self, event: Button.Pressed) -> None:
        by_id = {
            "subs-add": self.action_add,
            "subs-remove": self.action_remove,
            "subs-rename": self.action_rename,
        }
        if handler := by_id.get(event.button.id or ""):
            handler()

    def action_back(self) -> None:
        self.dismiss(self._changed)
