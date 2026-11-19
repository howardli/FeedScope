"""日志自诊体系（--verbose / --log-file / 默认落盘）与 CLI 入口接线。"""

from __future__ import annotations

import logging

import pytest

from feedscope import cli
from feedscope.core import logsetup


@pytest.fixture()
def fresh_logging(monkeypatch: pytest.MonkeyPatch):
    """logsetup 的进程级开关按用例复位（真实运行只装配一次，测试要反复来）。"""
    monkeypatch.setattr(logsetup, "_configured", False)
    monkeypatch.setattr(logsetup, "_active_path", None)
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for h in root.handlers:
        root.removeHandler(h)
        h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_setup_logging_writes_file_with_levels(tmp_path, fresh_logging) -> None:
    log = tmp_path / "fs.log"
    used = logsetup.setup_logging(path=log)
    assert used == log and log.exists()
    logging.getLogger("feedscope.test").warning("警告行")
    logging.getLogger("feedscope.test").debug("调试行")
    text = log.read_text(encoding="utf-8")
    assert "WARNING" in text and "警告行" in text and "feedscope.test" in text
    assert "调试行" not in text, "默认 INFO：debug 不落盘"

    other = tmp_path / "verbose.log"
    logsetup.setup_logging(verbose=True, path=other, force=True)
    logging.getLogger("feedscope.test").debug("调试行2")
    assert "调试行2" in other.read_text(encoding="utf-8"), "--verbose 提到 DEBUG"


def test_setup_logging_idempotent_and_env_path(
    tmp_path, fresh_logging, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = logsetup.setup_logging(path=tmp_path / "a.log")
    again = logsetup.setup_logging(path=tmp_path / "b.log")  # 未 force：不动
    assert first == again == tmp_path / "a.log"
    moved = logsetup.setup_logging(path=tmp_path / "b.log", force=True)  # 显式换位
    assert moved == tmp_path / "b.log"
    monkeypatch.setenv("FEEDSCOPE_LOG_FILE", str(tmp_path / "env.log"))
    assert logsetup.log_path() == tmp_path / "env.log"


def test_add_stderr_mirror_once(fresh_logging) -> None:
    """幂等只挂一份镜像（按 handler 名判重——宿主自带的 StreamHandler 不算）；
    写入走 handler 的 stream（换成 StringIO 断言，不与 pytest 的捕获插件纠缠）。"""
    import io

    logsetup.setup_logging(path=None)
    logsetup.add_stderr_mirror()
    logsetup.add_stderr_mirror()  # 幂等：不重复挂
    mirrors = [h for h in logging.getLogger().handlers if h.name == "feedscope-stderr-mirror"]
    assert len(mirrors) == 1, "镜像 handler 只有一份"
    buf = io.StringIO()
    mirrors[0].stream = buf
    logging.getLogger("feedscope.test").warning("镜像行")
    assert "镜像行" in buf.getvalue(), "镜像写的是同一条记录"


def test_cli_early_logging_and_doctor_line(
    tmp_path, fresh_logging, monkeypatch, capsys
) -> None:
    """--log-file PATH 从预扫就生效（config 读取期的警告也进文件）；doctor 报日志路径。"""
    log = tmp_path / "where.log"
    assert cli.main(["--log-file", str(log), "doctor"]) in (0, 1)  # 本机 Chrome 与否都合法
    assert log.exists(), "预扫装配在 doctor 执行前就落盘"
    out = capsys.readouterr().out
    assert "where.log" in out, "doctor 的信息行列出日志位置"
    assert "日志" in out
