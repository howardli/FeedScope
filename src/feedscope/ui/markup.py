"""markup 字面量化：把用户/第三方文本安全地拼进 Textual 的 markup 字符串。

Textual 的 markup 解析里，开标签的正则是 `(?<!\\)\\[`——**任何前面不是反斜杠
的 `[` 都开标签**，首字母大小写都认；反斜杠本身是字面文本（不消耗），只是让
紧随的 `[` 不再开标签。而 rich.markup.escape 与 textual.markup.escape 的转义
正则只认小写开头的标签（`[a-z#/@]`）：「[AI] 周刊」「[PDF] 论文」这类大写开头
的方括号词不会被转义，上屏即被当样式标签吞掉（标题列、侧栏、源名列都在内）。

本模块的 escape 不猜哪些方括号像标签：给每个 `[` 前面插一个反斜杠，原有
反斜杠原样保留（它们本就是字面量）。唯一的边角是文本以反斜杠结尾——它会
压掉紧随其后的闭合标签，补一个空格隔开（Label 里不可见）。

取舍（tests/test_markup_sinks.py 守着）：
- 纯文本出口优先 markup=False：按构造即安全，不必转义
- 确实要保留 [dim] / [b] 这类有意样式的出口才拼 markup 字符串，其中的动态值
  一律经这里的 escape；ui/ 里不允许再从 rich.markup 导入 escape
"""

from __future__ import annotations


def escape(text: str) -> str:
    """文本 -> markup 字面量：解析后逐字还原（见模块头的解析规则）。"""
    out = text.replace("[", "\\[")
    return out + " " if out.endswith("\\") else out
