"""ui/wrap.py 纯函数折行引擎的基线测试（离线，无网络）。"""

from feedscope.core.text import display_width
from feedscope.ui.wrap import wrap_display, wrap_markdown


def test_display_width() -> None:
    assert display_width("中a") == 3  # 全宽 2 + 半宽 1
    assert display_width("") == 0


def test_wrap_display_basic() -> None:
    assert wrap_display("一二三四", 4) == ["一二", "三四"]


def test_wrap_display_avoid_head() -> None:
    # 句号不落行首：连带上一行末字一起带下来
    assert wrap_display("一二三。", 4) == ["一二", "三。"]


def test_wrap_display_avoid_tail() -> None:
    # 开括号不落行尾：挪到下一行行首
    assert wrap_display("你好（", 4) == ["你好", "（"]


def test_wrap_display_ascii_word_kept() -> None:
    # ASCII 词内部不拆
    lines = wrap_display("中文word中文", 6)
    assert any("word" in line for line in lines)


def test_wrap_display_zero_width_passthrough() -> None:
    # 宽度非法时原样返回，不抛异常
    assert wrap_display("abc", 0) == ["abc"]


def test_wrap_display_empty() -> None:
    assert wrap_display("", 5) == [""]


def test_wrap_markdown_keeps_url_atom() -> None:
    md = "文字文字文字 https://example.com/a/very/long/path 文字"
    out = wrap_markdown(md, 10)
    url_lines = [line for line in out.split("\n") if "example.com" in line]
    assert url_lines, "URL 不应消失"
    for line in url_lines:
        assert "https://example.com/a/very/long/path" in line  # URL 整体不截断


def test_wrap_markdown_bold_atom() -> None:
    # 粗体标记是原子：比行宽也不拆（否则 markdown 渲染会碎）
    out = wrap_markdown("**粗体标记整体不拆**", 4)
    assert "**粗体标记整体不拆**" in out


def test_wrap_markdown_fenced_and_heading_untouched() -> None:
    md = "# 标题行\n\n```python\ncode line\n```\n\n正文内容"
    assert wrap_markdown(md, 10) == md  # 标题/代码块原样保留，正文够短不动


def test_wrap_markdown_hard_break_between_lines() -> None:
    # 段内行接成两空格硬换行：Textual 的 Markdown 把软换行折叠成空格（实测），
    # 既要保住源文本的真实换行（X 推文分行），也要让预折行真正断行而非留假空格
    out = wrap_markdown("第一行\n第二行", 40)
    assert out == "第一行  \n第二行"
    # 预折行同样走硬换行（4 格 = 每行 2 个汉字）
    out = wrap_markdown("一二三四五六", 4)
    assert out == "一二  \n三四  \n五六"
