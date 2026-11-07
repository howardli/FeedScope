"""用户反馈统一出口：对用户的提示措辞与级别都在这里定。

分工：notify 是瞬时反馈（数秒后消失）；屏内常驻状态（列表空态、详情失败
文案）由各屏自己挂，但失败文案复用本模块的 failure_text，保证同义同形。
收件人 App 或 Screen 皆可（Textual 两者都有 notify，屏内调用直接传 self）。
错误分级直接用 Textual notify 自带的 severity（info/warning/error），不另造枚举。

action 是动作文案键（feedback.*）：请求/添加/登出/评论加载/加载更多失败
各有一键，标题跟随动作——登出失败别写成「抓取失败」。
"""

from __future__ import annotations

from textual.app import App
from textual.screen import Screen

from feedscope.core.i18n import has, t
from feedscope.core.source import Source, SourceError

ACTION_FETCH = "feedback.action_fetch"  # 默认动作（列表/详情首载）


def _action_text(action: str) -> str:
    """动作参数 -> 展示文本：键走 t()，非键（历史调用/特殊拼接）直通。"""
    return t(action) if has(action) else action


def failure_text(source: Source | None, exc: Exception, action: str = ACTION_FETCH) -> str:
    """面向用户的失败文案。

    SourceError 的消息已是完整文案（源在边界包装时带上了源标签与原因）；
    其余技术异常（playwright 超时之类）才在这里补「{源} {动作}：」前缀。
    source 为 None（无源语境的业务流，如订阅添加）只补「{动作}：」。
    """
    if isinstance(exc, SourceError):
        return str(exc)
    if source is None:
        return t("feedback.failure", action=_action_text(action), error=exc)
    return t(
        "feedback.failure_with_source",
        source=source.label_text, action=_action_text(action), error=exc,
    )


def fail(
    app: App | Screen, source: Source | None, exc: Exception, action: str = ACTION_FETCH
) -> None:
    """失败通知（error 级）。标题跟随动作：登出/加载更多失败别都写成「抓取失败」。"""
    title = (
        t("feedback.title_fetch_failed")
        if action == ACTION_FETCH
        else _action_text(action)
    )
    app.notify(failure_text(source, exc, action), title=title, severity="error")


def warn(app: App | Screen, msg: str, title: str) -> None:
    """警告通知（warning 级），如需要登录。"""
    app.notify(msg, title=title, severity="warning")


def info(app: App | Screen, msg: str) -> None:
    """普通通知（info 级），如「没有更多了」。"""
    app.notify(msg)
