"""命令行入口：装配子命令并分发（命令实现全在 feedscope.commands，一个命令一个模块）。

本模块只做四件事，按固定顺序：先行日志装配 → 进程语言/时区设定 → 解析参数
→ 分发。全局旗标（--verbose / --log-file / --version）与默认命令（无子命令 =
tui）在这里；其余一律不在。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from feedscope import __version__, commands
from feedscope.core import config, i18n, logsetup, timefmt
from feedscope.core.i18n import t

_DEFAULT_COMMAND = "tui"  # 无子命令时的落点


def _early_logging(argv: list[str]) -> None:
    """语言/配置初始化**之前**的先行日志装配：手动预扫 argv。

    顺序是鸡蛋问题：语言按配置设定（要读配置，坏配置要记日志）而 argparse
    的 help 又要等语言——预扫只认 `--verbose` 与 `--log-file PATH` 两种
    长旗标形态（`--log-file=PATH` 看不见，正式 parse 后 force 换位补齐），
    让 config 读取期的警告也进文件。
    """
    path: Path | None = None
    if "--log-file" in argv:
        i = argv.index("--log-file")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            path = Path(argv[i + 1])
    logsetup.setup_logging(verbose="--verbose" in argv, path=path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedscope", description=t("cli.description"))
    parser.add_argument("--verbose", action="store_true", help=t("cli.help_verbose"))
    parser.add_argument("--log-file", metavar="PATH", help=t("cli.help_log_file"))
    parser.add_argument(
        "--version", action="version", version=f"feedscope {__version__}",
        help=t("cli.help_version"),
    )
    sub = parser.add_subparsers(dest="command")
    for command in commands.ALL:  # 顺序 = ALL 的书写顺序 = --help 里的命令顺序
        command.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    # 日志先行（预扫 argv，见 _early_logging）：config 读取期的警告也要进文件
    _early_logging(list(argv) if argv is not None else [])
    # 进程语言按配置设定（未设置 = 缺省）：CLI 的 help/输出与 TUI 同语言；
    # 必须在 build_parser 之前——argparse 的 help 在构造时就成型。展示时区
    # 同批设定（fmt_* 全走它，见 core/timefmt.py）
    i18n.set_language(config.get_language() or i18n.DEFAULT_LANGUAGE)
    timefmt.set_timezone(config.get_timezone())
    args = build_parser().parse_args(argv)
    if args.log_file:  # --log-file=VALUE 形式预扫看不见：正式 parse 后换位
        logsetup.setup_logging(verbose=args.verbose, path=Path(args.log_file), force=True)
    name = args.command or _DEFAULT_COMMAND  # 无子命令 = 默认命令
    if args.verbose and name != "tui":
        logsetup.add_stderr_mirror()  # 子命令在普通终端跑，镜像不串屏；TUI 绝不
    command = next(c for c in commands.ALL if name == c.NAME)
    return command.run(args)
