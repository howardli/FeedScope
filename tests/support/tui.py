"""Textual Pilot 的轮询等待与通知读取（worker / 事件循环需要喘息）。"""

from __future__ import annotations

import asyncio

from textual.pilot import Pilot


async def settle(pilot: Pilot, cond, timeout: float = 5.0) -> bool:
    """轮询等条件成立：worker / 事件循环需要喘息。默认 5s——CI 慢机的冷启动
    常超 3s（历史上 test_account 因此闪断），只在失败路径上多等，不拖慢通过路径。"""
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        await pilot.pause()
        if cond():
            return True
    return cond()


def last_notification(app) -> str:
    """最近一条 notify 的正文（Textual 的通知面板在 _notifications）。"""
    notes = list(getattr(app, "_notifications", []))
    return str(notes[-1].message) if notes else ""
