"""JSON 落盘原语：原子写与紧凑文本形态的唯一实现。

原子写 = 先写同目录临时文件再 os.replace：读者要么读到旧文件、要么读到新
文件，绝无半截（崩溃 / 并发读到半截文件是数据损坏最常见的人工来源）。
config（TOML 文本）/ store / feed_cache / browser 的 UA 缓存全经这里写，别处
不再手写「tmp + replace」。

失败抛 OSError——「写失败怎么办」是调用方的策略（配置向上抛给用户、已读
状态与缓存内存照改只记日志），原语不替它决定。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def json_text(data: Any) -> str:
    """紧凑 JSON 文本（非 ASCII 原样写、无多余空白）：落盘的唯一文本形态。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def write_atomic(path: Path, text: str) -> None:
    """文本原子落盘（临时文件 + os.replace）；父目录不存在就建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump_atomic(path: Path, data: Any) -> None:
    """JSON 对象原子落盘（= write_atomic + json_text）。"""
    write_atomic(path, json_text(data))
