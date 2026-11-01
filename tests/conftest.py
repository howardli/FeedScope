"""测试统一隔离用户环境：FEEDSCOPE_CONFIG / FEEDSCOPE_SAMPLE_DIR 都指向临时路径。

防止开发者本机 ~/.config/feedscope/sources.toml 的隐藏流/启用配置泄漏进
断言。默认预创建一个只含注释的空配置（= 已完成首启、全默认全启用），让
既有 TUI 用例不触发首启引导；要测引导/配置本身，用 monkeypatch.setenv
把 FEEDSCOPE_CONFIG 覆盖成 tmp_path 下的目标文件（不存在的路径即首启），
样本落盘也只写进临时目录。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 不用 tmp_path：不少用例断言 tmp_path 的文件清单，隔离目录不能混进去
    env_dir = tmp_path_factory.mktemp("fs-user-env")
    config = env_dir / "sources.toml"
    config.write_text("# 测试预创建：跳过首启引导，全默认全启用\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(config))
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(env_dir / "samples"))


@pytest.fixture(autouse=True)
def _no_browser_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 冒烟测试里禁止启动即预热浏览器：测试源全是 stub，拉真 Chrome
    既慢又可能挂起（PREWARM 闸门见 feedscope.ui.app）。"""
    monkeypatch.setattr("feedscope.ui.app.PREWARM", False)
