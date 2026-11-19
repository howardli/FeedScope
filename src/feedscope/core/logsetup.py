"""日志自诊：--verbose / --log-file 的统一装配与默认落盘。

- 默认**总是**落盘（INFO 级，用户日志目录下滚动 1MB×3）：报障时「日志已在」
  远好过「请开 --verbose 复现一次」——既有 33 处 logger 调用零改动自动受益
- --verbose 提到 DEBUG；--log-file 换位置（环境变量 FEEDSCOPE_LOG_FILE 同效）
- TUI 模式**绝不**写 stderr：Textual 接管终端后任何裸字节都会插进重绘流
  串屏；只有 CLI 子命令 + --verbose 才镜像 stderr（add_stderr_mirror）
- 装配在进程入口先行（cli.main 里语言/配置初始化之前）：config 读取期的
  警告也要进文件——但 help 文案要等语言定、argparse 还没跑，入口只能手动
  预扫 argv（见 cli._early_logging 的口径说明）
- 日志目录建不出来（权限等）降级 NullHandler：日志绝不反过来挡启动
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_path

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 1_000_000
_BACKUPS = 3
_MIRROR_NAME = "feedscope-stderr-mirror"

_configured = False
_active_path: Path | None = None


def log_path() -> Path:
    """日志文件位置：FEEDSCOPE_LOG_FILE 覆盖，缺省用户日志目录（惯例同 config_path）。"""
    if p := os.environ.get("FEEDSCOPE_LOG_FILE"):
        return Path(p)
    return user_log_path("feedscope") / "feedscope.log"


def setup_logging(
    *, verbose: bool = False, path: Path | None = None, force: bool = False
) -> Path | None:
    """装配 root logger（幂等；force 供测试反复装配/换位）。返回实际日志路径。

    返回 None = 目录建不出来已降级 NullHandler（调用方不该拿它做事）。
    幂等语义：二次调用不动既有 handler——入口预扫一次、正式 parse 后如需
    换位（--log-file=VALUE 形式预扫看不见）显式传 force=True。
    """
    global _configured, _active_path
    if _configured and not force:
        return _active_path
    root = logging.getLogger()
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
    target = path or log_path()
    used: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FMT))
        root.addHandler(file_handler)
        used = target
    except OSError:
        root.addHandler(logging.NullHandler())  # 落不了盘也别把异常抛给启动
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    _configured = True
    _active_path = used
    return used


def active_path() -> Path:
    """当前实际写入的日志位置（未装配/落盘降级时回落约定位置——doctor 展示用）。"""
    return _active_path or log_path()


def add_stderr_mirror() -> None:
    """CLI 子命令 + --verbose 时镜像 stderr（TUI 绝不调用：会串屏）。幂等。

    幂等判据用 handler 名字而不是类形状：宿主可能自带各种 StreamHandler
    （pytest 的捕获、别的库的控制台输出），按形状判重会误判「已镜像」。
    """
    if any(h.name == _MIRROR_NAME for h in logging.getLogger().handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_MIRROR_NAME)
    handler.setFormatter(logging.Formatter(_FMT))
    logging.getLogger().addHandler(handler)
