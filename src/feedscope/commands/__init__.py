"""CLI 子命令层：一个子命令一个模块（形状约定见 base.py）。

cli.py 只做装配（遍历 ALL 调 register）与分发（按 NAME 取模块调 run），自身
不实现任何命令。新增子命令 = 建一个模块 + 在 ALL 里登记一行；ALL 的顺序就是
--help 里的命令顺序。report.py 是 doctor / probe 共用的状态行输出件，不是命令。

导入纪律：命令模块的依赖在模块级导入（core / services / sources 都轻）；只有
Textual 与 playwright 一律留在 run() 里——`feedscope doctor` 这类纯命令行路径
不加载界面框架。
"""

from __future__ import annotations

from feedscope.commands import cache, doctor, favorites, login, probe, rss, tui
from feedscope.commands.base import Command, SubParsers

__all__ = ["ALL", "Command", "SubParsers"]

ALL: tuple[Command, ...] = (tui, login, probe, doctor, cache, rss, favorites)
