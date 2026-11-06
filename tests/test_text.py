"""core/text.py 的直接单测：显示宽度 / 截断 / 零宽清理 / CJK 判定。

此前只经 ui.wrap 转口与解析器 fixture 间接覆盖（换实现就跟着漂），
规则微妙（全宽 2 列、省略号语义、is_cjk 的区间族）值得一份基线。
"""

from __future__ import annotations

from feedscope.core.text import (
    char_width,
    display_width,
    is_cjk,
    strip_zwsp,
    truncate_display,
)


def test_char_width_fullwidth_vs_halfwidth() -> None:
    assert char_width("中") == 2  # 全宽（F/W）
    assert char_width("ａ") == 2  # 全角形式
    assert char_width("a") == 1
    assert char_width(" ") == 1
    assert char_width("。") == 2  # 中文标点


def test_display_width_sums_cells() -> None:
    assert display_width("") == 0
    assert display_width("中a") == 3
    assert display_width("abe") == 3
    assert display_width("中😀a") == 5  # emoji 非 F/W 记 1（与终端实现的宽表无关，规则自洽即可）


def test_truncate_display_noop_within_limit() -> None:
    assert truncate_display("短文", 10) == "短文"
    assert truncate_display("", 5) == ""


def test_truncate_display_by_cells_with_ellipsis() -> None:
    # 限 5：吃进「中a中」（2+1+2=5）补省略号；第 4 个字符（a）放不下就断
    assert truncate_display("中a中a中", 5) == "中a中…"
    # 恰好等于限宽不加省略号
    assert truncate_display("中a中", 5) == "中a中"
    # 奇数限宽遇全宽字符：不硬塞半格，断在上一个全宽字符
    assert truncate_display("中中", 3) == "中…"


def test_strip_zwsp_removes_and_strips() -> None:
    assert strip_zwsp("a\u200bb") == "ab"
    assert strip_zwsp("  文本 \u200b") == "文本"
    assert strip_zwsp("") == ""
    assert strip_zwsp(None) == ""  # 野数据（None）容错为空串


def test_is_cjk_ranges() -> None:
    assert is_cjk("中")  # 表意文字（U+4E00 段）
    assert is_cjk("㐀")  # 扩展 A（U+3400 段）
    assert is_cjk("，")  # 中文标点（U+3000 段）
    assert is_cjk("Ａ")  # 全角形式（U+FF00 段）
    assert not is_cjk("a")
    assert not is_cjk(" ")
    assert not is_cjk("😀")
    assert not is_cjk("𠀀")  # 扩展 B（U+20000，代理区外）不在判定范围
