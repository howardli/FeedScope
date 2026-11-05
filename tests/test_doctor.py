"""doctor 环境自检测试：平台分支（Chrome 发现 / Python 版本）与退出码。

Chrome 发现用 monkeypatch 假造存在性（不依赖本机装没装 Chrome）；版本分支
在 3.11+ 解释器上只测通过侧（3.10 的拒绝分支由 CI 矩阵以外的环境自证）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from feedscope import doctor
from feedscope.core import const


class _FakePath(str):
    def exists(self) -> bool:
        return True


def test_find_chrome_macos_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        const, "CHROME_PATH", _FakePath("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    )
    assert const.find_chrome() == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_find_chrome_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "CHROME_PATH", const.Path("/nonexistent/Chrome"))
    monkeypatch.setattr(const, "shutil", type("S", (), {"which": staticmethod(lambda name: f"/usr/bin/{name}")}))
    assert const.find_chrome() == "/usr/bin/google-chrome"  # 候选名按序第一个命中


def test_find_chrome_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "CHROME_PATH", const.Path("/nonexistent/Chrome"))
    monkeypatch.setattr(const, "shutil", type("S", (), {"which": staticmethod(lambda name: None)}))
    assert const.find_chrome() == ""


def test_run_checks_all_pass(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(doctor, "PROFILE_DIR", Path("/nonexistent-profile"))  # 信息项不依赖真机
    monkeypatch.setattr(const, "CHROME_PATH", const.Path("/nonexistent/Chrome"))
    monkeypatch.setattr(const, "shutil", type("S", (), {"which": staticmethod(lambda name: "/usr/bin/chrome")}))
    assert doctor.run_checks() == 0
    out = capsys.readouterr().out
    assert "[OK]" in out and "Google Chrome" in out


def test_run_checks_chrome_missing_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(doctor, "PROFILE_DIR", Path("/nonexistent-profile"))
    monkeypatch.setattr(const, "CHROME_PATH", const.Path("/nonexistent/Chrome"))
    monkeypatch.setattr(const, "shutil", type("S", (), {"which": staticmethod(lambda name: None)}))
    assert doctor.run_checks() == 1
    out = capsys.readouterr().out
    assert "[FAIL] Google Chrome" in out and "存在失败项" in out
