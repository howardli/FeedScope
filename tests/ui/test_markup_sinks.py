"""markup 出口门禁 + 回归：用户/第三方数据不得被当成 Rich 标记解析。

Textual 的 Label / Static 默认 markup=True，notify 默认 markup=True：拼进去的
文本会被当成 Rich 标记——「[AI] 周刊」这种订阅名被当样式标签吞掉，含闭合
标记的直接抛 MarkupError。0.4.1 修的是「花括号经 t() 崩溃」与三处调用点，
同一类问题仍从 4 个出口漏网（侧栏项 / 列表源名列 / 删除确认框 / toast）。

约定（静态门禁 test_markup_sinks_are_safe_by_construction 守着）：
1. 纯文本出口一律 markup=False——弹层提示、toast、空态提示：按构造即安全，
   调用方不必记得逐处转义
2. 保留 markup 的出口（要 [dim] / [b] 这类有意样式），表达式里的每个动态值
   必须是「洁净」的：已 escape()、数字/常量、词汇表文案属性，或插值全洁净
   的 t()
3. 两者都不满足的少数出口进 _INTENTIONAL_MARKUP 豁免清单，逐条写明转义落
   在哪里——新增豁免必须给理由，这是防线不是绕道

Header 副标题不做标记解析（下方有用例钉住），不在门禁范围：赋给它的文本
原样显示，转义反而会露出反斜杠。
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Header, Label, Static

from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.source import Source
from feedscope.ui import feedback
from feedscope.ui.dialogs import ConfirmScreen
from feedscope.ui.list_render import build_item
from feedscope.ui.markup import escape
from feedscope.ui.widgets import FeedNavItem
from tests.support.tui import last_notification

UI = Path(__file__).resolve().parents[2] / "src" / "feedscope" / "ui"

# 三种敌意文本：标签被吞 / 解析抛错 / 被改样式
SWALLOWED = "[AI] Weekly"
CLOSER = "[/] feed"
RESTYLED = "[b]bold[/b] feed"
HOSTILE = (SWALLOWED, CLOSER, RESTYLED)


# ---- 行为回归：四个出口 + Header 副标题 ----


def _plain(widget: Static) -> str:
    """控件实际渲染出的纯文本（visual 即 Rich Text）。"""
    v = widget.visual
    return (getattr(v, "rich", None) or v).plain


class _Host(App[None]):
    def __init__(self, widgets: list[Static]) -> None:
        super().__init__()
        self._widgets = widgets

    def compose(self) -> ComposeResult:
        yield from self._widgets


class _Stub(Source):
    source_id = "markup_stub"
    label = "stub"

    async def open_feed(self, name: str) -> Source:  # type: ignore[override]
        raise NotImplementedError  # 只借元信息列词汇，不开流


def _row(tag: str, title: str = "t", meta: str = "") -> Static:
    item = FeedItem(source="rss", id="k/1", title=title, url="https://e.com", tag=tag, meta=meta)
    return build_item(item, 40, 12, _Stub(), tag=tag, tag_width=30)


def _run(flow: Callable[[], Awaitable[None]]) -> None:
    """项目惯例：同步用例里 asyncio.run 一段动线（同 test_favorites）。"""
    asyncio.run(flow())


def test_list_columns_treat_data_as_plain_text() -> None:
    """列表行三列都是用户/第三方数据：源名列（订阅名）、标题列（帖子标题）、
    元信息列（作者名）。大写开头的方括号词是重点——rich.markup.escape 漏转它。"""
    _run(_row_flow)


async def _row_flow() -> None:
    app = _Host([_row(tag, title=tag, meta=tag) for tag in HOSTILE])
    async with app.run_test(size=(120, 10)):
        for selector in (".item-tag", ".item-title", ".item-score"):
            assert [_plain(w) for w in app.query(selector)] == list(HOSTILE), selector


def test_sidebar_feed_item_treats_label_as_plain_text() -> None:
    _run(_sidebar_flow)


async def _sidebar_flow() -> None:
    items = [
        FeedNavItem(i, tag, active=False, unread=12 if i == 2 else 0)
        for i, tag in enumerate(HOSTILE)
    ]
    app = _Host(items)
    async with app.run_test(size=(60, 8)):
        shown = [_plain(w) for w in app.query(".feed-nav-item")]
    assert shown == [SWALLOWED, CLOSER, f"{RESTYLED} 12"]


def test_confirm_dialog_prompt_treats_label_as_plain_text() -> None:
    """真实调用路径：订阅管理屏把订阅名拼进删除确认文案（subscriptions.remove_confirm）。"""
    _run(_confirm_flow)


async def _confirm_flow() -> None:
    prompt = t("subscriptions.remove_confirm", label=CLOSER)
    app = _Host([])
    async with app.run_test(size=(60, 10)) as pilot:
        await app.push_screen(ConfirmScreen(prompt))
        await pilot.pause()
        assert CLOSER in _plain(app.screen.query_one(f"#{ConfirmScreen.PROMPT_ID}", Label))


def test_toast_treats_message_as_plain_text() -> None:
    _run(_toast_flow)


async def _toast_flow() -> None:
    app = _Host([])
    async with app.run_test(size=(60, 10)) as pilot:
        for msg in HOSTILE:
            feedback.info(app, f"removed {msg}")
            await pilot.pause()  # toast 渲染出错会在退出 run_test 时抛出
            assert msg in last_notification(app)


def test_header_sub_title_does_not_parse_markup() -> None:
    """Header 副标题原样显示：状态行里的过滤词不能再 escape（会露出反斜杠）。"""
    _run(_header_flow)


async def _header_flow() -> None:
    class _WithHeader(App[None]):
        def compose(self) -> ComposeResult:
            yield Header()

    app = _WithHeader()
    async with app.run_test(size=(80, 6)) as pilot:
        for text in (*HOSTILE, "a\\[b"):
            app.sub_title = text
            await pilot.pause()
            shown = _plain(app.query_one(Header).query_one("HeaderTitle", Static))
            assert shown.endswith(text), shown


# ---- 转义函数本身 ----

_ROUND_TRIP = (
    SWALLOWED,  # 大写开头：rich.markup.escape 漏转的那一类
    "[pdf] paper",  # 小写开头
    CLOSER,
    RESTYLED,
    "a {b} c",
    "[周刊] 中文",
    r"end \\",  # 尾部反斜杠会压掉闭合标签
    r"tag\[x]",
    r"two \\ [y]",
    "",
    "plain",
)


def test_escape_round_trips_through_markup_parser() -> None:
    for text in _ROUND_TRIP:
        content = Content.from_markup(f"[dim]{escape(text)}[/dim]")
        assert content.plain.rstrip(" ") == text, text
        assert [str(s.style) for s in content.spans] == ["dim"] * bool(text), text


def test_ui_escape_comes_from_the_project_helper() -> None:
    """ui/ 里 escape 只能来自 feedscope.ui.markup（rich/textual 自带的那份漏转大写开头的方括号词）。"""
    offenders = [
        f"{path.relative_to(UI)}:{node.lineno} {node.module}"
        for path in sorted(UI.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module in {"rich.markup", "textual.markup"}
    ]
    assert not offenders, (
        "请改用 feedscope.ui.markup.escape（逐个转义所有左方括号）：\n" + "\n".join(offenders)
    )


# ---- 静态门禁：ui/ 里的 markup 出口按构造即安全 ----

# 词汇表文案属性：源声明经 t() 解析后的展示文本，不含用户数据
_VOCAB_ATTRS = {"label_text", "detail_unit", "comment_unit", "sub_noun_text", "_unit"}
_INERT_CALLS = {"escape", "len", "int"}


def _inert(node: ast.AST) -> bool:
    """值本身不可能带标记：常量 / 已转义 / 数字 / 词汇表文案。"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Attribute):
        return node.attr in _VOCAB_ATTRS
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in _INERT_CALLS
    if isinstance(node, ast.IfExp):
        return _inert(node.body) and _inert(node.orelse)
    if isinstance(node, ast.BinOp):
        return _inert(node.left) and _inert(node.right)
    return False


def _scope_fn(tree: ast.AST, node: ast.AST) -> ast.AST | None:
    """出口所在的最内层函数（局部变量的赋值只在这里找）。"""
    best: ast.AST | None = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            n.lineno <= node.lineno <= (n.end_lineno or n.lineno)
            and (best is None or n.lineno >= best.lineno)  # type: ignore[attr-defined]
        ):
            best = n
    return best


def _numeric_param(name: str, scope: ast.AST) -> bool:
    """name 是 scope（函数）里标注成 int/bool/float 的形参。"""
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = scope.args
    return any(
        a.arg == name
        and isinstance(a.annotation, ast.Name)
        and a.annotation.id in {"int", "bool", "float"}
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    )


def _clean(
    node: ast.AST, scope: ast.AST | None = None, seen: frozenset[str] = frozenset()
) -> bool:
    """表达式里的每个动态值都洁净（可以安全地进 markup 出口）。

    局部变量看它在所在函数里的全部赋值：title = escape(...) 这类预转义的局部
    变量算洁净，不必进豁免清单；seen 防自引用。"""
    if _inert(node):
        return True
    if isinstance(node, ast.Name):
        if scope is None or node.id in seen:
            return False
        if _numeric_param(node.id, scope):  # 标了数字类型的形参：角标/计数
            return True
        assigned = [
            a.value
            for a in ast.walk(scope)
            if isinstance(a, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == node.id for t in a.targets)
        ]
        return bool(assigned) and all(_clean(v, scope, seen | {node.id}) for v in assigned)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"t", "raw"}  # 首参是文案键，其余全是插值
    ):
        return all(_inert(a) for a in node.args[1:]) and all(
            _inert(k.value) for k in node.keywords
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    ):
        return bool(node.args) and _clean_seq(node.args[0], scope, seen)
    if isinstance(node, ast.JoinedStr):
        return all(
            _clean(v.value, scope, seen)
            if isinstance(v, ast.FormattedValue)
            else isinstance(v, ast.Constant)
            for v in node.values
        )
    if isinstance(node, ast.BinOp):
        return _clean(node.left, scope, seen) and _clean(node.right, scope, seen)
    if isinstance(node, ast.IfExp):
        return _clean(node.body, scope, seen) and _clean(node.orelse, scope, seen)
    return False


def _clean_seq(node: ast.AST, scope: ast.AST | None, seen: frozenset[str]) -> bool:
    """可迭代对象的每个元素都洁净（sep.join(...) 用）：字面量序列或推导式的元素表达式。"""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_clean(e, scope, seen) for e in node.elts)
    if isinstance(node, ast.GeneratorExp):
        return _clean(node.elt, scope, seen)
    return False


def _markup_sinks(tree: ast.AST) -> Iterator[ast.Call]:
    """三类 markup 出口：Label/Static 构造、Static/Label 直接子类的 super().__init__、notify。"""
    subclass_ranges = [
        (n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id in {"Static", "Label"} for b in n.bases)
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Name) and f.id in {"Label", "Static"}) or (
            isinstance(f, ast.Attribute)
            and f.attr == "__init__"
            and isinstance(f.value, ast.Call)  # super() 本身是个调用
            and isinstance(f.value.func, ast.Name)
            and f.value.func.id == "super"
            and any(start <= node.lineno <= end for start, end in subclass_ranges)
        ) or (isinstance(f, ast.Attribute) and f.attr == "notify"):
            yield node


def _site(tree: ast.AST, node: ast.Call) -> str:
    """出口所在的「类.函数」（模块级函数只有函数名）：最内层的那个。"""
    cls = fn = ""
    best = -1
    for n in ast.walk(tree):
        if (
            isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.lineno <= node.lineno <= (n.end_lineno or n.lineno)
            and n.lineno >= best
        ):
            best = n.lineno
            cls, fn = (n.name, "") if isinstance(n, ast.ClassDef) else (cls, n.name)
    return f"{cls}.{fn}" if cls and fn else fn or cls


# 豁免清单：(相对 ui/ 的路径, "类.函数", 出口的实参表达式) -> 理由（转义落在哪里）。
# 精确到单个出口：同一函数里新增的出口不沾光；被豁免的表达式一改，陈旧检查就会
# 要求重新审一遍。新增必须给理由——这是防线不是绕道。
_INTENTIONAL_MARKUP: dict[tuple[str, str, str], str] = {
    ("list_render.py", "build_item", "meta_col(source, it)"): (
        "元信息文本在 meta_col 内部逐条 escape 后包 [dim]"
    ),
    ("list_render.py", "build_item", "mark"): "未读圆点：调用方只传空串或 '●'",
    ("list_render.py", "build_item", "fmt_relative(it.published)"): "相对时间只有数字与 m/h/d/-",
    ("screens/reading.py", "floor_head", "'  '.join(parts)"): (
        "author/fields 在函数内逐个 escape，lead 是 t() 文案；parts 靠 append 累加，门禁追不到"
    ),
}


def test_markup_sinks_are_safe_by_construction() -> None:
    offenders: list[str] = []
    used: set[tuple[str, str, str]] = set()
    for path in sorted(UI.rglob("*.py")):
        rel = str(path.relative_to(UI))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _markup_sinks(tree):
            where = (rel, _site(tree, node))
            off = any(
                k.arg == "markup" and isinstance(k.value, ast.Constant) and k.value.value is False
                for k in node.keywords
            )
            shown = ast.unparse(node.args[0]) if node.args else ""
            if off or (node.args and _clean(node.args[0], _scope_fn(tree, node))):
                continue
            exempt = (where[0], where[1], shown)
            if exempt in _INTENTIONAL_MARKUP:
                used.add(exempt)
                continue
            offenders.append(f"{rel}:{node.lineno} [{where[1]}] {shown[:60]}")
    assert not offenders, (
        "markup 出口拿到了未转义的动态值（纯文本出口请传 markup=False；要保留样式的"
        "请对动态值 escape()；确属例外再进 _INTENTIONAL_MARKUP 并写明理由）：\n"
        + "\n".join(offenders)
    )
    stale = set(_INTENTIONAL_MARKUP) - used
    assert not stale, f"豁免清单里有已不再需要的条目，请删除或按新写法重新审视：{sorted(stale)}"
