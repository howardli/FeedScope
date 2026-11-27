"""`feedscope`（无子命令）/ `feedscope tui`：拉起终端界面。"""

from __future__ import annotations

import argparse

from feedscope.commands.base import SubParsers
from feedscope.core.i18n import t

NAME = "tui"


def register(sub: SubParsers) -> None:
    sub.add_parser(NAME, help=t("cli.help_tui"))


def run(args: argparse.Namespace) -> int:
    # Textual 只在真要起界面时才加载：其余子命令走纯命令行路径
    from feedscope.ui.app import FeedScopeApp

    FeedScopeApp().run()
    return 0
