"""`feedscope login [source]`：有头 Chrome 手动登录（流程本体在 services/login）。"""

from __future__ import annotations

import argparse
import asyncio

from feedscope.commands.base import SubParsers
from feedscope.core.i18n import t
from feedscope.services import login

NAME = "login"


def register(sub: SubParsers) -> None:
    parser = sub.add_parser(NAME, help=t("cli.help_login"))
    parser.add_argument("source", nargs="?", default="zhihu", help=t("cli.help_login_source"))


def run(args: argparse.Namespace) -> int:
    return 0 if asyncio.run(login.login(args.source)) else 1
