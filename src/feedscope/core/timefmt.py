"""时间展示格式化：源的原始时间形态 -> 展示时区的「YYYY-MM-DD HH:MM」。

各源原始形态不同（X 是英文 Twitter 串、知乎是页面上的 DOM 文本、HN 是
UTC 的 ISO 串），统一在这里转成同一展示文本。时区默认北京时间（UTC+8）
而非本机时区：跨时区设备（服务器/海外机器）不该看到不一样的时间；可用
配置的 timezone 键（IANA 名）换（set_timezone，启动期一次，仿
i18n.set_language 的「烙进数据」模型——已格式化文本会进列表缓存，换
时区对新抓取生效）。解析失败回空串——时间只是元信息，缺了不致命，
别让一行脏数据炸掉整版内容。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_FMT = "%Y-%m-%d %H:%M"
_DEFAULT_TZ = timezone(timedelta(hours=8))  # 北京时间：固定 UTC+8，无夏令时
_TZ: timezone | ZoneInfo = _DEFAULT_TZ

# fmt_relative 的档位上限（秒）：分钟内直给 0m（不造「刚刚」这类要翻译的词）
_MIN = 60
_HOUR = 3600
_DAY = 86400
_RECENT_DAYS = 14  # 14 天内相对计龄，再久日期更可读


def set_timezone(name: str | None) -> None:
    """配置值 -> 展示时区；缺失/空回默认，非法（IANA 名不存在）记警告回退。

    进程级一次（cli/app 启动期），之后 fmt_* 全走它——与 set_language
    同款的可注入全局，测试可反复调。
    """
    global _TZ
    if not name:
        _TZ = _DEFAULT_TZ
        return
    try:
        _TZ = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("无法识别的时区名 %r，回退默认北京时间（UTC+8）", name)
        _TZ = _DEFAULT_TZ


def _local(dt: datetime) -> str:
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)  # 无时区标记的串：当作展示时区（中文站源）
        return dt.astimezone(_TZ).strftime(_FMT)
    except (OverflowError, OSError, ValueError):
        return ""


def fmt_twitter(s: str | None) -> str:
    """X 的 created_at（"Wed Oct 15 08:23:11 +0000 2025"，UTC）-> 展示文本。"""
    if not s:
        return ""
    try:
        return _local(datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y"))
    except ValueError:
        return ""


def fmt_unix(seconds: float | None) -> str:
    """unix 秒时间戳（知乎 initialData 的 created 等）-> 展示文本。"""
    if not seconds:
        return ""
    try:
        return _local(datetime.fromtimestamp(int(seconds), tz=_TZ))
    except (OverflowError, OSError, ValueError):
        return ""


def fmt_iso(s: str | None) -> str:
    """ISO 8601 串（HN Algolia 的 created_at，UTC 带 Z）-> 展示文本。"""
    if not s:
        return ""
    try:
        return _local(datetime.fromisoformat(s))
    except (OverflowError, OSError, ValueError):
        return ""


def iso_epoch(s: str | None) -> float | None:
    """ISO 8601 串 -> unix 秒（列表 published 填充用，telegram/HN）。

    缺失/解析失败 None；无时区标记的串当 UTC（机器格式），与 _local 的
    「无标记当展示时区」（人写的中文站文本）口径不同，别混用。
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def twitter_epoch(s: str | None) -> float | None:
    """X 的 created_at 串 -> unix 秒；缺失/解析失败 None（列表 published 填充用）。"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp()
    except ValueError:
        return None


def fmt_relative(seconds: float | None, now: float | None = None) -> str:
    """unix 秒 -> 列表时间列的紧凑相对文本（最长 5 列，纯数字档位不进词表）。

    档位：N 分钟内 0m · 一小时内 Nm · 一天内 Nh · 14 天内 Nd · 同年
    MM-DD · 跨年 YY-MM——列宽一致优先于精度，就地取整不进位。未来时间
    （钟差/源侧脏数据）钳成 0m；解析失败回空串（列内右对齐空占位）。
    now 可注入（测试），缺省取当前钟。
    """
    if not seconds:
        return ""
    now = time.time() if now is None else now
    try:
        dt = datetime.fromtimestamp(seconds, tz=_TZ)
        now_dt = datetime.fromtimestamp(now, tz=_TZ)
    except (OverflowError, OSError, ValueError):
        return ""
    delta = (now_dt - dt).total_seconds()
    if delta < _MIN:
        return "0m"
    if delta < _HOUR:
        return f"{int(delta // _MIN)}m"
    if delta < _DAY:
        return f"{int(delta // _HOUR)}h"
    if delta < _RECENT_DAYS * _DAY:
        return f"{int(delta // _DAY)}d"
    return dt.strftime("%m-%d") if dt.year == now_dt.year else dt.strftime("%y-%m")
