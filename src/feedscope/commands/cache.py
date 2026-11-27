"""`feedscope cache [clear]`：列表缓存运维——无子命令 = 体检（目录/文件数/字节），clear = 清空。

缓存是纯派生数据（下次刷新重建），clear 无损——不需要确认。
"""

from __future__ import annotations

import argparse

from feedscope.commands.base import SubParsers
from feedscope.core import feed_cache
from feedscope.core.i18n import t

NAME = "cache"


def register(sub: SubParsers) -> None:
    parser = sub.add_parser(NAME, help=t("cli.help_cache"))
    actions = parser.add_subparsers(dest="cache_command")
    actions.add_parser("clear", help=t("cli.help_cache_clear"))


def run(args: argparse.Namespace) -> int:
    if args.cache_command == "clear":
        removed = feed_cache.clear_cache()
        print(t("cli.cache_cleared", n=removed))
        return 0
    n, size = feed_cache.cache_stats()
    print(f"{feed_cache.cache_dir()}")
    print(t("cli.cache_status", n=n, size=size))
    return 0
