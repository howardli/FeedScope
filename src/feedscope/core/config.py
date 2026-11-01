"""用户配置：sources.toml 隐藏不想看的内置信息流。

- 路径：FEEDSCOPE_CONFIG 环境变量（测试、多套配置切换），缺省用户配置目录下
  sources.toml（platformdirs，Linux 为 ~/.config/feedscope/sources.toml）
- 容错哲学：配置问题绝不挡启动——文件不存在 = 全默认；TOML 语法错 / 结构
  不符 = 警告日志 + 当作不存在（feedscope doctor 可诊断）。
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

from platformdirs import user_config_path

logger = logging.getLogger(__name__)


def config_path() -> Path:
    if p := os.environ.get("FEEDSCOPE_CONFIG"):
        return Path(p)
    return user_config_path("feedscope") / "sources.toml"


def _read() -> tuple[dict, str]:
    """读配置返回 (数据, 状态描述)；不存在 / 坏 = ({}, 原因)，不抛异常。"""
    path = config_path()
    try:
        with path.open("rb") as f:
            return tomllib.load(f), ""
    except FileNotFoundError:
        return {}, "未创建（全部用内置默认）"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("用户配置 %s 无法解析，已忽略：%s", path, exc)
        return {}, f"无法解析（{exc}）"


def hidden_feeds(source_id: str) -> list[str]:
    """读某源要隐藏的流名列表。

    期望结构（本源的结构不符 = 警告并当全默认，不波及其他源）：

        [zhihu]
        hide = ["follow"]
    """
    section = _read()[0].get(source_id)
    if not isinstance(section, dict):
        return []
    hidden = section.get("hide")
    if hidden is not None and (
        not isinstance(hidden, list) or not all(isinstance(n, str) for n in hidden)
    ):
        logger.warning("配置 [%s] hide 应为流名列表，已忽略", source_id)
        hidden = []
    return list(hidden or [])


def describe() -> str:
    """doctor 用的一句话状态：未创建 / 解析失败原因 / N 源 K 隐藏。"""
    data, reason = _read()
    if reason:
        return reason
    n_hidden = n_sources = 0
    for section in data.values():
        if not isinstance(section, dict):
            continue
        n_sources += 1
        hide = section.get("hide") or []
        n_hidden += len(hide) if isinstance(hide, list) else 0
    return f"{n_sources} 源覆写：{n_hidden} 隐藏" if n_sources else "未创建（全部用内置默认）"
