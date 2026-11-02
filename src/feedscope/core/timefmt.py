"""时间展示格式化：源的原始时间形态 -> 北京时间（UTC+8）「YYYY-MM-DD HH:MM」。

各源原始形态不同（X 是英文 Twitter 串、知乎是页面上的 DOM 文本、HN 是
UTC 的 ISO 串），统一在这里转成同一展示文本。一律折算成固定的北京时间而非本机时区：
跨时区设备（服务器/海外机器）不该看到不一样的时间。解析失败
回空串——时间只是元信息，缺了不致命，别让一行脏数据炸掉整版内容。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_FMT = "%Y-%m-%d %H:%M"
_BJT = timezone(timedelta(hours=8))  # 北京时间：固定 UTC+8，无夏令时


def _bj(dt: datetime) -> str:
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BJT)  # 无时区标记的串：当作北京时间（中文站源）
        return dt.astimezone(_BJT).strftime(_FMT)
    except (OverflowError, OSError, ValueError):
        return ""


def fmt_twitter(s: str | None) -> str:
    """X 的 created_at（"Wed Oct 15 08:23:11 +0000 2025"，UTC）-> 北京时间展示文本。"""
    if not s:
        return ""
    try:
        return _bj(datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y"))
    except ValueError:
        return ""


def fmt_unix(seconds: float | None) -> str:
    """unix 秒时间戳（知乎 initialData 的 created 等）-> 北京时间展示文本。"""
    if not seconds:
        return ""
    try:
        return _bj(datetime.fromtimestamp(int(seconds), tz=_BJT))
    except (OverflowError, OSError, ValueError):
        return ""


def fmt_iso(s: str | None) -> str:
    """ISO 8601 串（HN Algolia 的 created_at，UTC 带 Z）-> 北京时间展示文本。"""
    if not s:
        return ""
    try:
        return _bj(datetime.fromisoformat(s))
    except (OverflowError, OSError, ValueError):
        return ""
