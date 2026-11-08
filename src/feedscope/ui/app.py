"""TUI 应用壳：主题/生命周期/全局键位与账号动线的宿主，主屏见 screens/main.py。

App 只保留跨屏职责——主题注册、启动序列（注册表检查 + 浏览器预热）、
退出清理（会话/连接池）、全局退出键；主屏（tab 条/边栏/列表/导航）整体
住在 MainScreen（get_default_screen 返回，框架启动即挂为默认屏），经 app.main
访问。这样键位归属与守卫自然对齐：主屏键只绑在主屏（弹层/详情在前台时
不命中），全局键只剩 Ctrl+Q。
"""

from __future__ import annotations

import os
from typing import ClassVar

from textual.app import App
from textual.binding import Binding

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core import config, http
from feedscope.core import i18n
from feedscope.core.i18n import t
from feedscope.core.browser import close_session, get_session
from feedscope.core.registry import source_classes
from feedscope.core.store import ReadState
from feedscope.ui import Bindings
from feedscope.ui.account import AccountFlow
from feedscope.ui.labels import retranslate_bindings
from feedscope.ui.screens.main import MainScreen
from feedscope.ui.theme import FEEDSCOPE_THEME


def _no_prewarm() -> bool:
    """禁预热开关：FEEDSCOPE_NO_PREWARM=1 时启动不拉浏览器。

    测试与截图脚本用（stub 源不需要真 Chrome，拉起来既慢又可能挂起）；
    运行时读环境变量而非模块级开关，工具侧设 env 即生效、不用掰模块属性。
    """
    return os.environ.get("FEEDSCOPE_NO_PREWARM") == "1"


class FeedScopeApp(App[None]):
    # 标题随语言：TITLE 类属性在 import 期求值会冻住语言，on_mount 里按 t() 设

    # 主屏即默认屏：框架启动时实例化（无需显式 push），详情/弹层盖在其上
    def get_default_screen(self) -> MainScreen:
        return MainScreen()

    # 关闭 Textual 自带命令面板（页脚 palette 链接 + Ctrl+P）：里面只有框架默认的
    # Theme/Quit/Keys/Screenshot 系统命令，对本应用全是冗余——主题定死编辑器风、
    # 退出有 q，其余是调试入口，不该暴露给用户。
    ENABLE_COMMAND_PALETTE = False

    # 键位全部非打印键：字母/数字会被中文输入法拦进候选框，一个动作只留
    # 一个键，不给字母留别名。全局只剩退出（Ctrl+C 连按退出是 Textual 自带
    # 兜底，不占页脚）；主屏键（刷新/翻页/导航/F2/Ctrl+U）在 MainScreen——
    # 屏级绑定只在该屏前台时命中，App 级动作不再需要「是否在主屏」守卫。
    BINDINGS: ClassVar[Bindings] = [
        Binding("ctrl+q", "quit", "bindings.quit", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        # 进程语言按配置设定（未设置 = 缺省英文，不是系统猜测——猜测只作
        # 首启语言屏的预选高亮；这里保证直构 App 的路径（截图脚本 / 测试）
        # 与 cli.main 组装的一致）。语言随进程固定：改语言走 Ctrl+L 落盘 + 重启
        i18n.set_language(config.get_language() or i18n.DEFAULT_LANGUAGE)
        # 克制的编辑器风配色（见 ui/theme.py），产品需求而非保守
        self.register_theme(FEEDSCOPE_THEME)
        self.theme = FEEDSCOPE_THEME.name
        # 账号动线（撞墙自动询问 / Ctrl+U 登录登出）：状态与编排都在 ui/account.py
        self.accounts = AccountFlow(self)
        # 已读/已见状态（● 新 / ○ 未读 / 空已读 的判据）：惰性加载，见 core/store.py
        self.state = ReadState()

    @property
    def main(self) -> MainScreen | None:
        """主屏实例（栈底，被弹层/详情覆盖时也在）；启动完成前可能为 None。"""
        for screen in reversed(self.screen_stack):
            if isinstance(screen, MainScreen):
                return screen
        return None

    def on_mount(self) -> None:
        self.title = t("app.title")
        retranslate_bindings(self)  # App 级键位（ctrl+q）的键式文案解析，见 ui/labels.py
        if not source_classes():
            self.exit(message=t("app.exit_no_sources"))
            return
        if not _no_prewarm():
            self.run_worker(self._prewarm(), group="prewarm")  # 后台预热，见 _prewarm

    async def _prewarm(self) -> None:
        """后台预热浏览器会话：首条列表不用再付冷启动（Chrome 拉起几秒；
        UA 探针另有 7 天落盘缓存，多数启动只付 Chrome 拉起）。

        知乎/Reddit（通道页签）与 X/Reddit（profile 读 cookie）都要浏览器，
        预热几乎总用得上；失败静默——首次真正取数时自然再试并给正式报错。
        """
        try:
            await get_session().start()
        except Exception:
            pass

    # ---- 清理 ----

    async def on_unmount(self) -> None:
        await close_session()
        await http.close()
