"""弹层底座与轻弹层：居中卡片 + 按钮行的骨架只写一次。

之前是三胞胎（账号框 / 确认框 / 输入框各抄一份 compose 与底座 CSS）；
差异本来只有三件事——提示文案、确认按钮的词、要不要多一个输入框。
卡片 CSS 与 Esc 取消在 ArrowModalScreen 唯一实现（.modal-card +
action_cancel，各弹层永远同形），本模块只剩轻弹层自己的增量。
"""

from __future__ import annotations

from typing import Any, TypeVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label

from feedscope.core.i18n import t
from feedscope.ui import feedback
from feedscope.ui.focus_nav import ArrowModalScreen

_DialogT = TypeVar("_DialogT")


class DialogScreen(ArrowModalScreen[_DialogT]):
    """居中卡片弹层底座（泛型 = dismiss 值类型）：卡片宽度约束 + 提示行样式；
    compose 与取消值留给子类（Confirm -> bool，Input -> str | None）。"""

    DEFAULT_CSS = """
    .dialog-card { max-width: 64; }
    .dialog-prompt { padding-bottom: 1; }
    """

    def action_cancel(self) -> None:
        raise NotImplementedError  # 子类声明自己的取消值（False / None）


class ConfirmScreen(DialogScreen[bool]):
    """轻确认弹框：True=确认，Esc/取消=False。

    PROMPT_ID / OK_ID 是语义化 id 的挂点（账号框覆写成 account-*，
    测试与排错好认），按钮 id 只在屏内寻址、跨屏不撞。
    """

    PROMPT_ID = "confirm-prompt"
    OK_ID = "confirm-ok"

    def __init__(self, prompt: str, confirm_label: str = "") -> None:
        # confirm_label 空串 = 默认键（构造时解析：类体默认值在 import 期，语言未定）
        confirm_label = confirm_label or t("dialogs.confirm")
        super().__init__()
        self._prompt = prompt
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-card dialog-card"):
            # markup=False：提示是纯文本（会拼入订阅名等用户数据），按构造即不做
            # 标记解析——调用方不必记得逐处转义（tests/test_markup_sinks.py 守着）
            yield Label(self._prompt, id=self.PROMPT_ID, classes="dialog-prompt", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button(self._confirm_label, id=self.OK_ID, variant="primary")
                yield Button(t("dialogs.cancel"), id="confirm-cancel")

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

    def __init__(self, label: str, action_key: str) -> None:
        # action_key 是文案键（dialogs.login / dialogs.logout）：动作词与提示语一次解析
        action = t(action_key)
        prompt = (
            t("dialogs.login_prompt", label=label)
            if action_key == "dialogs.login"
            else t("dialogs.logout_prompt", label=label)
        )
        super().__init__(prompt, confirm_label=action)


class InputScreen(DialogScreen[str | None]):
    """单行输入弹层：Enter 确定 / Esc 取消，返回文本（空 = None）。

    全应用唯一的文本输入（粘贴走终端原生 bracketed paste，从 Finder 拖
    文件进终端即得路径）；Input 自己消费字符键（←/→ 是文本光标），↑/↓
    在输入行与按钮行间移动焦点（ArrowModalScreen）。
    allow_empty=True 时空串回 ""（与取消的 None 可区分）——「空提交 =
    清除」语义的调用方用（标题过滤）；默认 False 沿用「空 = 取消」。
    """

    CSS = """
    .dialog-fixed { width: 64; }  /* 输入行要 1fr：卡片定宽，输入行才有得铺 */
    """

    def __init__(
        self, title: str, *, initial: str = "", placeholder: str = "", allow_empty: bool = False
    ) -> None:
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder
        self._allow_empty = allow_empty

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-card dialog-card dialog-fixed"):
            yield Label(self._title, id="input-title", classes="dialog-prompt", markup=False)
            yield Input(value=self._initial, placeholder=self._placeholder, id="input-line")
            with Horizontal(classes="modal-actions"):
                yield Button(t("dialogs.ok"), id="input-ok", variant="primary")
                yield Button(t("dialogs.cancel"), id="input-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-line", Input).focus()

    def _dismiss_value(self, raw: str) -> str | None:
        text = raw.strip()
        return text if text or self._allow_empty else None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._dismiss_value(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "input-ok":
            self.dismiss(self._dismiss_value(self.query_one("#input-line", Input).value))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


async def ask_title_filter(holder: Screen[Any], current: str | None) -> str | None:
    """/ 过滤词问答（主屏与收藏屏同一条流）：弹输入层 -> 归一化新词。

    返回 None = 不动现有过滤（Esc 取消 / 词没变——取消输入 ≠ 清除过滤），
    空串归 None（= 清除）；词变了顺手把 set/clear 的 toast 弹掉（toast 出口
    是纯文本，见 feedback 模块头——用户词原样给，不转义）。
    """
    q = await holder.app.push_screen(
        # allow_empty：空提交回 ""（= 清除过滤），None 只留给 Esc（= 不变）
        InputScreen(t("main.filter_prompt"), initial=current or "", allow_empty=True),
        wait_for_dismiss=True,
    )
    if q is None:
        return current
    new = q.lower() or None
    if new != current:
        feedback.info(
            holder,
            t("main.toast_filter_set", q=new) if new else t("main.toast_filter_clear"),
        )
    return new
