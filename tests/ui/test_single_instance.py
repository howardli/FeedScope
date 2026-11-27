"""单实例保护：同一份浏览器 profile / 已读状态，不该有两个 feedscope 同时在写。

- profile 锁：Chrome 的 SingletonLock 属于一个还活着的进程时，另一个实例不能
  把它当「残留」删掉（删了随后就是两个 Chrome 共用一个 profile：登录态互相
  覆盖、数据库损坏）——启动失败并把 pid 说清；真残留（进程已不在、别的机器
  拷来的 profile）照常清
- 状态锁：state.json / favorites.json 是整文件覆写，第二个 TUI 实例照常使用
  （不挡），但要提醒两边会互相覆盖
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys

import pytest

from feedscope.core import browser
from feedscope.core.browser import ChannelTab
from feedscope.core.registry import _REGISTRY
from feedscope.core.store import StateLock
from feedscope.ui.app import FeedScopeApp
from tests.support.tui import last_notification, settle


def _dead_pid() -> int:
    """一个确定已退出的进程号（SingletonLock 记的就是「主机名-pid」）。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ---- profile 锁 ----


def test_truly_stale_locks_are_removed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """异常退出残留（进程已不在）与别的机器拷来的 profile（主机名对不上）：清掉，Chrome 才肯启动。"""
    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path)
    (tmp_path / "SingletonLock").symlink_to(f"{socket.gethostname()}-{_dead_pid()}")
    (tmp_path / "SingletonSocket").symlink_to("anything")
    (tmp_path / "SingletonCookie").write_text("x", encoding="utf-8")
    browser.drop_stale_locks()
    assert list(tmp_path.iterdir()) == []
    (tmp_path / "SingletonLock").symlink_to("someone-elses-host-1234")
    browser.drop_stale_locks()
    assert list(tmp_path.iterdir()) == []


def test_lock_of_a_live_process_is_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """另一个 feedscope 正在用这个 profile：不删它的锁，报出 owner 的 pid（本测试进程充当 owner）。"""
    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path)
    lock = tmp_path / "SingletonLock"
    lock.symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    with pytest.raises(browser.ProfileInUse, match=f"pid {os.getpid()}"):
        browser.drop_stale_locks()
    assert lock.is_symlink(), "活进程的锁不能删"
    os.replace(lock, tmp_path / "other")  # owner 退出后锁成了残留：下次启动清得掉
    lock.symlink_to(f"{socket.gethostname()}-{_dead_pid()}")
    browser.drop_stale_locks()
    assert not lock.is_symlink()


def test_profile_in_use_reaches_the_ui_unwrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话启动撞上的 ProfileInUse 已是完整文案：fetch 的通用包装不再套一层「请求失败：」。"""

    class _BusySession:
        async def run(self, op, *args, **kwargs):
            raise browser.ProfileInUse("profile busy (pid 7)")

    monkeypatch.setattr(browser, "get_session", lambda: _BusySession())
    with pytest.raises(browser.ProfileInUse, match=r"^profile busy \(pid 7\)$"):
        asyncio.run(ChannelTab("https://example.com/", name="t").fetch("https://x/a", source="t"))


# ---- 状态锁 ----


def test_state_lock_admits_one_holder() -> None:
    """同一份状态只许一个实例持锁（flock 按打开的文件描述计：同进程的第二个实例也算「别的实例」）。"""
    first, second = StateLock(), StateLock()
    assert first.try_acquire() is True
    assert second.try_acquire() is False
    first.release()
    assert second.try_acquire() is True, "关 fd 即释放；异常退出由内核代劳"
    second.release()
    second.release()  # 幂等


def test_second_app_is_warned_but_not_blocked() -> None:
    """第二个 TUI 实例照常使用（多套配置并行的用户有自己的理由），但要知道两边会互相覆盖。"""
    from tests.support.stubs import StubSource

    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubSource.source_id] = StubSource
    try:
        asyncio.run(_two_instances_flow())
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


async def _two_instances_flow() -> None:
    holder = StateLock()  # 相当于另一个终端里先开着的那个 feedscope
    assert holder.try_acquire()
    try:
        first = FeedScopeApp()
        async with first.run_test(size=(90, 24)) as pilot:
            assert await settle(pilot, lambda: len(first.main.items) == 3), "不挡使用，照常出内容"
            assert "互相覆盖" in last_notification(first)
    finally:
        holder.release()
    alone = FeedScopeApp()
    async with alone.run_test(size=(90, 24)) as pilot:
        assert await settle(pilot, lambda: len(alone.main.items) == 3)
        assert "互相覆盖" not in last_notification(alone), "只有一个实例：不提醒"
