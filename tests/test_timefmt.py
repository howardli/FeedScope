"""时间格式化基线：Twitter 串 -> 北京时间（固定 UTC+8）「YYYY-MM-DD HH:MM」。

期望值直接写死：时区固定后输出是确定值，与环境时区无关（CI/海外机器同结果）；
脏数据一律回空串。
"""

from __future__ import annotations

from feedscope.core.timefmt import fmt_twitter, fmt_unix


def test_fmt_twitter_parses_english_utc_string() -> None:
    assert fmt_twitter("Wed Oct 15 08:23:11 +0000 2025") == "2025-10-15 16:23"
    assert fmt_twitter("随时") == ""
    assert fmt_twitter(None) == ""


def test_fmt_unix_converts_to_bjt() -> None:
    assert fmt_unix(1735689600) == "2025-01-01 08:00"  # 2025-01-01T00:00Z -> 北京 08:00
    assert fmt_unix(0) == ""
    assert fmt_unix(None) == ""
    assert fmt_unix(10**18) == ""  # 脏数据：溢出回空串
