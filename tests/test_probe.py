"""feedscope probe 巡检逻辑：用假源换进注册表，离线验证 OK/FAIL/SKIP 与退出码。

假源只在 fixture 里注册（换掉整个注册表、测完还原）——绝不在模块级
@register，否则收集阶段就污染其他测试看到的真实注册表。
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from feedscope.core.models import DetailDoc, FeedItem
from feedscope.core.source import (
    DetailLoader,
    LoginRequired,
    Source,
    SourceError,
    StaticFeedLoader,
)


class _OkDetail(DetailLoader):
    def __init__(self, empty: bool = False) -> None:
        self.doc = DetailDoc(title="" if empty else "帖子标题", description_md="" if empty else "正文")

    async def load_more(self) -> list:
        return []

    async def close(self) -> None:
        pass


class _OkSource(Source):
    source_id = "okfake"
    label = "正常源"
    feeds: ClassVar[list[str]] = ["a"]
    supports_detail = True
    detail_kinds = frozenset({"topic"})

    async def open_feed(self, name: str) -> StaticFeedLoader:
        return StaticFeedLoader(
            [
                FeedItem(
                    source="okfake", id="1", title="帖子", url="https://x.test/1", kind="topic"
                )
            ]
        )

    async def open_detail(self, item: FeedItem) -> _OkDetail:
        return _OkDetail()


class _EmptyDetailSource(_OkSource):
    """详情全空：标题/正文/楼层都没有 = 详情页改版，应 FAIL。"""

    source_id = "emptyfake"
    label = "空详情源"

    async def open_detail(self, item: FeedItem) -> _OkDetail:
        return _OkDetail(empty=True)


class _BrokenSource(Source):
    source_id = "badfake"
    label = "坏源"
    feeds: ClassVar[list[str]] = ["a"]

    async def open_feed(self, name: str) -> StaticFeedLoader:
        raise SourceError("坏源「a」解析到 0 条内容，站点可能已改版（feedscope probe 可复查）")


class _LockedSource(Source):
    source_id = "lockfake"
    label = "登录源"
    feeds: ClassVar[list[str]] = ["a"]

    async def open_feed(self, name: str) -> StaticFeedLoader:
        raise LoginRequired("登录源 需要登录")


@pytest.fixture
def fake_registry():
    """清空注册表供本测试注册假源；测完还原成真实源。"""
    from feedscope.core import registry

    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    yield registry
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def _run(capsys: pytest.CaptureFixture[str], only: list[str] = ()) -> tuple[int, str]:
    from feedscope.probe import run_probes

    code = run_probes(only)
    return code, capsys.readouterr().out


def test_probe_all_ok(fake_registry, capsys: pytest.CaptureFixture[str]) -> None:
    fake_registry.register(_OkSource)
    code, out = _run(capsys)
    assert code == 0
    assert "[OK]   正常源 a - 1 条" in out
    assert "[OK]   正常源 详情 - 「帖子标题」0 回答" in out  # 0 回复不算坏
    assert "2 通过 / 0 失败 / 0 跳过" in out  # 信息流 + 详情各计一个 OK


def test_probe_reports_fail_and_skip(fake_registry, capsys: pytest.CaptureFixture[str]) -> None:
    for cls in (_OkSource, _BrokenSource, _LockedSource):
        fake_registry.register(cls)
    code, out = _run(capsys)
    assert code == 1
    assert "[FAIL] 坏源 a" in out and "改版" in out
    assert "[SKIP] 登录源 a" in out
    assert "2 通过 / 1 失败 / 1 跳过" in out
    assert "原始样本在" in out  # 失败时提示样本位置


def test_probe_empty_detail_fails(fake_registry, capsys: pytest.CaptureFixture[str]) -> None:
    fake_registry.register(_EmptyDetailSource)
    code, out = _run(capsys)
    assert code == 1
    assert "[FAIL] 空详情源 详情 - 标题/正文/楼层全空" in out


def test_probe_filter_and_unknown_id(fake_registry, capsys: pytest.CaptureFixture[str]) -> None:
    fake_registry.register(_OkSource)
    fake_registry.register(_LockedSource)
    code, out = _run(capsys, ["okfake"])  # 只测正常源
    assert code == 0
    assert "登录源" not in out
    code, out = _run(capsys, ["nope"])
    assert code == 2 and "未知源 id：nope" in out


def test_probe_keeps_env_sample_dir(
    fake_registry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """FEEDSCOPE_SAMPLE_DIR 已设（conftest 指到 tmp）时 probe 沿用，不强设缓存目录。"""
    fake_registry.register(_OkSource)
    code, out = _run(capsys)
    assert code == 0
    assert str(tmp_path / "samples") in out
