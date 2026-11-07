"""全 app 展示词汇的唯一出处：署名、计数、赞文本。

各源数据形态不同（X 的 GraphQL、知乎的 API JSON），但上屏词汇必须同形——
用户在源之间的体验是同一个软件，不是 N 个爬虫的拼接。源的 parse 层只把
原始数值/字段传进来，拼接规则全部收口在这里；新增源照用，别自拼。

词汇表（与 UI 层的既定形态对齐；各源行为见 README 的站点说明）：
- 署名 signature(name, handle)：「名字 @handle」——账号体系双要素的源
  （X/论坛）两样都露；没有 @ 账号惯例的源（知乎，站方 @ 提及用姓名）只传
  name，不硬造站方没有的形态。各语言形态一致，不经 i18n
- 计数 fmt_count：随语言二形——中文过万折「万」保留一位小数（45000 ->
  4.5 万），其余语言按千分位 K/M 折（45000 -> 45K、1234000 -> 1.2M）。
  折法是书写惯例不是文案，代码分支按语言族收在这里（zh* 用万，其余
  K/M——新语言默认进 K/M 形）
- 赞 votes_text：「赞 4.5 万」/「45K likes」；0 赞空文本（与站方按钮同
  形态，楼层头不占位）
"""

from __future__ import annotations

from feedscope.core import i18n
from feedscope.core.i18n import t


def signature(name: str = "", handle: str = "") -> str:
    """作者署名「名字 @handle」：两样都露，缺谁剩谁，全缺空串。"""
    if name and handle:
        return f"{name} @{handle}"
    return f"@{handle}" if handle else name


def _use_wan() -> bool:
    """计数折法用「万」还是 K/M：中文书写惯例用万，其余按 K/M。"""
    return i18n.language().startswith("zh")


def fmt_count(n: int) -> str:
    """整数计数 -> 展示文本（随语言二形，见模块头词汇表）。"""
    if _use_wan():
        return f"{n / 10000:.1f} {t('display.wan')}".replace(".0 ", " ") if n >= 10000 else str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)


def votes_text(n: int) -> str:
    """赞数 -> 楼层头赞文本；0 赞空文本（无赞不占位）。"""
    return t("display.votes", n=fmt_count(n)) if n else ""


def view_count_text(n: int) -> str:
    """知乎详情 meta 的浏览数段（带前导「 · 」分隔符）：「 · 1234 万浏览」。

    「万浏览」是站方复合词（fmt_count 出「1234 万」会多个空格），中文分支
    拼写与站方同形；其余语言走 K/M 计数一条模板。分隔符进模板：语种想换
    分隔形态改文件即可。
    """
    if _use_wan():
        return t("display.visits_wan", n=n // 10000) if n >= 10000 else t("display.visits", n=n)
    return t("display.visits", n=fmt_count(n))
