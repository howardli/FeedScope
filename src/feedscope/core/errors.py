"""业务异常词汇：全库最底层的叶子，任何层都可以只为一枚异常 import 它。

- SourceError / LoginRequired：源侧取数失败。原本住在 core.source——core 的
  设施模块（http / browser）只需要一个异常类型，却要为此依赖整份源插件契约，
  core.source 与 core.browser 之间的环就是这么来的
- SubscriptionError / BadSubscriptionFile：订阅管理失败。原本住在
  services.subscriptions——源的订阅钩子（Source.check_subscription /
  probe_subscription）要抛它，而 sources 不能依赖 services

拆出后依赖方向干净：errors 谁都不依赖（分层守卫见 tests/test_architecture.py）。
"""

from __future__ import annotations


class SourceError(Exception):
    """源侧失败的业务异常：消息为面向用户的当前语言文案（含源标签与原因，
    经 core.i18n.t 生成），UI 可直接展示。

    源在取数/解析边界把「自己认识的失败」（HTTP 状态、服务端错误体、超时）
    包成它；UI 出口对它直接展示消息，对其他技术异常才补「源标签 + 失败」前缀。

    status 是引发失败的结构化 HTTP 状态码（无则 None）：语义判定（401/403
    转登录引导、404 转消亡文案等）只看它，绝不从消息文本猜——消息是给人
    看的翻译文案，措辞一变控制流就断。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LoginRequired(SourceError):
    """源要求登录但当前 profile 未登录（如知乎重定向到 /signin）。"""


class SubscriptionError(Exception):
    """订阅操作的业务失败（重复 / key 冲突 / 订阅物不存在 / 文件读不了）。

    消息面向用户，可直接打印。
    """


class BadSubscriptionFile(SubscriptionError):
    """OPML 文件读不了 / 不是合法 OPML：用法级错误（CLI 退出码 2，与 argparse 约定一致）。"""
