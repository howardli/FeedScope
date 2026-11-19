"""日志自诊体系（--verbose / --log-file / 默认落盘）与 CLI 入口接线。"""

from __future__ import annotations

import logging

import pytest
from conftest import use_config

from feedscope import __version__, cli
from feedscope.core import feed_cache, logsetup
from feedscope.core.config import Subscription, rss_feeds, update_feeds


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


def test_cli_version_flag(capsys) -> None:
    """--version 打印版本（单源 __version__），exit 0。"""
    with pytest.raises(SystemExit) as ei:
        cli.build_parser().parse_args(["--version"])
    assert ei.value.code == 0
    assert capsys.readouterr().out.strip() == f"feedscope {__version__}"


def test_cli_cache_status_and_clear(monkeypatch, tmp_path, capsys) -> None:
    """cache 子命令：无参报数（目录/文件数/字节），clear 清空（派生数据无损）。"""
    monkeypatch.setenv("FEEDSCOPE_FEED_CACHE", str(tmp_path / "feeds"))
    from feedscope.core.models import FeedItem

    feed_cache.save("stub", "one", [FeedItem("stub", "i1", "t", "u")])
    feed_cache.save("stub", "two", [FeedItem("stub", "i2", "t", "u")])
    assert cli.main(["cache"]) == 0
    out = capsys.readouterr().out
    assert "2 个缓存文件" in out and str(tmp_path / "feeds") in out

    assert cli.main(["cache", "clear"]) == 0
    assert "已清空缓存（删 2 个文件）" in capsys.readouterr().out
    assert cli.main(["cache"]) == 0
    assert "0 个缓存文件" in capsys.readouterr().out


def test_cli_rss_rename(monkeypatch, tmp_path, capsys) -> None:
    """rss rename：改名落盘可读回；不存在的 key 退 1。"""
    use_config(monkeypatch, tmp_path, 'language = "zh-CN"\n')
    update_feeds("rss", [Subscription("a", "A", "https://a.test/f")])
    assert cli.main(["rss", "rename", "a", "新名字"]) == 0
    assert "已改名 a：新名字" in capsys.readouterr().out
    assert rss_feeds()[0].label == "新名字"
    assert cli.main(["rss", "rename", "nope", "x"]) == 1
