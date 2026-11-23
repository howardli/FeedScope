"""正文链接选择器：阅读屏家族里纯键盘跟进链接的出口。

[图片]/[视频]/@提及/原文 这些链接在 Markdown 控件里只能鼠标点（Textual 的
链接是 @click 动作，没有键盘入口）——对一个全键位非打印键、输入法友好的
键盘优先应用，纯键盘用户看 X 时间线里的图片只能 Ctrl+O 整条丢给浏览器。
这里把它们按出现顺序列出来：↑↓ 选、Enter 在浏览器打开、Esc 返回。
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from feedscope.core.i18n import t
from feedscope.ui import Bindings
from feedscope.ui.focus_nav import ArrowModalScreen, FocusNavListView
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.markup import escape


class _LinkListView(FocusNavListView):
    """Enter 归屏分派（与主列表 / 收藏列表同一纪律）。"""

    BINDINGS: ClassVar[Bindings] = [
        Binding("enter", "screen.nav_enter", "bindings.links_open", show=False),
    ]


class LinkPickerScreen(ArrowModalScreen[str | None]):
    """正文里的链接选一个：dismiss(地址)；Esc / 没选 dismiss(None)。"""

    CSS = """
    #links-dialog { width: auto; min-width: 60; max-width: 96; height: auto; }
    #links-list { height: auto; max-height: 16; }
    .link-row { padding: 0 1; }
    #links-hint { padding-top: 1; color: $text-muted; }
    """

    def __init__(self, links: list[tuple[str, str]]) -> None:
        super().__init__()
        self._links = links

    def compose(self) -> ComposeResult:
        with Vertical(id="links-dialog", classes="modal-card"):
            yield Label(t("links.title"), id="links-title", markup=False)
            yield _LinkListView(
                *[
                    ListItem(
                        Static(
                            f"{escape(text)}  [dim]{escape(url)}[/dim]",
                            classes="link-row",  # 链接文字与地址都是第三方内容：逐个转义
                        )
                    )
                    for text, url in self._links
                ],
                id="links-list",
            )
            yield Label(t("links.hint"), id="links-hint", markup=False)

    async def on_mount(self) -> None:
        retranslate_bindings(self)
        retranslate_bindings(self.query_one("#links-list", _LinkListView))
        self.query_one("#links-list", ListView).focus()

    def action_nav_enter(self) -> None:
        self.query_one("#links-list", ListView).action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._links):
            self.dismiss(self._links[idx][1])

    def action_cancel(self) -> None:
        self.dismiss(None)
