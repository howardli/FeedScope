"""显示折行：UAX #14 中文场景的规范子集。

- 汉字之间任意断行
- ASCII 词（字母/数字/常见符号）内部不拆
- 避头（挪字法）：句读/收尾符不落行首，连同上一行末字符一起带下来
- 避尾：开括号/开引号不落行尾，挪到下一行行首

wrap_display(text)：纯文本折行（列表标题）
wrap_markdown(md)：markdown 折行（正文）——内联标记（**粗体**、[链接]()、`代码`、
裸 URL）作为原子不拆；fenced 代码块与标题行原样保留。

终端一格一字符，做不了印刷级的标点压缩/悬挂，挪字法是规范允许的简化。
"""

from __future__ import annotations

import re

from feedscope.core.text import display_width

# 行首禁则：句读、收尾标点（UAX #14 CL/EX 类）
_NO_START = set("。，、；：？！）】》」』〉…％·-～)〕")
# 行尾禁则：开括号、开引号（UAX #14 OP/QU 类）
_NO_END = set("（【《「『〈([{")

# ASCII「词」成分：字母数字 + 混排常见符号；连续出现视为不可拆词
_WORD_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789%+-./#@&=_"
)

# 内联标记原子：粗体/斜体/行内代码/链接/裸 URL
# 链接文本可为空（知乎图片来源的空锚点会生成 [](url)），空文本也算一个原子
_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]*\]\([^)\s]+\)|https?://\S+)"
)
_FENCED_RE = re.compile(r"(```.*?```)", re.DOTALL)  # 带捕获组：split 才会把代码块留在结果里


# ---- 折行引擎：unit = (可见文本, 完整形态)，unit 内部不再拆 ----


def _split_units(visible: str, full: str, breakable: bool) -> list[tuple[str, str]]:
    """breakable 文本切成「单字符 / ASCII 词」unit；标记原子整体一个 unit。"""
    if not breakable:
        return [(visible, full)]
    units: list[tuple[str, str]] = []
    i, n = 0, len(visible)
    while i < n:
        ch = visible[i]
        if ch in _WORD_CHARS:
            j = i
            while j < n and visible[j] in _WORD_CHARS:
                j += 1
            units.append((visible[i:j], visible[i:j]))
            i = j
        else:
            units.append((ch, ch))
            i += 1
    return units


def _wrap_units(units: list[tuple[str, str]], width: int) -> list[str]:
    """贪心装箱 + 避头尾（挪字法），返回完整形态的行。

    比行还宽的原子（长 URL 等）不再硬拆：整只占一行留给 Textual 自己折，
    拆了会把 URL 断成两截，markdown-it 的 linkify 只认得前半截。
    """
    if width <= 0:
        return ["".join(f for _, f in units)]

    lines: list[list[tuple[str, str]]] = [[]]
    w = 0

    def open_line() -> None:
        nonlocal w
        carried: list[tuple[str, str]] = []
        # 避尾：刚关闭的行若以开括号收尾，一并带到新行行首
        while lines[-1] and lines[-1][-1][0] in _NO_END:
            carried.insert(0, lines[-1].pop())
        lines.append(carried)
        w = sum(display_width(v) for v, _ in carried)

    for visible, full in units:
        cw = display_width(visible)
        if w > 0 and w + cw > width:
            if visible[0] in _NO_START and lines[-1]:
                # 避头：句读要落行首 -> 把上一行末 unit 带下来陪它
                carry = lines[-1].pop()
                lines.append([carry])
                w = display_width(carry[0])
            else:
                open_line()
        lines[-1].append((visible, full))
        w += cw

    return ["".join(f for _, f in ln) for ln in lines if ln] or [""]


def wrap_display(text: str, width: int) -> list[str]:
    """纯文本按显示宽度折行；避头尾禁则用挪字法处理。"""
    if width <= 0 or not text:
        return [text]
    return _wrap_units(_split_units(text, text, True), width)


# ---- markdown 折行 ----


def _wrap_inline_line(line: str, width: int) -> list[str]:
    """一条物理行 -> 原子化后折行；内联标记原子不拆（宽度按可见文本计）。"""
    atoms: list[tuple[str, str, bool]] = []
    pos = 0
    for m in _INLINE_RE.finditer(line):
        if m.start() > pos:
            atoms.append((line[pos:m.start()], line[pos:m.start()], True))
        token = m.group(0)
        if token.startswith("["):
            inner = re.match(r"\[([^\]]+)\]", token)
            atoms.append((inner.group(1) if inner else token, token, False))
        else:
            atoms.append((token, token, False))
        pos = m.end()
    if pos < len(line):
        atoms.append((line[pos:], line[pos:], True))

    units: list[tuple[str, str]] = []
    for visible, full, breakable in atoms:
        units.extend(_split_units(visible, full, breakable))
    return _wrap_units(units, width)


def wrap_markdown(md: str, width: int) -> str:
    """markdown 正文折行：段落内避头尾；代码块与标题行原样保留。

    段内行一律接成两空格硬换行（\"  \\n\"）——Textual 的 Markdown 按规范把
    软换行折叠成空格（实测），既会抹掉源文本的真实换行（X 推文分行、引用
    块行），也会把这里的预折行变回一行并留下「样 式」式的假空格；硬换行
    让每一物理行原样上屏。
    """
    if width <= 0 or not md:
        return md
    out_blocks: list[str] = []
    for part in _FENCED_RE.split(md):  # fenced 代码块原样保留
        if part.startswith("```"):
            out_blocks.append(part)
            continue
        for block in re.split(r"\n[ \t]*\n", part):  # 空行分段
            if not block.strip():
                continue
            wrapped: list[str] = []
            for hard_line in block.split("\n"):
                if not hard_line.strip() or hard_line.lstrip().startswith("#"):
                    wrapped.append(hard_line)  # 标题行短，不动
                    continue
                wrapped.extend(_wrap_inline_line(hard_line, width))
            out_blocks.append("  \n".join(wrapped))
    return "\n\n".join(out_blocks)
