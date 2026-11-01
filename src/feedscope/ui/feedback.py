"""用户反馈统一出口：对用户的提示措辞与级别都在这里定。

分工：notify 是瞬时反馈（数秒后消失）；屏内常驻状态（列表空态、详情失败
文案）由各屏自己挂，但失败文案复用本模块的 failure_text，保证同义同形。
错误分级直接用 Textual notify 自带的 severity（info/warning/error），不另造枚举。
"""

from __future__ import annotations

from textual.app import App

from feedscope.core.source import Source, SourceError


def failure_text(source: Source, exc: Exception, action: str = "请求失败") -> str:
    """面向用户的失败文案。

    SourceError 的消息已是完整中文（源在边界包装时带上了源标签与原因）；
    其余技术异常（playwright 超时之类）才在这里补「{源} {动作}：」前缀。
    """
    if isinstance(exc, SourceError):
        return str(exc)
    return f"{source.label} {action}：{exc}"


def fail(app: App, source: Source, exc: Exception, action: str = "请求失败") -> None:
    """失败通知（error 级）。"""
    app.notify(failure_text(source, exc, action), title="抓取失败", severity="error")


def warn(app: App, msg: str, title: str) -> None:
    """警告通知（warning 级），如需要登录。"""
    app.notify(msg, title=title, severity="warning")


def info(app: App, msg: str) -> None:
    """普通通知（info 级），如「没有更多了」。"""
    app.notify(msg)
