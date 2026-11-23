"""core.desktop + ui.launch：打开链接 / 复制的可配置、自动回退与失败反馈。

缺省行为的两个缺口（Ctrl+O 在 SSH/无桌面环境静默失效；Ctrl+Y 只有 OSC 52，
macOS Terminal 不认而 toast 恒报已复制）在这里逐条钉住。全部用替身，不拉起
真实浏览器、不碰系统剪贴板。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import last_notification, settle, use_config

import feedscope.sources  # noqa: F401  先触发真实源注册（stub_registry 结束后恢复用）
from feedscope.core import desktop
from feedscope.core.registry import _REGISTRY
from feedscope.ui.app import FeedScopeApp


def _never(*_args, **_kwargs):
    pytest.fail("不该走到这里")


# ---- 打开链接 ----


def test_open_url_default_uses_webbrowser(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: seen.append(url) or True)
    assert desktop.open_url("https://e.com/1") is True
    assert seen == ["https://e.com/1"]


def test_open_url_reports_failure_instead_of_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSH / 无桌面环境：webbrowser 找不到浏览器。以前返回值被忽略（条目标了已读，什么都没发生）。"""
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: False)
    assert desktop.open_url("https://e.com/1") is False

    def no_browser(url: str) -> bool:
        raise desktop.webbrowser.Error("could not locate runnable browser")

    monkeypatch.setattr(desktop.webbrowser, "open", no_browser)
    assert desktop.open_url("https://e.com/1") is False


def test_open_url_configured_command_fills_placeholder(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, 'browser = "my-browser --profile work %u"\n')
    spawned: list[list[str]] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kw: spawned.append(args))
    monkeypatch.setattr(desktop.webbrowser, "open", _never)  # 配了命令就不走 webbrowser
    assert desktop.open_url("https://e.com/1") is True
    assert spawned == [["my-browser", "--profile", "work", "https://e.com/1"]]


def test_open_url_configured_command_without_placeholder_appends_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, "browser = 'firefox'\n")
    spawned: list[list[str]] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kw: spawned.append(args))
    desktop.open_url("https://e.com/2")
    assert spawned == [["firefox", "https://e.com/2"]]


def test_open_url_configured_command_that_cannot_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """命令不存在 / 词法坏了：如实回 False，不抛。"""
    use_config(monkeypatch, tmp_path, 'browser = "definitely-not-a-real-browser-xyz %u"\n')
    assert desktop.open_url("https://e.com/1") is False
    use_config(monkeypatch, tmp_path, "browser = 'open \"unbalanced'\n")  # TOML 字面串里的引号没配对
    assert desktop.open_url("https://e.com/1") is False


# ---- 复制 ----


def _spawn_recorder(calls: list, *, ok=lambda args: True):
    def fake(args: list[str], *, stdin: bytes | None = None) -> bool:
        calls.append((args, stdin))
        return ok(args)

    return fake


def test_copy_prefers_configured_command(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, 'clipboard = "my-clip --cold"\n')
    calls: list = []
    monkeypatch.setattr(desktop, "_spawn", _spawn_recorder(calls))
    desktop.copy_text("链接", osc52=_never)
    assert calls == [(["my-clip", "--cold"], "链接".encode())]


def test_copy_falls_through_when_configured_command_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, 'clipboard = "nope"\n')
    calls: list = []
    monkeypatch.setattr(
        desktop, "_spawn", _spawn_recorder(calls, ok=lambda args: args == ["pbcopy"])
    )
    monkeypatch.setattr(desktop, "_clipboard_tool", lambda: ["pbcopy"])
    osc52: list[str] = []
    desktop.copy_text("x", osc52=osc52.append)
    assert [args for args, _ in calls] == [["nope"], ["pbcopy"]] and osc52 == []


def test_copy_uses_detected_tool_then_osc52(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, "")
    calls: list = []
    monkeypatch.setattr(desktop, "_spawn", _spawn_recorder(calls))
    monkeypatch.setattr(desktop, "_clipboard_tool", lambda: ["pbcopy"])
    desktop.copy_text("abc", osc52=_never)
    assert calls == [(["pbcopy"], b"abc")]
    # conftest 的缺省替身（没有本机工具）：退回 OSC 52
    monkeypatch.setattr(desktop, "_clipboard_tool", lambda: None)
    osc52: list[str] = []
    desktop.copy_text("abc", osc52=osc52.append)
    assert osc52 == ["abc"] and len(calls) == 1


def test_clipboard_tool_detection_needs_a_graphical_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """X11 / Wayland 的工具要有对应会话才认：SSH 进来的远端机器上装了 xclip 也到不了本地剪贴板。"""
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}")
    for env in ("WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(
        desktop,
        "_CLIPBOARD_TOOLS",
        (("wl-copy", "WAYLAND_DISPLAY"), ("xclip -selection clipboard", "DISPLAY"), ("pbcopy", None)),
    )
    assert desktop._clipboard_tool() == ["pbcopy"]
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert desktop._clipboard_tool() == ["wl-copy"]


# ---- 屏级反馈：拉不起浏览器不再静默 ----


def test_ctrl_o_warns_when_no_browser_can_open(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_tui_smoke import StubSource

    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    try:
        asyncio.run(_warn_flow(monkeypatch))
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


async def _warn_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "open_url", lambda url: False)
    app = FeedScopeApp()
    async with app.run_test(size=(90, 30)) as pilot:
        assert await settle(pilot, lambda: len(app.main.items) == 3)
        await pilot.press("ctrl+o")
        assert "打不开浏览器" in last_notification(app)
        assert "Ctrl+Y" in last_notification(app), "指路：复制链接 / 配置 browser 命令"
