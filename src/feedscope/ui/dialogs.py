"""弹层底座与轻弹层：居中卡片 + 按钮行的骨架只写一次。

之前是三胞胎（账号框 / 确认框 / 输入框各抄一份 compose 与底座 CSS）；
差异本来只有三件事——提示文案、确认按钮的词、要不要多一个输入框。
卡片 CSS 与 Esc 取消在此唯一实现，各弹层永远同形。
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label

from feedscope.ui.focus_nav import ArrowModalScreen


class DialogScreen(ArrowModalScreen[object]):
    """居中卡片弹层底座：共享卡片 CSS 与 Esc 取消；compose 留给子类。"""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "取消", show=False)]

    CSS = """
    .dialog-card {
        width: auto; max-width: 64; height: auto;
        background: $surface; border: ascii $primary; padding: 1 2;
    }
    .dialog-prompt { padding-bottom: 1; }
    .dialog-actions { height: auto; }
    .dialog-actions Button { margin-right: 1; }
    """

    def action_cancel(self) -> None:
        raise NotImplementedError  # 子类声明自己的取消值（False / None）


class ConfirmScreen(DialogScreen):
    """轻确认弹框：True=确认，Esc/取消=False。

    PROMPT_ID / OK_ID 是语义化 id 的挂点（账号框覆写成 account-*，
    测试与排错好认），按钮 id 只在屏内寻址、跨屏不撞。
    """

    PROMPT_ID = "confirm-prompt"
    OK_ID = "confirm-ok"

    def __init__(self, prompt: str, confirm_label: str = "确认") -> None:
        super().__init__()
        self._prompt = prompt
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-card"):
            yield Label(self._prompt, id=self.PROMPT_ID, classes="dialog-prompt")
            with Horizontal(classes="dialog-actions"):
                yield Button(self._confirm_label, id=self.OK_ID, variant="primary")
                yield Button("取消", id="confirm-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == self.OK_ID)

    def action_cancel(self) -> None:
        self.dismiss(False)


class AccountScreen(ConfirmScreen):
    """账号操作弹框：登录 / 登出一个源。返回 True=确认、False=取消。

    只在「选择」阶段可见；确认后即 dismiss，真正的有头登录 / 清 cookie 由
    后台 worker 执行（期间 profile 锁冲突由登录进行中标志挡刷新兜底）。
    """

    PROMPT_ID = "account-prompt"
    OK_ID = "account-go"

    def __init__(self, label: str, action: str) -> None:
        prompt = f"{label}需要登录" if action == "登录" else f"登出 {label}？"
        super().__init__(prompt, confirm_label=action)


class InputScreen(DialogScreen):
    """单行输入弹层：Enter 确定 / Esc 取消，返回文本（空 = None）。

    全应用唯一的文本输入（粘贴走终端原生 bracketed paste，从 Finder 拖
    文件进终端即得路径）；Input 自己消费字符键（←/→ 是文本光标），↑/↓
    在输入行与按钮行间移动焦点（ArrowModalScreen）。
    """

    CSS = """
    .dialog-fixed { width: 64; }  /* 输入行要 1fr：卡片定宽，输入行才有得铺 */
    """

    def __init__(self, title: str, *, initial: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-card dialog-fixed"):
            yield Label(self._title, id="input-title", classes="dialog-prompt")
            yield Input(value=self._initial, placeholder=self._placeholder, id="input-line")
            with Horizontal(classes="dialog-actions"):
                yield Button("确定", id="input-ok", variant="primary")
                yield Button("取消", id="input-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-line", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "input-ok":
            self.dismiss(self.query_one("#input-line", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
