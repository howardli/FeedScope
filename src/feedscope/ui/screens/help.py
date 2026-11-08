"""帮助弹层（F1）：全局 + 按任务分组的当前屏键位表。

页脚只亮 F1（帮助入口自身必须可发现），其余键的去处是这里。行内容从各屏
的键位声明自动生成——加键不需要记得改帮助页，永不失同步。要点：

- 只收**本项目声明**的键（沿 MRO 扫 feedscope.* 类自己的 __dict__），描述取
  活注册表里挂载期解析后的最终文案——Textual 屏级继承默认（tab=Focus Next
  之类，实际已被列表控件压制的死键）不进帮助页
- 按**动作**分组排布（导航 → 内容 → 已读与未读 → 收藏 → 配置，见
  _ACTION_GROUP）：分组挂在动作名而不是键位上，键改绑分组跟着走；空组不
  出现，任何屏都是同一套条理
- 键名按全表最长对齐成列（终端等宽，直接补空格），说明列起点整齐
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label

from feedscope.core.i18n import has, t
from feedscope.ui import Bindings
from feedscope.ui.focus_nav import ArrowModalScreen

# 分组排布（组序即展示序）+ 动作名归类。归类挂动作不挂键：换篇/换楼层与
# 边栏导航键不同但同属「导航」，键改绑（如收藏换键）分组不漂移
_GROUP_ORDER = ("nav", "content", "read", "fav", "settings")
_ACTION_GROUP: dict[str, str] = {
    # 导航：切站 / 区域移动 / 进出层级 / 换篇换楼层
    "screen.next_source": "nav", "screen.prev_source": "nav",
    "nav_left": "nav", "nav_right": "nav", "nav_up": "nav", "nav_down": "nav",
    "nav_enter": "nav", "nav_back": "nav", "back": "nav", "cancel": "nav",
    "pick_answer": "nav", "next_article": "nav", "prev_article": "nav",
    "next_answer": "nav", "prev_answer": "nav", "close": "nav",
    # 内容：取数与外带
    "refresh": "content", "more": "content", "open_browser": "content",
    "copy_link": "content",
    # 已读与未读
    "toggle_read": "read", "mark_all_read": "read", "unread_only": "read",
    # 收藏
    "favorite": "fav", "unfavorite": "fav", "favorites": "fav",
    # 配置
    "manage_sources": "settings", "account": "settings",
    "language": "settings", "theme": "settings",
}


def global_rows() -> list[tuple[str, str]]:
    """全局段：App 级键 + 帮助自身。"""
    return [
        ("f1", t("bindings.help")),
        ("ctrl+q", t("bindings.quit")),
        ("ctrl+c ×2", t("help.ctrl_c_fallback")),
    ]


def screen_rows(holder, *extra, skip: set[str] | None = None) -> list[tuple[str, str, str]]:  # noqa: ANN001
    """收本项目声明的键（holder 与 extra 控件都扫），(键, 文案, 动作) 按首见序。

    同一个键在活注册表里可能是**列表**（Textual 继承默认与我们的声明并存，
    如 tab：focus_next 与 next_source）——按 action 匹配取我们那条的最终文案
    （含运行期重标的源词汇表），声明期的原始键兜底 t() 解析。同名键先到
    定序、空描述可被后到的非空覆盖（控件级 up/down 声明为空占位、屏级才
    有描述——先扫控件时不能把描述顶掉）。
    """
    collected: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    skip = skip or set()
    for h in (holder, *extra):
        live = h._bindings.key_to_bindings
        for b in _declared_bindings(h):
            if b.key in skip:
                continue
            match = next((lb for lb in live.get(b.key, []) if lb.action == b.action), None)
            desc = str(match.description if match else b.description)
            if has(desc):  # 类体声明期的原始键（未经 retranslate 的路径）
                desc = t(desc)
            if b.key not in collected:
                collected[b.key] = (desc, b.action)
                order.append(b.key)
            elif not collected[b.key][0] and desc:
                collected[b.key] = (desc, b.action)
    return [(key, *collected[key]) for key in order if collected[key][0]]


def group_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """(键, 文案, 动作) -> 按任务分组的段；组序固定，空组与未归类不出现。

    未归类的动作（理论上只有未来新增键忘了归类）并入「内容」组兜底——
    比悄悄消失好。
    """
    buckets: dict[str, list[tuple[str, str]]] = {g: [] for g in _GROUP_ORDER}
    for key, desc, action in rows:
        buckets[_ACTION_GROUP.get(action, "content")].append((key, desc))
    return [
        (t(f"help.group_{g}"), rows_)
        for g in _GROUP_ORDER
        if (rows_ := buckets[g])
    ]


def _declared_bindings(holder) -> list[Binding]:  # noqa: ANN001
    """沿 MRO 收 feedscope.* 类声明的键位（声明序、按键去重），滤掉继承默认。

    只认类自己 __dict__ 里的 BINDINGS：getattr 会在没声明键位的类上顺着
    MRO 拿到 Textual 基类的列表（模块过滤的是类名，属性却是继承来的），
    Focus Next / Copy selected text 之类的死键就是从这漏进来的。
    """
    out: list[Binding] = []
    keys: set[str] = set()
    for klass in type(holder).__mro__:
        if not getattr(klass, "__module__", "").startswith("feedscope."):
            continue
        for b in klass.__dict__.get("BINDINGS") or []:
            if b.key not in keys:
                keys.add(b.key)
                out.append(b)
    return out


class HelpScreen(ArrowModalScreen[None]):
    """键位帮助：Esc / F1 / Enter 关闭。sections = [(段名, [(键, 文案)...])...]。"""

    # max-height 30：常见终端（38+ 行）下全表直出；更矮的屏退化为弹层内
    # 滚动（组序固定，折叠的是尾部配置组）
    CSS = """
    #help-dialog { width: auto; min-width: 56; max-width: 88; height: auto; }
    #help-scroll { height: auto; max-height: 30; }
    .help-section { text-style: bold; padding: 1 0 0 0; }
    .help-row { padding: 0 1; }
    #help-hint { padding-top: 1; color: $text-muted; }
    """

    BINDINGS: ClassVar[Bindings] = [
        Binding("f1", "close", "bindings.help", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, sections: list[tuple[str, list[tuple[str, str]]]]) -> None:
        super().__init__()
        self._sections = sections

    def compose(self) -> ComposeResult:
        # 键列按全表最长对齐（键都是 ASCII，等宽补空格即成列）
        width = max(len(key) for _, rows in self._sections for key, _ in rows)
        with Vertical(id="help-dialog", classes="modal-card"):
            with VerticalScroll(id="help-scroll"):
                yield Label(t("help.title"), id="help-title", classes="help-section")
                for title, rows in self._sections:
                    yield Label(title, classes="help-section")
                    for key, desc in rows:
                        yield Label(
                            f"[b]{key.ljust(width)}[/b]  {desc}", classes="help-row"
                        )
            yield Label(t("help.close_hint"), id="help-hint")

    def on_mount(self) -> None:
        from feedscope.ui.labels import retranslate_bindings

        retranslate_bindings(self)  # 本屏自己的 f1/enter 键式文案解析
        self.query_one("#help-scroll", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
