"""样本落盘：把取到的原始响应写到磁盘，站点改版后修 parse 用。

- 开关：FEEDSCOPE_SAMPLE_DIR 环境变量（未设 = 关，零开销）；feedscope probe 自动设到
  ~/.cache/feedscope/samples/，平时 TUI 里完全不落盘
- 文件名 = 源标签--URL 路径清洗版（rand 等随机参数剔除、其余参数排序，
  重跑同名覆盖，样本目录始终是最新的）
- 一切落盘失败吞掉只留警告：这是调试辅助，绝不能反过来弄崩取数
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

logger = logging.getLogger(__name__)

# 剔除的查询参数：每次请求都变的随机值，留着文件名就永远不重名
_VOLATILE_PARAMS = {"rand", "_", "t", "ts", "_t", "timestamp", "nonce", "callback"}


def active_dir() -> Path | None:
    d = os.environ.get("FEEDSCOPE_SAMPLE_DIR")
    return Path(d) if d else None


def url_name(source: str, url: str) -> str:
    """URL -> 稳定样本文件名（不含扩展名），如 x--TweetDetail___variables=….json。

    查询参数排序 + 随机参数剔除，保证同一端点重跑同名（覆盖刷新）。
    源标签只保留 ASCII 字符（跨平台文件名安全，非 ASCII 全清后回退 src）。
    """
    u = urlparse(url)
    keep = "&".join(
        f"{k}={v}" for k, v in sorted(parse_qsl(u.query))
        if k not in _VOLATILE_PARAMS and len(v) <= 40
    )
    raw = u.path.strip("/") + ("_" + keep if keep else "")
    clean = re.sub(r"[^A-Za-z0-9._=-]+", "-", raw)[:100].strip("-.") or "root"
    src = re.sub(r"[^a-z0-9]+", "", source.lower()) or "src"
    return f"{src}--{clean}"


def _suffix(content: str | bytes) -> str:
    """按内容嗅探扩展名：JSON / HTML / 其他。"""
    head = content[:128].lstrip()
    if isinstance(head, bytes):
        head = head.decode("utf-8", "ignore")
    if head.startswith(("{", "[")):
        return ".json"
    if head.startswith("<"):
        return ".html"
    return ".txt"


def save(name: str, content: str | bytes) -> None:
    """写样本进 active_dir（未开启 = 无事发生）；失败吞掉只留警告。"""
    d = active_dir()
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else content.decode("utf-8", "ignore")
        (d / (name + _suffix(content))).write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("样本落盘失败 %s：%s", name, exc)
