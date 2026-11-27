"""全树生效的隔离 fixture：每个用例都在一份干净的「用户环境」里跑。

本文件只放 fixture（autouse 的环境隔离），不放 helper——跨模块共用的函数与
替身在 tests/support/，子树共享的 fixture 在各子目录的 conftest.py。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 不用 tmp_path：不少用例断言 tmp_path 的文件清单，隔离目录不能混进去。
    # 预创建的配置显式钉 zh-CN：缺省语言是英文（core.i18n.DEFAULT_LANGUAGE），
    # 而本仓库绝大多数断言写的是中文文案——测试语言必须确定，不随缺省漂
    env_dir = tmp_path_factory.mktemp("fs-user-env")
    config = env_dir / "sources.toml"
    config.write_text('language = "zh-CN"\n# 测试预创建：跳过首启引导，全默认全启用\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(config))
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(env_dir / "samples"))
    # 已读状态与列表缓存同样指进临时目录：不隔离会读写真实用户状态
    # （state.json 的已读标记 / feeds/ 缓存），断言也会被本机历史污染
    monkeypatch.setenv("FEEDSCOPE_STATE", str(env_dir / "state.json"))
    monkeypatch.setenv("FEEDSCOPE_FEED_CACHE", str(env_dir / "feeds"))
    monkeypatch.setenv("FEEDSCOPE_FAVORITES", str(env_dir / "favorites.json"))
    # 日志文件同理（cli.main 现在默认落盘）：不隔离会让每次跑测试都写
    # 真实用户日志目录
    monkeypatch.setenv("FEEDSCOPE_LOG_FILE", str(env_dir / "feedscope.log"))


@pytest.fixture(autouse=True)
def _pin_chinese_for_direct_calls() -> None:
    """直调 t() 的单元测试（不经 FeedScopeApp/配置）钉中文：语言是进程级
    状态，别的用例（en 冒烟 / 语言屏）改过后不能泄漏过来。App 用例不受
    影响——App.__init__ 总是按自己的配置重设语言。"""
    from feedscope.core import i18n

    i18n.set_language("zh-CN")
    yield
    i18n.set_language("zh-CN")


@pytest.fixture(autouse=True)
def _no_browser_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 冒烟测试里禁止启动即预热浏览器：测试源全是 stub，拉真 Chrome
    既慢又可能挂起（开关见 feedscope.ui.app._no_prewarm）。"""
    monkeypatch.setenv("FEEDSCOPE_NO_PREWARM", "1")


@pytest.fixture(autouse=True)
def _no_system_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不碰系统剪贴板（pbcopy 等）：自动探测的候选表清空，Ctrl+Y 走 OSC 52
    回退——替身拦 app.copy_to_clipboard 即可断言（见 test_theme）。探测逻辑
    本身与配置命令的测试在 test_desktop.py 里自己给候选表 / 替身。"""
    from feedscope.core import desktop

    monkeypatch.setattr(desktop, "_CLIPBOARD_TOOLS", ())
