"""语言选择屏：首启设置（先于源引导、选中即本会话生效）与 Ctrl+L 日常修改
（落盘 + 重启生效 toast，不做当场切换）。

语言是进程级状态：autouse 复位兜底，用例改过不泄漏给同进程的其他用例。
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import last_notification, settle
from textual.widgets import OptionList

from feedscope.core import i18n
from feedscope.core.models import FeedItem
from feedscope.core.registry import _REGISTRY
from feedscope.core.source import Source, StaticFeedLoader
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.language import LanguageScreen
from feedscope.ui.screens.source_setup import OnboardingScreen


@pytest.fixture(autouse=True)
def _restore_language() -> None:
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


class LangStubSource(Source):
    """最小 stub 源：一个流两条目，够主屏渲染即可。"""

    source_id = "lstub"
    label = "语言存根"
    feeds: ClassVar[list[str]] = ["one"]

    async def open_feed(self, name: str) -> StaticFeedLoader:
        return StaticFeedLoader(
            [FeedItem(source="lstub", id="a", title="标题甲", url="https://x.test/a")]
        )


@pytest.fixture()
def lang_stub_registry():
    """只留 stub 源（仅影响本测试进程），结束后恢复真实注册表。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[LangStubSource.source_id] = LangStubSource
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _option_prompts(app: FeedScopeApp) -> list[str]:
    ol = app.screen.query_one(OptionList)
    return [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]


async def _run_first_run_pick_language(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "fresh" / "sources.toml"  # 不存在 → 首启
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    monkeypatch.setattr(i18n, "getlocale", lambda: ("en_US", "UTF-8"))  # 预选 en
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: isinstance(app.screen, LanguageScreen))
        assert _option_prompts(app) == ["简体中文", "English"], "行文本 = 各语言自称"
        ol = app.screen.query_one(OptionList)
        assert ol.highlighted == 1, "预选 = 系统猜测（en）"

        await pilot.press("up")  # 移到 简体中文
        await pilot.press("enter")
        assert i18n.language() == "zh-CN", "首启选中即本会话生效（渲染前设定）"
        assert await settle(pilot, lambda: isinstance(app.screen, OnboardingScreen))

        await pilot.click("#setup-confirm")  # 引导确认（默认全勾）
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["language"] == "zh-CN"
        assert data["enabled"] == ["lstub"], "语言与源选择同一次首启落盘"


def test_first_run_pick_language_then_onboarding(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_first_run_pick_language(monkeypatch, tmp_path))


async def _run_first_run_skip_language(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "skip" / "sources.toml"
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    monkeypatch.setattr(i18n, "getlocale", lambda: ("en_US", "UTF-8"))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: isinstance(app.screen, LanguageScreen))
        await pilot.press("escape")  # 跳过语言：沿用缺省（不是系统猜测）
        assert i18n.language() == i18n.DEFAULT_LANGUAGE
        assert await settle(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        await pilot.click("#setup-confirm")
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["language"] == i18n.DEFAULT_LANGUAGE, "跳过也随首启落定：之后不再问"


def test_first_run_skip_language_settles_default(
    lang_stub_registry, monkeypatch, tmp_path
) -> None:
    asyncio.run(_run_first_run_skip_language(monkeypatch, tmp_path))


async def _run_ctrl_l_change(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "sources.toml"
    cfg.write_text('language = "zh-CN"\n', encoding="utf-8")  # 钉 zh：断言的是中文预选与 toast
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)

        await pilot.press("ctrl+l")
        assert await settle(pilot, lambda: isinstance(app.screen, LanguageScreen))
        ol = app.screen.query_one(OptionList)
        assert ol.highlighted == 0, "日常修改预选 = 当前语言（zh-CN）"

        await pilot.press("down")  # English
        await pilot.press("enter")
        assert await settle(pilot, lambda: not isinstance(app.screen, LanguageScreen))
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["language"] == "en"
        assert "重启 feedscope 后生效" in last_notification(app)
        assert i18n.language() == "zh-CN", "不当场切换：文案随进程固定（本会话仍是启动语言）"


def test_ctrl_l_change_saves_and_toasts_restart(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_ctrl_l_change(monkeypatch, tmp_path))


async def _run_ctrl_l_same_language(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "sources.toml"
    cfg.write_text('language = "zh-CN"\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        await pilot.press("ctrl+l")
        assert await settle(pilot, lambda: isinstance(app.screen, LanguageScreen))
        await pilot.press("enter")  # 重选当前语言（高亮即 zh-CN）
        await settle(pilot, lambda: not isinstance(app.screen, LanguageScreen))
        # 原语言重选：不落盘但要出声（静默会让「以为切了」的用户误判成功）
        assert "语言未变" in last_notification(app)
        assert tomllib.loads(cfg.read_text(encoding="utf-8"))["language"] == "zh-CN"


def test_ctrl_l_same_language_no_toast(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_ctrl_l_same_language(monkeypatch, tmp_path))


async def _run_ctrl_l_cancel(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "sources.toml"
    cfg.write_text("# 已配置（无 language 键）\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(90, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        await pilot.press("ctrl+l")
        assert await settle(pilot, lambda: isinstance(app.screen, LanguageScreen))
        await pilot.press("escape")
        await settle(pilot, lambda: not isinstance(app.screen, LanguageScreen))
        assert "language" not in tomllib.loads(cfg.read_text(encoding="utf-8")), "取消不落盘"


def test_ctrl_l_cancel_keeps_config(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_ctrl_l_cancel(monkeypatch, tmp_path))


async def _run_footer_shows_language_binding(monkeypatch, tmp_path: Path) -> None:
    """Ctrl+L 的键式描述挂载时解析：键位表里是「语言」而不是原始键名
    （Footer 实时读键位表，这里直查同一数据源）。"""
    cfg = tmp_path / "sources.toml"
    cfg.write_text('language = "zh-CN"\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(100, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        binding = app.main._bindings.key_to_bindings["ctrl+l"][0]
        assert binding.description == "语言"
        app_binding = app._bindings.key_to_bindings["ctrl+q"][0]
        assert app_binding.description == "退出"  # 非键描述直通（迁移过渡期语义）
        # tab/shift+tab 切站声明在列表控件上（压过 Screen 焦点切换），页脚读它：
        # 控件级键位也要随语言解析（否则页脚露原始键名）
        from feedscope.ui.screens.main import FeedListView

        feed = app.main.query_one("#feed", FeedListView)
        assert [feed._bindings.key_to_bindings[k][0].description for k in ("tab", "shift+tab")] == [
            "切站",
            "切站",
        ]
        for key in ("f5", "f2", "ctrl+n"):
            assert app.main._bindings.key_to_bindings[key][0].description != "", key


def test_footer_shows_language_binding(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_footer_shows_language_binding(monkeypatch, tmp_path))


async def _run_en_smoke(monkeypatch, tmp_path: Path) -> None:
    """英文配置启动：标题/状态行/键位文案/空态全部英文（en 冒烟）。"""
    cfg = tmp_path / "sources.toml"
    cfg.write_text('language = "en"\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(100, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        assert app.title == "FeedScope"
        # en 计数模板统一复数形（"{n} items"）：单复数分支不在本次范围
        assert app.sub_title == "1 items", f"状态行英文：{app.sub_title!r}"
        assert app.main._bindings.key_to_bindings["ctrl+l"][0].description == "Language"
        assert app.main._bindings.key_to_bindings["f5"][0].description == "Refresh"
        assert app._bindings.key_to_bindings["ctrl+q"][0].description == "Quit"
        from feedscope.ui.screens.main import FeedListView

        feed = app.main.query_one("#feed", FeedListView)
        assert feed._bindings.key_to_bindings["tab"][0].description == "Switch"

        await pilot.press("f5")  # 刷新走英文通道（失败出口也是英文模板）
        assert await settle(pilot, lambda: len(app.main.items) == 1)


def test_en_smoke_main_screen(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_en_smoke(monkeypatch, tmp_path))


async def _run_unset_language_defaults_to_english(monkeypatch, tmp_path: Path) -> None:
    """缺省即英文：配置文件存在但没有 language 键（升级上来的老用户同形），
    不再回退中文——中文用户按一次 Ctrl+L 选简体中文即可。"""
    cfg = tmp_path / "sources.toml"
    cfg.write_text("# 已配置但未设语言\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    app = FeedScopeApp()
    async with app.run_test(size=(100, 36)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 1)
        assert i18n.language() == i18n.DEFAULT_LANGUAGE == "en"
        assert app.sub_title == "1 items", "缺省英文状态行"


def test_unset_language_defaults_to_english(lang_stub_registry, monkeypatch, tmp_path) -> None:
    asyncio.run(_run_unset_language_defaults_to_english(monkeypatch, tmp_path))


def test_en_display_vocabulary() -> None:
    """计数/赞词汇双形态：zh 万折算，en K/M 折算。"""
    from feedscope.core.display import fmt_count, votes_text

    i18n.set_language("zh-CN")
    assert fmt_count(45000) == "4.5 万"
    assert fmt_count(999) == "999"
    assert votes_text(45000) == "赞 4.5 万"
    i18n.set_language("en")
    assert fmt_count(45000) == "45K"
    assert fmt_count(1_234_000) == "1.2M"
    assert fmt_count(1500) == "1.5K"
    assert fmt_count(999) == "999"
    assert votes_text(45000) == "45K likes"
