"""主题色彩契约：正文里「编辑器蓝只给链接」。

背景：Textual 缺省把 Markdown 链接派生成前景灰，灰底灰字认不出可点。
Markdown 标题的统一格式（抹平成段落样式，所有源同形）在 app.py 的 CSS
规则里，按渲染行为验证见 test_tui_smoke（MarkdownH1/H6 与段落同色）。
"""

from __future__ import annotations

from feedscope.ui.theme import FEEDSCOPE_THEME


def test_link_color_is_the_only_blue() -> None:
    """链接 = 编辑器蓝（primary）：正文里唯一用蓝色标记的东西。"""
    vs = FEEDSCOPE_THEME.to_color_system().generate()
    assert vs["link-color"] == "#569cd6", "链接要可认，蓝色只出现在这里"
