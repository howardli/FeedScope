"""主题选择弹层（Ctrl+T）：镜像语言选择屏的结构，但语义更简单——主题是

纯显示层，Enter 即时应用（App.theme 当场生效）+ 落盘；Esc 取消不动。
行 id = 主题注册名（editor-dark / editor-light / 用户新增的 TOML 名）。
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from feedscope.core import config
from feedscope.core.i18n import t
from feedscope.ui import feedback, themes
from feedscope.ui.focus_nav import ArrowModalScreen


class ThemeScreen(ArrowModalScreen[str | None]):
    """主题选择：dismiss 值为主题名，Esc / 取消 = None（不动当前主题）。"""

    CSS = """
    #theme-dialog { max-width: 56; }
    #theme-title { text-style: bold; padding-bottom: 1; }
    #theme-list { width: auto; min-width: 36; height: auto; }
    #theme-hint { padding-top: 1; color: $text-muted; }
    """

    def __init__(self, highlight: str = "") -> None:
        super().__init__()
        self._themes = themes.available()
        self._highlight = highlight  # 预选当前主题（回车即重选 = 无变化提示）

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog", classes="modal-card"):
            yield Label(t("theme.title"), id="theme-title")
            yield OptionList(
                *[Option(th.name, id=th.name) for th in self._themes], id="theme-list"
            )
            yield Label(t("theme.hint"), id="theme-hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        names = [th.name for th in self._themes]
        if self._highlight in names:
            ol.highlighted = names.index(self._highlight)
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


async def pick_theme(holder: Screen[Any]) -> None:
    """Ctrl+T 主题选择动线（主屏的 action_theme 只负责起 worker）：弹选择屏 →
    选定即时换肤 + 落盘；取消或重选当前主题不动。对照 Ctrl+L 语言的「重启生效」：
    主题是纯显示层。wait_for_dismiss 只能在 worker 里用。"""
    name = await holder.app.push_screen(
        ThemeScreen(highlight=holder.app.theme), wait_for_dismiss=True
    )
    if not name or name == holder.app.theme:
        return
    holder.app.theme = name  # Textual 即时应用全屏换肤
    config.save_theme(name)
    feedback.info(holder, t("theme.toast_applied", name=name))
