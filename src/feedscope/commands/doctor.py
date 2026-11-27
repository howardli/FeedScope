"""`feedscope doctor`：环境自检。逐项检查、逐项打印，退出码 0 = 全部通过。

配置这一项要真体检：读取侧对坏配置全是静默容错（坏值回退默认、坏条目跳过、
只记日志），保存侧遇到坏文件又拒绝写入并指向 doctor——doctor 自己对坏文件
报 OK 的话，用户就被指来指去兜圈子。所以：解析失败、不会生效的设置、对不上
任何流的 hide 名都判 FAIL；数据文件损坏后的留底用 WARN 提醒（不算失败，但
用户该知道自己的已读/收藏是从空态重新开始的）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import feedscope.sources  # noqa: F401  import 即注册：源词表体检要认识全部源
from feedscope import __version__
from feedscope.commands.base import SubParsers
from feedscope.commands.report import line
from feedscope.core import config, logsetup
from feedscope.core.browser import PROFILE_DIR, find_chrome
from feedscope.core.i18n import t
from feedscope.core.registry import source_classes
from feedscope.core.store import state_path

NAME = "doctor"


def register(sub: SubParsers) -> None:
    sub.add_parser(NAME, help=t("cli.help_doctor"))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    return line("OK" if ok else "FAIL", label, detail)


def _info(label: str, detail: str) -> None:
    """信息项：恒 OK，不计入通过累计。"""
    line("OK", label, detail)


def _warn(label: str, detail: str) -> None:
    line("WARN", label, detail)


def _join(parts: list[str]) -> str:
    return t("common.enum_comma").join(parts)


def _source_lint() -> list[str]:
    """要认识源词表的那部分配置体检：enabled 里的未知源 id、hide 里对不上任何流的名字。

    纯文件结构的检查在 config.lint——那一层不认识任何具体的源。"""
    problems: list[str] = []
    known_ids = {cls.source_id for cls in source_classes()}
    unknown_ids = [i for i in config.enabled_ids() or [] if i not in known_ids]
    if unknown_ids:
        problems.append(t("doctor.unknown_source_ids", ids=_join(unknown_ids)))
    stray: list[str] = []
    for cls in source_classes():
        # 词表取类体默认流（实例视图已把被隐藏的剔掉，不能拿它对账）+ 订阅 key
        names = set(cls.feeds) | set(cls.feed_labels)
        if cls.subscribable:
            names |= {f.key for f in config.feeds_for(cls.source_id)}
        if all_feed := getattr(cls, "all_feed", ""):
            names.add(all_feed)
        stray += [
            f"{cls.source_id}: {name}"
            for name in config.hidden_feeds(cls.source_id)
            if name not in names
        ]
    if stray:
        problems.append(t("doctor.unknown_hidden", items=_join(stray)))
    return problems


def _kept_aside(state_dir: Path) -> list[str]:
    """状态目录下损坏数据文件的留底（core/store 的 *.corrupt-*）。"""
    return sorted(p.name for p in state_dir.glob("*.corrupt-*")) if state_dir.is_dir() else []


def run_checks() -> int:
    print(t("doctor.title", version=__version__))
    print()
    warnings = 0

    py_ok = sys.version_info >= (3, 11)
    ok = _check(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        py_ok,
        "" if py_ok else t("doctor.need_py"),
    )

    chrome = find_chrome()
    ok &= _check(
        "Google Chrome",
        bool(chrome),
        chrome or t("doctor.chrome_missing"),
    )

    try:
        import playwright  # noqa: F401
        import textual  # noqa: F401

        ok &= _check(t("doctor.deps_label"), True, t("doctor.importable"))
    except ImportError as exc:
        ok &= _check(t("doctor.deps_label"), False, t("doctor.import_failed", error=exc))

    # ---- 以下信息项与配置体检：不算环境失败 ----
    profile_state = (
        t("doctor.profile_exists") if PROFILE_DIR.exists() else t("doctor.profile_will_create")
    )
    _info(t("doctor.profile_label"), f"{PROFILE_DIR}（{profile_state}）")

    problem = config.parse_error()
    ok &= _check(
        t("doctor.config_label"),
        problem is None,
        f"{config.config_path()} - {problem or config.describe()}",
    )
    for text in (config.lint() + _source_lint()):  # 每条问题一行：不挡启动，但等于没生效
        ok &= _check(t("doctor.config_problem_label"), False, text)

    state = t("doctor.profile_exists") if state_path().exists() else t("doctor.profile_will_create")
    _info(t("doctor.state_label"), f"{state_path()}（{state}）")
    if kept := _kept_aside(state_path().parent):
        warnings += 1
        _warn(t("doctor.state_label"), t("doctor.corrupt_kept", n=len(kept), names=_join(kept)))

    _info(t("doctor.log_label"), str(logsetup.active_path()))

    print()
    if warnings and ok:
        print(t("doctor.has_warnings", n=warnings))
    print(t("doctor.all_ok") if ok else t("doctor.has_failures"))
    return 0 if ok else 1


def run(args: argparse.Namespace) -> int:
    return run_checks()
