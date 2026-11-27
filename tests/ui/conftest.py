"""UI 子树共享的 fixture：桩源注册表与「开浏览器」的记录器。

桩源本体在 tests/support/stubs.py；这里只负责把它们装进注册表、用完恢复。
注册表的替换必须成对出现——stub_registry 曾在三个测试模块里各写一份。
"""

from __future__ import annotations

import webbrowser

import pytest

from feedscope.core.registry import _REGISTRY
from tests.support.stubs import (
    FailingSource,
    StubSource,
    StubSourceNoComments,
    StubSourceTwo,
)


def _swap_registry(*classes) -> None:
    """把注册表整体换成给定桩源，用完恢复（各 fixture 的公共体）。"""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    for cls in classes:
        _REGISTRY[cls.source_id] = cls
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


@pytest.fixture()
def stub_registry():
    """只留基础 stub 源。"""
    yield from _swap_registry(StubSource)


@pytest.fixture()
def failing_registry():
    """只留失败注入源：驱动各错误出口。"""
    yield from _swap_registry(FailingSource)


@pytest.fixture()
def two_source_registry():
    """两个 stub 源：驱动「Tab 循环 + 顶部 tab 点击」两条切源路径。"""
    yield from _swap_registry(StubSource, StubSourceTwo)


@pytest.fixture()
def no_comments_registry():
    """只留无评论层的 stub 源：驱动详情屏对 supports_comments=False 的退场。"""
    yield from _swap_registry(StubSourceNoComments)


@pytest.fixture()
def opened(monkeypatch):
    """记录而非真开浏览器；返回被「打开」的 URL 列表。"""
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: urls.append(url) or True)
    yield urls
