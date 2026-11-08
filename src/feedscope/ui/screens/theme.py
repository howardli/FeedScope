"""主题选择弹层（Ctrl+T）：镜像语言选择屏的结构，但语义更简单——主题是

纯显示层，Enter 即时应用（App.theme 当场生效）+ 落盘；Esc 取消不动。
行 id = 主题注册名（editor-dark / editor-light / 用户新增的 TOML 名）。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from feedscope.core.i18n import t
from feedscope.ui import theme as theme_mod
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
        self._themes = theme_mod.available()
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
