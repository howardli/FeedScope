"""语言选择弹层：首启设置与日常修改（Ctrl+L）共用一屏。

语言未定时无法翻译「选择语言」这件事本身——标题与提示是全 app 唯一的
双语文案（两种语言文件里同值，见 core/i18n/*.toml 的 language 段），
列表行用各语言的自称（[meta] name，永远以该语言书写：简体中文 / English）。
Enter / 点击返回语言代码，Esc 返回 None：首启流程 None = 沿用缺省继续
（完成首启时随 enabled 一并落定，不再反复问），日常修改 None = 不动。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from feedscope.core import i18n
from feedscope.core.i18n import t
from feedscope.ui.focus_nav import ArrowModalScreen


class LanguageScreen(ArrowModalScreen[str | None]):
    """语言选择：dismiss 值为语言代码（如 "zh-CN" / "en"），Esc / 取消 = None。"""

    CSS = """
    #language-dialog { max-width: 56; }
    #language-title { text-style: bold; padding-bottom: 1; }
    #language-list { width: auto; min-width: 36; height: auto; }
    #language-hint { padding-top: 1; color: $text-muted; }
    """

    def __init__(self, highlight: str = "") -> None:
        super().__init__()
        self._langs = i18n.available()
        self._highlight = highlight  # 预选项：首启传系统猜测，日常修改传当前语言

    def compose(self) -> ComposeResult:
        with Vertical(id="language-dialog", classes="modal-card"):
            yield Label(t("language.title"), id="language-title")
            # option id = 语言代码（dismiss 直接回传；代码唯一，id 不撞）
            yield OptionList(
                *[Option(lang.name, id=lang.code) for lang in self._langs],
                id="language-list",
            )
            yield Label(t("language.hint"), id="language-hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        codes = [lang.code for lang in self._langs]
        if self._highlight in codes:
            ol.highlighted = codes.index(self._highlight)
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
