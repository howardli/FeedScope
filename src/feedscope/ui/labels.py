"""键位文案的 i18n 解析：类级 BINDINGS 的 description 存文案键，挂载时翻译。

Binding 的 description 在类体里求值（import 期），那时进程语言未定（测试
还要在同进程里来回切），description 只能存 i18n 键、挂载时统一解析——
Footer 实时读 _bindings，替换即生效。这与 reading 系按源单位词 relabel
（_relabel_bindings，传最终文案）是同一手法的两个用法：一个是静态键、
一个是动态词。

约定：App 与各 Screen 的 on_mount 调 retranslate_bindings(self)；漏调的
屏 Footer 会显示原始键名（肉眼即见，en 冒烟测试兜底）。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from textual.app import App
from textual.screen import Screen

from feedscope.core.i18n import has, t


def retranslate_bindings(holder: App[Any] | Screen[Any]) -> None:
    """把 holder 键位表里以文案键书写的 description 换成当前语言文本。

    非键描述直通（has 判定）：relabel 后的源单位词等最终文案不是键，不能
    进 t()——否则落到「缺键」警告还会把文案换成键名。
    """
    table = holder._bindings.key_to_bindings
    for key, bindings in list(table.items()):
        table[key] = [
            dataclasses.replace(b, description=t(b.description))
            if b.description and has(b.description)
            else b
            for b in bindings
        ]
