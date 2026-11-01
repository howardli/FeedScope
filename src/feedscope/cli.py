"""命令行入口：`feedscope` 启动 TUI；子命令 login / probe / doctor。"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feedscope", description="FeedScope（观流）- 终端信息流聚合阅读器"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("tui", help="启动 TUI（默认）")

    login = sub.add_parser("login", help="打开浏览器窗口登录某个源")
    login.add_argument("source", nargs="?", default="zhihu", help="源 id（默认 zhihu）")

    probe = sub.add_parser("probe", help="巡检全部源（改版诊断 + 原始样本落盘）")
    probe.add_argument(
        "sources", nargs="*", help="只测这些源 id（缺省全部；未登录的源报 SKIP）"
    )

    sub.add_parser("doctor", help="环境自检（Chrome / profile / 用户配置）")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in (None, "tui"):
        # 惰性导入：让 doctor / login / probe 子命令不用加载 Textual
        from feedscope.ui.app import FeedScopeApp

        FeedScopeApp().run()
        return 0

    if args.command == "login":
        import asyncio

        from feedscope.login import login

        return 0 if asyncio.run(login(args.source)) else 1

    if args.command == "probe":
        from feedscope.probe import run_probes

        return run_probes(args.sources)

    if args.command == "doctor":
        from feedscope.doctor import run_checks

        return run_checks()

    return 0
