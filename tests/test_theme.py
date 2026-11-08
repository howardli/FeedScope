"""主题系统测试：一文件一主题的发现/完整性/回退 + 色彩契约 + 配置往返 + Ctrl+T 动线。"""

from __future__ import annotations

import asyncio

import pytest

from feedscope.core import config
from feedscope.core.registry import _REGISTRY
from feedscope.ui import theme as theme_mod
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.theme import ThemeScreen

from conftest import last_notification, settle, use_config
from test_tui_smoke import StubSource


@pytest.fixture()
def stub_registry():
    """只留 stub 源（pytest fixture 不能跨模块直调，这里内联 test_tui_smoke 的同款替换）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def test_two_builtin_themes_discovered() -> None:
    names = [t.name for t in theme_mod.available()]
    assert names == ["editor-dark", "editor-light"], "内置两主题按名排序"


def test_theme_completeness_and_contract() -> None:
    """键校验 + 色彩契约：全部色槽/变量齐；链接 = primary（蓝只给链接的既有约定，
    两套主题都守）。"""
    themes = {t.name: t for t in theme_mod.available()}
    for name, th in themes.items():
        assert th.primary and th.background and th.foreground, f"{name} 缺基础色槽"
        assert set(th.variables) >= {"link-color", "footer-key-foreground", "nav-sel-background"}
        vs = th.to_color_system().generate()
        assert vs["link-color"] == th.primary, f"{name}：链接要可认，蓝只给链接"
    assert themes["editor-dark"].dark is True
    assert themes["editor-light"].dark is False


def test_bad_theme_file_skipped(monkeypatch, tmp_path) -> None:
    """坏文件跳过不挡启动（i18n 同款容错）；键校验漏色槽同样跳过。"""
    bad = tmp_path / "broken.toml"
    bad.write_text("not a [theme] table", encoding="utf-8")
    incomplete = tmp_path / "incomplete.toml"
    incomplete.write_text(
        '[theme]\nname = "x"\ndark = true\nprimary = "#000000"\n[theme.variables]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(theme_mod, "_DIR", tmp_path)
    monkeypatch.setattr(theme_mod, "_cache", None)
    assert theme_mod.available() == [], "全部坏 = 空（App 回退 Textual 内置）"

    good = tmp_path / "good.toml"
    good.write_text((theme_mod.Path(__file__).parents[1]
                     / "src/feedscope/ui/themes/editor-dark.toml").read_text(encoding="utf-8"),
                    encoding="utf-8")
    monkeypatch.setattr(theme_mod, "_cache", None)
    assert [t.name for t in theme_mod.available()] == ["editor-dark"], "好文件注册、坏文件跳过"


def test_resolve_falls_back_to_default() -> None:
    assert theme_mod.resolve(None) == "editor-dark"
    assert theme_mod.resolve("no-such-theme") == "editor-dark"
    assert theme_mod.resolve("editor-light") == "editor-light"


def test_config_theme_roundtrip(tmp_path, monkeypatch) -> None:

    use_config(monkeypatch, tmp_path, 'language = "zh-CN"\n')
    assert config.get_theme() is None
    config.save_theme("editor-light")
    assert config.get_theme() == "editor-light"
    # 语言/主题共存：落盘不互相覆盖
    assert config.get_language() == "zh-CN"
    config.save_language("en")
    assert config.get_theme() == "editor-light" and config.get_language() == "en"


def test_ctrl_t_switches_live_and_persists(stub_registry, monkeypatch, tmp_path) -> None:
    """Ctrl+T：选择屏 → Enter 当场换肤（App.theme 即时）+ 落盘，第二实例读回。"""

    cfg = use_config(monkeypatch, tmp_path, 'language = "zh-CN"\n')

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            assert app.theme == "editor-dark"
            await pilot.press("ctrl+t")
            assert await settle(pilot, lambda: isinstance(app.screen, ThemeScreen))
            await pilot.press("down")  # 第二项 = editor-light
            await pilot.press("enter")
            assert await settle(pilot, lambda: app.theme == "editor-light"), "即时生效"
            assert last_notification(app) == "主题已切换：editor-light"
        assert 'theme = "editor-light"' in cfg.read_text(encoding="utf-8"), "已落盘"

        app2 = FeedScopeApp()
        async with app2.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app2.main.items) == 3)
            assert app2.theme == "editor-light", "重启（第二实例）读回"

    asyncio.run(flow())


def test_row_read_dimming_follows_marks(stub_registry) -> None:
    """已读置灰随圆点同进退：读过的行带 row-read 类，未读不带。"""
    from textual.widgets import ListView

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            view = app.main.query_one("#feed", ListView)
            assert not view.children[0].has_class("row-read"), "未读不置灰"
            await pilot.press("enter")  # 打开即已读
            assert await settle(pilot, lambda: len(app.screen.query("DetailScreen")) >= 0)
            await pilot.press("escape")
            assert await settle(pilot, lambda: view.children[0].has_class("row-read")), "已读置灰"

    asyncio.run(flow())


def test_copy_link_on_three_layers(stub_registry, monkeypatch) -> None:
    """Ctrl+Y 三层：主列表复制当前条目 url；详情层复制 _browser_url（同 Ctrl+O）。"""
    copied: list[str] = []

    def fake_copy(self, text):  # noqa: ANN001 - 同步方法（OSC 52 直写终端）
        copied.append(text)

    monkeypatch.setattr(FeedScopeApp, "copy_to_clipboard", fake_copy)

    async def flow() -> None:
        app = FeedScopeApp()
        async with app.run_test(size=(90, 36)) as pilot:
            assert await settle(pilot, lambda: len(app.main.items) == 3)
            await pilot.press("ctrl+y")
            assert copied[-1] == "https://example.com/0", "主列表：当前条目 url"
            assert last_notification(app) == "链接已复制"

            await pilot.press("enter")
            assert await settle(pilot, lambda: len(app.screen.query("DetailScreen")) >= 0)
            await pilot.press("ctrl+y")
            assert copied[-1] == "https://example.com/0", "详情层：正在读的条目"

    asyncio.run(flow())
