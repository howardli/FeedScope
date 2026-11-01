"""sources.toml 用户覆写基线测试：启用源 / 隐藏流 / 容错 / 写入往返。

conftest 已把 FEEDSCOPE_CONFIG 隔离到临时目录；本文件按用例覆写成
tmp_path 下的真实文件，get_source 每次返回新实例，读的就是新配置。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import feedscope.sources  # noqa: F401  import 即注册（单跑本文件时注册表才是满的）
from feedscope.core.config import (
    describe,
    enabled_ids,
    hidden_feeds,
    is_configured,
    save_enabled,
)
from feedscope.core.registry import get_source


def _use_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> Path:
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))
    return p


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


# ---- 启用源（enabled）：读 / 容错 / 写入往返 ----


def test_enabled_ids_absent_means_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """文件不存在 / 文件里没有 enabled = None（默认全启用，老配置不受影响）。"""
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(tmp_path / "absent.toml"))
    assert enabled_ids() is None
    _use_config(monkeypatch, tmp_path, '[zhihu]\nhide = ["follow"]\n')
    assert enabled_ids() is None


def test_enabled_ids_reads_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_config(monkeypatch, tmp_path, 'enabled = ["zhihu"]\n')
    assert enabled_ids() == ["zhihu"]


def test_enabled_ids_bad_type_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_config(monkeypatch, tmp_path, 'enabled = "zhihu"\n')  # 应为列表
    assert enabled_ids() is None


def test_is_configured_tracks_file_existence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(tmp_path / "fresh" / "sources.toml"))
    assert not is_configured()  # 首启判定：目录都还没有
    save_enabled(["zhihu"])
    assert is_configured()
    assert enabled_ids() == ["zhihu"]


def test_save_enabled_roundtrip_preserves_hide(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = _use_config(monkeypatch, tmp_path, '[zhihu]\nhide = ["follow"]\n')
    save_enabled(["x", "zhihu"])

    assert enabled_ids() == ["x", "zhihu"]  # 读回一致
    assert hidden_feeds("zhihu") == ["follow"]  # 已有 hide 段保留
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#") and "enabled" in text  # 注释头 + enabled 行
    assert tomllib.loads(text)  # 手写的 TOML 必须能被标准库解析
    assert not list(tmp_path.glob("*.tmp"))  # 原子写不留临时文件


def test_save_enabled_overwrite_drops_unknown_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """覆写只保留认识的键（enabled + 各源 hide）：未知键丢弃，文件仍是合法 TOML。"""
    p = _use_config(
        monkeypatch, tmp_path, 'note = "手写备注"\n[x]\nfoo = 1\nhide = ["foryou"]\n'
    )
    save_enabled(["zhihu"])
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["enabled"] == ["zhihu"]
    assert data["x"] == {"hide": ["foryou"]}  # hide 在、foo 不在


def test_describe_reports_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_config(monkeypatch, tmp_path, 'enabled = ["zhihu"]\n[zhihu]\nhide = ["follow"]\n')
    d = describe()
    assert "启用 1 源" in d and "zhihu" in d and "1 隐藏" in d
