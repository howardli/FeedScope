"""打开链接 / 复制链接的屏级封装：主列表、收藏屏、阅读屏家族三处共用。

桌面侧的差异（可配置命令、自动探测、SSH / 无桌面环境的回退）在
core.desktop；这里只补反馈——拉不起浏览器不再静默（条目标了已读，却什么都
没发生）。
"""

from __future__ import annotations

from typing import Any

from textual.screen import Screen

from feedscope.core import desktop
from feedscope.core.i18n import t
from feedscope.ui import feedback


def open_link(screen: Screen[Any], url: str) -> None:
    """交给浏览器；拉不起来给警告（指路 Ctrl+Y 复制链接 / 配置 browser 命令）。"""
    if not desktop.open_url(url):
        feedback.warn(screen, t("feedback.open_failed"), title=t("bindings.browser"))


def copy_link(screen: Screen[Any], text: str) -> None:
    """进剪贴板（配置命令 > 本机剪贴板工具 > OSC 52），随后照常报「已复制」。"""
    desktop.copy_text(text, osc52=screen.app.copy_to_clipboard)
    feedback.info(screen, t("main.toast_copied"))
