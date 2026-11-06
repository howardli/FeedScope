"""命令行入口：`feedscope` 启动 TUI；子命令 login / probe / doctor / rss。"""

from __future__ import annotations

import argparse
from pathlib import Path


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

    rss = sub.add_parser("rss", help="RSS 订阅管理（list / add / remove / import / export）")
    rss_sub = rss.add_subparsers(dest="rss_command", required=True)
    rss_sub.add_parser("list", help="列出全部订阅")
    add_p = rss_sub.add_parser("add", help="添加订阅（显示名缺省拉一次 feed 标题）")
    add_p.add_argument("url", help="RSS / Atom 地址、站点首页（自动发现 feed）或 OPML 文件路径")
    add_p.add_argument("--key", help="订阅 key（缺省按域名生成）")
    add_p.add_argument("--label", help="显示名（缺省用 feed 自述标题）")
    remove_p = rss_sub.add_parser("remove", help="删除订阅")
    remove_p.add_argument("key", help="要删的订阅 key（feedscope rss list 查看）")
    import_p = rss_sub.add_parser("import", help="从 OPML 批量导入（幂等合并，已订阅的跳过）")
    import_p.add_argument("path", help="OPML 文件路径")
    export_p = rss_sub.add_parser("export", help="导出订阅为 OPML 2.0")
    export_p.add_argument(
        "path", nargs="?", help="写入的文件路径（缺省打印到 stdout，可重定向）"
    )

    return parser


def _run_rss(args: argparse.Namespace) -> int:
    """rss 子命令组分发：订阅管理不走 Textual，加载轻。"""
    import asyncio

    from feedscope import subscriptions as subs
    from feedscope.core import config

    if args.rss_command == "list":
        feeds = config.rss_feeds()
        if not feeds:
            print("还没有订阅：feedscope rss add <地址> 或 import <OPML 文件>")
            return 0
        for f in feeds:
            print(f"{f.key:<16} {f.label}  <{f.url}>")
        print(f"共 {len(feeds)} 条订阅")
        return 0

    if args.rss_command == "add":
        # add_from_input 只分流 URL / OPML 路径；显式 key/label 仍走 add_feed。
        # 坏 OPML 文件 = 用法级错误退 2（与 import 同一规则），业务冲突退 1
        try:
            if args.key or args.label:
                feed = asyncio.run(subs.add_feed(args.url, key=args.key, label=args.label))
                added, summary = [feed], f"已订阅 {feed.key}：{feed.label} <{feed.url}>"
            else:
                added, summary = asyncio.run(subs.add_from_input(args.url))
        except subs.BadSubscriptionFile as exc:
            print(f"未添加：{exc}")
            return 2
        except subs.SubscriptionError as exc:
            print(f"未添加：{exc}")
            return 1
        if len(added) > 1:  # OPML 批量：逐条列出，方便核对
            for f in added:
                print(f"  {f.key:<16} {f.label}  <{f.url}>")
        print(f"{summary}（重启 TUI 生效）")
        return 0

    if args.rss_command == "remove":
        removed = subs.remove_feed(args.key)
        if removed is None:
            print(f"没有 key 为 {args.key!r} 的订阅（feedscope rss list 查看）")
            return 1
        print(f"已删除 {removed.key}：{removed.label}（重启 TUI 生效）")
        return 0

    if args.rss_command == "import":
        try:
            added, skipped = subs.import_opml(subs.read_opml_file(args.path))
        except subs.BadSubscriptionFile as exc:
            print(f"导入失败：{exc}")
            return 2
        for f in added:
            print(f"已订阅 {f.key}：{f.label} <{f.url}>")
        for line in skipped[:5]:
            print(f"跳过 {line}")
        if len(skipped) > 5:
            print(f"…另有 {len(skipped) - 5} 条跳过")
        print(f"导入 {len(added)} 条，跳过 {len(skipped)} 条（重启 TUI 生效）")
        return 0

    # export：给路径写文件，缺省 stdout（> 订阅.opml 重定向也行）
    opml = subs.export_opml(config.rss_feeds())
    if args.path:
        Path(args.path).write_text(opml, encoding="utf-8")
        print(f"已导出 {len(config.rss_feeds())} 条订阅到 {args.path}")
    else:
        print(opml, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in (None, "tui"):
        # 惰性导入：让 doctor / login / probe / rss 子命令不用加载 Textual
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

    if args.command == "rss":
        return _run_rss(args)

    return 0
