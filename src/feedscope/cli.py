"""命令行入口：`feedscope` 启动 TUI；子命令 login / probe / doctor / rss / favorites。"""

from __future__ import annotations

import argparse
from pathlib import Path

from feedscope.core.i18n import t


def _early_logging(argv: list[str]) -> None:
    """语言/配置初始化**之前**的先行日志装配：手动预扫 argv。

    顺序是鸡蛋问题：语言按配置设定（要读配置，坏配置要记日志）而 argparse
    的 help 又要等语言——预扫只认 `--verbose` 与 `--log-file PATH` 两种
    长旗标形态（`--log-file=PATH` 看不见，正式 parse 后 force 换位补齐），
    让 config 读取期的警告也进文件。
    """
    from feedscope.core import logsetup

    path: Path | None = None
    if "--log-file" in argv:
        i = argv.index("--log-file")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            path = Path(argv[i + 1])
    logsetup.setup_logging(verbose="--verbose" in argv, path=path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feedscope", description=t("cli.description")
    )
    parser.add_argument("--verbose", action="store_true", help=t("cli.help_verbose"))
    parser.add_argument("--log-file", metavar="PATH", help=t("cli.help_log_file"))
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("tui", help=t("cli.help_tui"))

    login = sub.add_parser("login", help=t("cli.help_login"))
    login.add_argument("source", nargs="?", default="zhihu", help=t("cli.help_login_source"))

    probe = sub.add_parser("probe", help=t("cli.help_probe"))
    probe.add_argument("sources", nargs="*", help=t("cli.help_probe_sources"))

    sub.add_parser("doctor", help=t("cli.help_doctor"))

    rss = sub.add_parser("rss", help=t("cli.help_rss"))
    rss_sub = rss.add_subparsers(dest="rss_command", required=True)
    rss_sub.add_parser("list", help=t("cli.help_rss_list"))
    add_p = rss_sub.add_parser("add", help=t("cli.help_rss_add"))
    add_p.add_argument("url", help=t("cli.help_rss_add_url"))
    add_p.add_argument("--key", help=t("cli.help_rss_add_key"))
    add_p.add_argument("--label", help=t("cli.help_rss_add_label"))
    remove_p = rss_sub.add_parser("remove", help=t("cli.help_rss_remove"))
    remove_p.add_argument("key", help=t("cli.help_rss_remove_key"))
    import_p = rss_sub.add_parser("import", help=t("cli.help_rss_import"))
    import_p.add_argument("path", help=t("cli.help_rss_import_path"))
    export_p = rss_sub.add_parser("export", help=t("cli.help_rss_export"))
    export_p.add_argument("path", nargs="?", help=t("cli.help_rss_export_path"))

    fav = sub.add_parser("favorites", help=t("cli.help_favorites"))
    fav_sub = fav.add_subparsers(dest="fav_command")
    fav_export = fav_sub.add_parser("export", help=t("cli.help_favorites_export"))
    fav_export.add_argument("path", nargs="?", help=t("cli.help_favorites_export_path"))

    return parser


def _run_rss(args: argparse.Namespace) -> int:
    """rss 子命令组分发：订阅管理不走 Textual，加载轻。"""
    import asyncio

    from feedscope import subscriptions as subs
    from feedscope.core import config

    if args.rss_command == "list":
        feeds = config.rss_feeds()
        if not feeds:
            print(t("cli.rss_list_empty"))
            return 0
        for f in feeds:
            print(f"{f.key:<16} {f.label}  <{f.url}>")
        print(t("cli.rss_list_total", n=len(feeds)))
        return 0

    if args.rss_command == "add":
        # add_from_input 只分流 URL / OPML 路径；显式 key/label 仍走 add_feed。
        # 坏 OPML 文件 = 用法级错误退 2（与 import 同一规则），业务冲突退 1
        try:
            if args.key or args.label:
                feed = asyncio.run(subs.add_feed(args.url, key=args.key, label=args.label))
                summary = t("cli.rss_added", key=feed.key, label=feed.label, url=feed.url)
                added = [feed]
            else:
                added, summary = asyncio.run(subs.add_from_input(args.url))
        except subs.BadSubscriptionFile as exc:
            print(t("cli.not_added", error=exc))
            return 2
        except subs.SubscriptionError as exc:
            print(t("cli.not_added", error=exc))
            return 1
        if len(added) > 1:  # OPML 批量：逐条列出，方便核对
            for f in added:
                print(f"  {f.key:<16} {f.label}  <{f.url}>")
        print(summary + t("cli.restart_hint"))
        return 0

    if args.rss_command == "remove":
        removed = subs.remove_feed(args.key)
        if removed is None:
            print(t("cli.no_such_key", key=repr(args.key)))
            return 1
        print(t("cli.removed", key=removed.key, label=removed.label) + t("cli.restart_hint"))
        return 0

    if args.rss_command == "import":
        try:
            added, skipped = subs.import_opml(subs.read_opml_file(args.path))
        except subs.BadSubscriptionFile as exc:
            print(t("cli.import_failed", error=exc))
            return 2
        for f in added:
            print(t("cli.rss_added", key=f.key, label=f.label, url=f.url))
        for line in skipped[:5]:
            print(t("cli.skipped_line", line=line))
        if len(skipped) > 5:
            print(t("cli.more_skipped", n=len(skipped) - 5))
        print(
            t("cli.import_result", added=len(added), skipped=len(skipped))
            + t("cli.restart_hint")
        )
        return 0

    # export：给路径写文件，缺省 stdout（> 订阅.opml 重定向也行）
    opml = subs.export_opml(config.rss_feeds())
    if args.path:
        Path(args.path).write_text(opml, encoding="utf-8")
        print(t("cli.exported", n=len(config.rss_feeds()), path=args.path))
    else:
        print(opml, end="")
    return 0


def _run_favorites(args: argparse.Namespace) -> int:
    """favorites 子命令：无子命令 = 明文列表；export = Markdown（防锁定，同 rss export）。"""
    import time

    from feedscope.core.store import ReadState

    pairs = ReadState().favorites()
    if args.fav_command != "export":
        if not pairs:
            print(t("cli.favorites_empty"))
            return 0
        for ts, it in pairs:
            date = time.strftime("%Y-%m-%d", time.localtime(ts))
            print(f"{it.source:<10} {date}  {it.title}  <{it.url}>")
        print(t("fav.status_count", n=len(pairs)))
        return 0

    # Markdown 链接的方括号/圆括号会破坏语法，标题与 URL 做最小转义
    lines = [
        f"- [{it.title.replace('[', '(').replace(']', ')')}]"
        f"({it.url.replace('(', '%28').replace(')', '%29')}) — {it.source}，"
        f"{time.strftime('%Y-%m-%d', time.localtime(ts))}"
        for ts, it in pairs
    ]
    md = "\n".join(lines) + "\n" if lines else ""
    if args.path:
        Path(args.path).write_text(md, encoding="utf-8")
        print(t("cli.favorites_exported", n=len(pairs), path=args.path))
    else:
        print(md, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    # 日志先行（预扫 argv，见 _early_logging）：config 读取期的警告也要进文件
    _early_logging(list(argv) if argv is not None else [])
    # 进程语言按配置设定（未设置 = 缺省）：CLI 的 help/输出与 TUI 同语言；
    # 必须在 build_parser 之前——argparse 的 help 在构造时就成型。展示时区
    # 同批设定（fmt_* 全走它，见 core/timefmt.py）
    from feedscope.core import config, i18n, logsetup, timefmt

    i18n.set_language(config.get_language() or i18n.DEFAULT_LANGUAGE)
    timefmt.set_timezone(config.get_timezone())
    args = build_parser().parse_args(argv)
    if args.log_file:  # --log-file=VALUE 形式预扫看不见：正式 parse 后换位
        logsetup.setup_logging(verbose=args.verbose, path=Path(args.log_file), force=True)
    if args.verbose and args.command not in (None, "tui"):
        logsetup.add_stderr_mirror()  # 子命令在普通终端跑，镜像不串屏；TUI 绝不

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

    if args.command == "favorites":
        return _run_favorites(args)

    return 0
