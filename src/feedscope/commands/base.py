"""子命令模块的形状约定（类型件；模块本身很薄，命令模块从这里取）。

一个子命令 = commands/ 下一个模块，带齐三样：

    NAME            命令名（argparse 子命令键，也是分发键）
    register(sub)   把自己挂到 argparse 的子命令集上（help 文案经 i18n）
    run(args)       执行，返回进程退出码

cli.py 只做装配（遍历 commands.ALL 调 register）与分发（按 NAME 取模块调
run）。本模块是包内的叶子：命令模块依赖它，它不依赖任何命令模块。
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    # argparse 的子命令集容器（add_subparsers 的返回值）：类型只在 stubs 里带下划线，
    # 带参数的别名也只在类型检查里存在
    SubParsers: TypeAlias = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    SubParsers = argparse._SubParsersAction


@runtime_checkable
class Command(Protocol):
    """子命令模块的形状（模块本身即实例）。"""

    NAME: str

    def register(self, sub: SubParsers) -> None: ...

    def run(self, args: argparse.Namespace) -> int: ...
