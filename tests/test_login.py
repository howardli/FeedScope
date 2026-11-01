"""login.py 的 cookie 判定 / 登出逻辑单测：假 context 替代 Playwright（不碰浏览器）。

覆盖：X 判登要 auth_token+ct0 成对、知乎判登 z_c0 单枚、登出只清声明的 cookie。
is_logged_in / logout 的 session 装配路径由 TUI 流程测试（test_account）用
monkeypatch 覆盖，这里不重复。
"""

from __future__ import annotations

import asyncio

import pytest

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core.registry import get_source
from feedscope.login import _clear_cookies, _has_all_cookies


class FakeContext:
    """最小 Playwright context 替身：cookies()/clear_cookies() 只按名字操作。"""

    def __init__(self, names: set[str]) -> None:
        self._names = set(names)
        self.cleared: list[str | None] = []

    async def cookies(self, url: str) -> list[dict]:
        return [{"name": n, "value": "v"} for n in sorted(self._names)]

    async def clear_cookies(self, name: str | None = None) -> None:
        self.cleared.append(name)
        self._names.discard(name)


@pytest.fixture()
def x():
    return get_source("x")


@pytest.fixture()
def zhihu():
    return get_source("zhihu")


def test_has_all_cookies_x_needs_both(x) -> None:
    assert asyncio.run(_has_all_cookies(FakeContext({"auth_token", "ct0"}), x)) is True
    assert asyncio.run(_has_all_cookies(FakeContext({"auth_token"}), x)) is False
    assert asyncio.run(_has_all_cookies(FakeContext(set()), x)) is False


def test_has_all_cookies_zhihu_single(zhihu) -> None:
    assert asyncio.run(_has_all_cookies(FakeContext({"z_c0"}), zhihu)) is True
    assert asyncio.run(_has_all_cookies(FakeContext(set()), zhihu)) is False


def test_clear_cookies_x_returns_had_and_clears_pair(x) -> None:
    ctx = FakeContext({"auth_token", "ct0"})
    assert asyncio.run(_clear_cookies(ctx, x)) is True
    assert set(ctx.cleared) == {"auth_token", "ct0"}
    assert ctx._names == set()


def test_clear_cookies_absent_returns_false_and_leaves_others(x) -> None:
    ctx = FakeContext({"some_other"})
    assert asyncio.run(_clear_cookies(ctx, x)) is False
    assert ctx._names == {"some_other"}  # 只清声明的，别的不动
