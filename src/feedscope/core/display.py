"""全 app 展示词汇的唯一出处：署名、计数、赞文本。

各源数据形态不同（X 的 GraphQL、知乎的 API JSON），但上屏词汇必须同形——
用户在源之间的体验是同一个软件，不是 N 个爬虫的拼接。源的 parse 层只把
原始数值/字段传进来，拼接规则全部收口在这里；新增源照用，别自拼。

词汇表（与 UI 层的既定形态对齐，约定详见 README 架构节）：
- 署名 signature(name, handle)：「名字 @handle」——账号体系双要素的源
  （X/论坛）两样都露；没有 @ 账号惯例的源（知乎，站方 @ 提及用姓名）只传
  name，不硬造站方没有的形态
- 计数 fmt_count：过万折「万」保留一位小数（45000 -> 4.5 万、12340000 ->
  1234 万），对齐站方按钮形态
- 赞 votes_text：「赞 4.5 万」；0 赞空文本（与站方按钮同形态，楼层头不占位）
"""

from __future__ import annotations


def signature(name: str = "", handle: str = "") -> str:
    """作者署名「名字 @handle」：两样都露，缺谁剩谁，全缺空串。"""
    if name and handle:
        return f"{name} @{handle}"
    return f"@{handle}" if handle else name


def fmt_count(n: int) -> str:
    """整数计数 -> 展示文本：过万折「万」（保留一位小数，整数万不带尾零）。"""
    return f"{n / 10000:.1f} 万".replace(".0 ", " ") if n >= 10000 else str(n)


def votes_text(n: int) -> str:
    """赞数 -> 楼层头赞文本；0 赞空文本（无赞不占位）。"""
    return f"赞 {fmt_count(n)}" if n else ""
