"""`feedscope rss …`：RSS 订阅管理——list / add / remove / rename / import / export。

不走 Textual，加载轻；业务本体在 services/subscriptions（CLI 与 TUI 订阅管理屏
共用同一份）与 services/opml，这里只管参数与打印。add 也收 OPML 文件路径
（add_from_input 分流）。
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from pathlib import Path

from feedscope.commands.base import SubParsers
from feedscope.core import config
from feedscope.core.errors import BadSubscriptionFile, SubscriptionError
from feedscope.core.i18n import t
from feedscope.services import opml
from feedscope.services import subscriptions as subs

NAME = "rss"

_Action = Callable[[argparse.Namespace], int]


def register(sub: SubParsers) -> None:
    parser = sub.add_parser(NAME, help=t("cli.help_rss"))
    actions = parser.add_subparsers(dest="rss_command", required=True)
    actions.add_parser("list", help=t("cli.help_rss_list"))
    add = actions.add_parser("add", help=t("cli.help_rss_add"))
    add.add_argument("url", help=t("cli.help_rss_add_url"))
    add.add_argument("--key", help=t("cli.help_rss_add_key"))
    add.add_argument("--label", help=t("cli.help_rss_add_label"))
    remove = actions.add_parser("remove", help=t("cli.help_rss_remove"))
    remove.add_argument("key", help=t("cli.help_rss_remove_key"))
    rename = actions.add_parser("rename", help=t("cli.help_rss_rename"))
    rename.add_argument("key", help=t("cli.help_rss_rename_key"))
    rename.add_argument("label", help=t("cli.help_rss_rename_label"))
    import_ = actions.add_parser("import", help=t("cli.help_rss_import"))
    import_.add_argument("path", help=t("cli.help_rss_import_path"))
    export = actions.add_parser("export", help=t("cli.help_rss_export"))
    export.add_argument("path", nargs="?", help=t("cli.help_rss_export_path"))


def _list(args: argparse.Namespace) -> int:
    feeds = config.feeds_for("rss")
    if not feeds:
        print(t("cli.rss_list_empty"))
        return 0
    for f in feeds:
        print(f"{f.key:<16} {f.label}  <{f.url}>")
    print(t("cli.rss_list_total", n=len(feeds)))
    return 0


def _add(args: argparse.Namespace) -> int:
    # add_from_input 只分流 URL / OPML 路径；显式 key/label 仍走 add_subscription。
    # 坏 OPML 文件 = 用法级错误退 2（与 import 同一规则），业务冲突退 1
    try:
        if args.key or args.label:
            feed = asyncio.run(
                subs.add_subscription("rss", args.url, key=args.key, label=args.label)
            )
            summary = t("cli.rss_added", key=feed.key, label=feed.label, url=feed.url)
            added = [feed]
        else:
            added, summary = asyncio.run(subs.add_from_input("rss", args.url))
    except BadSubscriptionFile as exc:
        print(t("cli.not_added", error=exc))
        return 2
    except SubscriptionError as exc:
        print(t("cli.not_added", error=exc))
        return 1
    if len(added) > 1:  # OPML 批量：逐条列出，方便核对
        for f in added:
            print(f"  {f.key:<16} {f.label}  <{f.url}>")
    print(summary + t("cli.restart_hint"))
    return 0


def _remove(args: argparse.Namespace) -> int:
    removed = subs.remove_subscription("rss", args.key)
    if removed is None:
        print(t("cli.no_such_key", key=repr(args.key)))
        return 1
    print(t("cli.removed", key=removed.key, label=removed.label) + t("cli.restart_hint"))
    return 0


def _rename(args: argparse.Namespace) -> int:
    renamed = subs.rename_subscription("rss", args.key, args.label)
    if renamed is None:
        print(t("cli.no_such_key", key=repr(args.key)))
        return 1
    print(t("cli.renamed", key=renamed.key, label=renamed.label) + t("cli.restart_hint"))
    return 0


def _import(args: argparse.Namespace) -> int:
    try:
        added, skipped = opml.import_opml(opml.read_opml_file(args.path), "rss")
    except BadSubscriptionFile as exc:
        print(t("cli.import_failed", error=exc))
        return 2
    for f in added:
        print(t("cli.rss_added", key=f.key, label=f.label, url=f.url))
    for line in skipped[:5]:
        print(t("cli.skipped_line", line=line))
    if len(skipped) > 5:
        print(t("cli.more_skipped", n=len(skipped) - 5))
    print(
        t("cli.import_result", added=len(added), skipped=len(skipped)) + t("cli.restart_hint")
    )
    return 0


def _export(args: argparse.Namespace) -> int:
    # 给路径写文件，缺省 stdout（> 订阅.opml 重定向也行）
    feeds = config.feeds_for("rss")
    text = opml.export_opml(feeds)
    if args.path:
        Path(args.path).write_text(text, encoding="utf-8")
        print(t("cli.exported", n=len(feeds), path=args.path))
    else:
        print(text, end="")
    return 0


# 子子命令分发表（键 = add_parser 的名字）
_ACTIONS: dict[str, _Action] = {
    "list": _list,
    "add": _add,
    "remove": _remove,
    "rename": _rename,
    "import": _import,
    "export": _export,
}


def run(args: argparse.Namespace) -> int:
    return _ACTIONS[args.rss_command](args)
