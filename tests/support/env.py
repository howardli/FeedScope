"""用户配置的按用例覆写（conftest 的隔离 fixture 已把 FEEDSCOPE_CONFIG 指进临时目录）。"""

from __future__ import annotations

from pathlib import Path

import pytest


def use_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> Path:
    """按用例覆写 FEEDSCOPE_CONFIG 成 tmp_path 下的真实文件，返回文件路径。"""
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))
    return p
