"""桌面桥：把链接交给浏览器、把文本放进系统剪贴板（可配置 + 自动回退）。

缺省行为有两个缺口：
- webbrowser.open() 在 SSH / 无桌面环境里返回 False（找不到浏览器）。调用方
  以前不看返回值：条目标了已读，却什么都没发生
- Textual 的 copy_to_clipboard() 只发 OSC 52——macOS 自带的 Terminal.app 不认
  （Textual 自己的文档写明），而 toast 恒报「已复制」

这里给出可配置 + 自动回退。两条路都是同步的：Ctrl+O / Ctrl+Y 是显式按键，
调用方按键后立刻拿得到结果好给反馈，而缺省路径本身不阻塞（webbrowser 只
拉起进程、剪贴板工具毫秒级读完 stdin 就退出）。

- 打开链接：配置的 browser 命令优先（%u 换成链接，不写则追加在末尾；只负责
  拉起、不等它退出——浏览器命令可能常驻前台），没配走 webbrowser.open()；
  拉不起来如实回 False，由调用方给反馈
- 复制：配置的 clipboard 命令优先（文本走 stdin，等它读完），没配则探测本机
  剪贴板工具——pbcopy（macOS）/ wl-copy（Wayland）/ xclip、xsel（X11）；
  都没有（SSH、纯 TTY）才退回调用方的 OSC 52

tmux load-buffer 不进自动探测：远端 tmux 的缓冲区到不了用户手边那台机器的
剪贴板，而 OSC 52 会被 tmux 转发到外层终端——真需要就显式配置 clipboard。
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import webbrowser
from collections.abc import Callable

from feedscope.core import config

logger = logging.getLogger(__name__)

_URL_PLACEHOLDER = "%u"
_TOOL_TIMEOUT_S = 5.0

# (命令, 探测前提的环境变量)：环境变量不在就跳过——SSH 进来的远端机器上即使
# 装了 xclip，放的也是远端 X 会话的剪贴板，到不了用户手边那台机器
_CLIPBOARD_TOOLS: tuple[tuple[str, str | None], ...] = (
    ("pbcopy", None),
    ("wl-copy", "WAYLAND_DISPLAY"),
    ("xclip -selection clipboard", "DISPLAY"),
    ("xsel --clipboard --input", "DISPLAY"),
)


def _words(cmd: str) -> list[str] | None:
    """命令字符串 -> 参数列表（shell 词法）；引号没配对之类的词法错回 None。"""
    try:
        return shlex.split(cmd)
    except ValueError as exc:
        logger.warning("命令词法有误 %r：%s", cmd, exc)
        return None


def _clipboard_tool() -> list[str] | None:
    """本机可用的剪贴板命令（按上表顺序），没有回 None。"""
    for cmd, env in _CLIPBOARD_TOOLS:
        if env and not os.environ.get(env):
            continue
        args = cmd.split()
        if shutil.which(args[0]):
            return args
    return None


def _fill(args: list[str], url: str) -> list[str]:
    """参数里的 %u 换成链接；一处都没写就把链接追加在末尾。"""
    if any(_URL_PLACEHOLDER in arg for arg in args):
        return [arg.replace(_URL_PLACEHOLDER, url) for arg in args]
    return [*args, url]


def _spawn(args: list[str], *, stdin: bytes | None = None) -> bool:
    """拉起外部命令。stdin 给了就喂进去并等它退出（剪贴板工具要读完才生效）；
    否则只负责拉起。找不到命令 / 超时 / 非 0 退出回 False。"""
    try:
        if stdin is None:
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        done = subprocess.run(
            args,
            input=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TOOL_TIMEOUT_S,
            check=False,
        )
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("外部命令不可用 %s：%s", args[0], exc)
        return False


def open_url(url: str) -> bool:
    """把链接交给浏览器；拉不起来回 False（调用方给反馈，别静默）。"""
    if cmd := config.get_browser():
        args = _words(cmd)
        return _spawn(_fill(args, url)) if args else False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        logger.warning("打开浏览器失败", exc_info=True)
        return False  # webbrowser.Error：没有任何可用的浏览器


def copy_text(text: str, osc52: Callable[[str], None]) -> None:
    """文本进剪贴板：配置命令 > 本机剪贴板工具 > 调用方的 OSC 52。"""
    data = text.encode("utf-8")
    if (cmd := config.get_clipboard()) and (args := _words(cmd)) and _spawn(args, stdin=data):
        return
    if (tool := _clipboard_tool()) and _spawn(tool, stdin=data):
        return
    osc52(text)
