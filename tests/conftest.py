"""测试统一隔离用户环境：FEEDSCOPE_CONFIG / FEEDSCOPE_SAMPLE_DIR 都指向临时路径。

防止开发者本机 ~/.config/feedscope/sources.toml 的隐藏流配置泄漏进 feeds
断言（要测配置本身，用 monkeypatch.setenv 覆盖成 tmp_path 下的文件），
样本落盘也只写进临时目录。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_env(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(tmp_path / "absent-sources.toml"))
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(tmp_path / "samples"))


@pytest.fixture(autouse=True)
def _no_browser_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 冒烟测试里禁止启动即预热浏览器：测试源全是 stub，拉真 Chrome
    既慢又可能挂起（PREWARM 闸门见 feedscope.ui.app）。"""
    monkeypatch.setattr("feedscope.ui.app.PREWARM", False)
