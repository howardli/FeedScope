"""sources.toml 用户覆写基线测试：隐藏流 / 容错。

conftest 已把 FEEDSCOPE_CONFIG 隔离到不存在的临时路径；本文件按用例覆写成
tmp_path 下的真实文件，get_source 每次返回新实例，读的就是新配置。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.registry import get_source


def _use_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))


def test_no_config_file_all_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(tmp_path / "absent.toml"))
    z = get_source("zhihu")
    assert z.feeds == ["hot", "recommend", "follow"]


def test_hide_builtin_feed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_config(monkeypatch, tmp_path, '[zhihu]\nhide = ["follow", "hot"]\n')
    z = get_source("zhihu")
    assert "follow" not in z.feeds and "hot" not in z.feeds
    assert z.feeds == ["recommend"]  # 其余内置保留原顺序


def test_hide_unknown_feed_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_config(monkeypatch, tmp_path, '[zhihu]\nhide = ["nonexistent"]\n')
    z = get_source("zhihu")
    assert z.feeds == ["hot", "recommend", "follow"]  # 隐藏不存在的流 = 无事发生


def test_hide_bad_type_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_config(monkeypatch, tmp_path, '[zhihu]\nhide = "follow"\n')  # 应为列表
    z = get_source("zhihu")
    assert z.feeds == ["hot", "recommend", "follow"]


def test_malformed_toml_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_config(monkeypatch, tmp_path, "[zhihu\n")  # 语法错误
    z = get_source("zhihu")
    assert z.feeds == ["hot", "recommend", "follow"]
