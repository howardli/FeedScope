"""core/display 展示词汇的唯一出处测试：署名 / 计数折万 / 赞文本。

两源接入的同形验证在 test_parsers（需要各源的 fixture）。
"""

from __future__ import annotations

from feedscope.core.display import fmt_count, signature, votes_text


def test_signature() -> None:
    assert signature("宝玉", "dotey") == "宝玉 @dotey"  # 双要素（X/论坛类）
    assert signature("", "dotey") == "@dotey"  # 缺名剩账号
    assert signature("阿财", "") == "阿财"  # 无账号惯例的源（知乎）只传名
    assert signature("", "") == ""


def test_fmt_count_wan_folding() -> None:
    """计数折「万」：整万去 .0，不过万原样（站方按钮同形态）。"""
    assert fmt_count(248) == "248"
    assert fmt_count(9999) == "9999"
    assert fmt_count(10000) == "1 万"
    assert fmt_count(10584) == "1.1 万"
    assert fmt_count(12340000) == "1234 万"


def test_votes_text() -> None:
    assert votes_text(0) == ""  # 0 赞空文本，楼层头不占位
    assert votes_text(3) == "赞 3"
    assert votes_text(45000) == "赞 4.5 万"
