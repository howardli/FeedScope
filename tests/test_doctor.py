"""doctor 环境自检测试：平台分支（Chrome 发现 / Python 版本）与退出码。

Chrome 发现用 monkeypatch 假造存在性（不依赖本机装没装 Chrome）；版本分支
在 3.11+ 解释器上只测通过侧（3.10 的拒绝分支由 CI 矩阵以外的环境自证）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import use_config

from feedscope import doctor
from feedscope.core import const
from feedscope.core.store import state_path


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


# ---- 配置体检：坏文件与「不挡启动但等于没生效」的设置 ----


def _healthy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境项全过，只留配置这一项给用例摆布。"""
    monkeypatch.setattr(doctor, "PROFILE_DIR", Path("/nonexistent-profile"))
    monkeypatch.setattr(const, "CHROME_PATH", const.Path("/nonexistent/Chrome"))
    monkeypatch.setattr(const, "shutil", type("S", (), {"which": staticmethod(lambda name: "/usr/bin/chrome")}))


def test_broken_config_is_a_failure(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path) -> None:
    """保存路径对坏文件拒写并指向 doctor——doctor 自己不能对坏文件报 OK。"""
    _healthy_env(monkeypatch)
    use_config(monkeypatch, tmp_path, 'enabled = ["rss"\n')  # TOML 语法错
    assert doctor.run_checks() == 1
    out = capsys.readouterr().out
    assert "[FAIL] 用户配置" in out and "无法解析" in out
    assert "全部通过" not in out


def test_config_lint_reports_settings_that_would_not_take_effect(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path
) -> None:
    """读取侧静默容错的每一种：坏语言 / 坏时区 / 未知源 id / 对不上流的 hide / 缺 url 的订阅 /
    label 类型错的订阅——doctor 逐条报出来，而不是只进日志。"""
    _healthy_env(monkeypatch)
    use_config(
        monkeypatch,
        tmp_path,
        'enabled = ["rss", "nope"]\nlanguage = "klingon"\ntimezone = "Mars/Olympus"\n'
        '[zhihu]\nhide = ["no_such_feed"]\n[rss.feeds]\n'
        'bad = { label = "no url" }\nweird = { url = "https://e.com/f.xml", label = 42 }\n',
    )
    assert doctor.run_checks() == 1
    out = capsys.readouterr().out
    for fragment in ("nope", "klingon", "Mars/Olympus", "zhihu: no_such_feed", "bad", "weird"):
        assert fragment in out, fragment
    assert out.count("[FAIL]") == 6  # 语言 / 时区 / 缺 url / label 类型 / 未知源 id / hide


def test_clean_config_passes_with_subscription_aware_vocab(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path
) -> None:
    """订阅 key 是合法的 hide 名（词表要认订阅），别把自己的订阅误报成对不上流。"""
    _healthy_env(monkeypatch)
    use_config(
        monkeypatch,
        tmp_path,
        'enabled = ["rss", "hn"]\nlanguage = "en"\ntimezone = "Asia/Tokyo"\n'
        '[rss.feeds]\nok = { url = "https://e.com/f.xml" }\n[rss]\nhide = ["all", "ok"]\n',
    )
    assert doctor.run_checks() == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out and "[WARN]" not in out and "全部通过" in out


def test_corrupt_backups_are_reported_as_warnings(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path) -> None:
    """数据文件损坏后的留底：不算失败，但用户该知道已读/收藏是从空态重新开始的。"""
    _healthy_env(monkeypatch)
    use_config(monkeypatch, tmp_path, "")
    state_path().parent.mkdir(parents=True, exist_ok=True)
    kept = state_path().parent / "favorites.json.corrupt-20260101-000000"
    kept.write_text("{", encoding="utf-8")
    assert doctor.run_checks() == 0
    out = capsys.readouterr().out
    assert "[WARN]" in out and kept.name in out and "1 条提醒" in out and "全部通过" in out
