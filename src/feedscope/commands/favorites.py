"""`feedscope favorites [export [path]]`：收藏列表。

无子命令 = 明文列表；export = Markdown（防锁定，同 rss export）。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from feedscope.commands.base import SubParsers
from feedscope.core.i18n import t
from feedscope.core.models import FeedItem
from feedscope.core.store import ReadState

NAME = "favorites"


def register(sub: SubParsers) -> None:
    parser = sub.add_parser(NAME, help=t("cli.help_favorites"))
    actions = parser.add_subparsers(dest="fav_command")
    export = actions.add_parser("export", help=t("cli.help_favorites_export"))
    export.add_argument("path", nargs="?", help=t("cli.help_favorites_export_path"))


def _date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _export(pairs: list[tuple[float, FeedItem]], path: str | None) -> int:
    # Markdown 链接的方括号/圆括号会破坏语法，标题与 URL 做最小转义
    lines = [
        f"- [{it.title.replace('[', '(').replace(']', ')')}]"
        f"({it.url.replace('(', '%28').replace(')', '%29')}) — {it.source}，{_date(ts)}"
        for ts, it in pairs
    ]
    md = "\n".join(lines) + "\n" if lines else ""
    if path:
        Path(path).write_text(md, encoding="utf-8")
        print(t("cli.favorites_exported", n=len(pairs), path=path))
    else:
        print(md, end="")
    return 0


def run(args: argparse.Namespace) -> int:
    pairs = ReadState().favorites()
    if args.fav_command != "export":
        if not pairs:
            print(t("cli.favorites_empty"))
            return 0
        for ts, it in pairs:
            print(f"{it.source:<10} {_date(ts)}  {it.title}  <{it.url}>")
        print(t("fav.status_count", n=len(pairs)))
        return 0
    return _export(pairs, args.path)
