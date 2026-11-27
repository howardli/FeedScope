"""OPML 进出：订阅表与外部阅读器互通（RSS 订阅的标准交换格式）。

用 stdlib ElementTree 解析（嵌套目录拍平），导出手写模板——结构固定，不值得
引库。读不了 / 不是合法 OPML 抛 BadSubscriptionFile（用法级错误：CLI 退 2，
与 argparse 约定一致）。函数按 source_id 操作订阅表——实践中只有 RSS 订阅
走 OPML，但这里不写死源。
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import feedscope.sources  # noqa: F401  import 即注册：保留字读源声明
from feedscope.core import config
from feedscope.core.config import Subscription
from feedscope.core.errors import BadSubscriptionFile
from feedscope.core.i18n import t
from feedscope.core.registry import get_class

logger = logging.getLogger(__name__)


def read_opml_file(path: str | Path) -> str:
    """读 OPML 文件文本（`~` 展开）；读不了抛 BadSubscriptionFile（CLI/TUI 同一出口）。"""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise BadSubscriptionFile(t("subs.opml_unreadable", error=exc)) from None


def parse_opml(text: str) -> list[tuple[str, str]]:
    """OPML 文本 -> [(label, xmlUrl)]；嵌套目录拍平，无 xmlUrl 的节点只当容器。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise BadSubscriptionFile(t("subs.opml_invalid", error=exc)) from None
    out: list[tuple[str, str]] = []
    for outline in root.iter("outline"):
        url = (outline.get("xmlUrl") or "").strip()
        if not url:
            continue
        label = (outline.get("text") or outline.get("title") or "").strip()
        out.append((label, url))
    return out


def import_opml(text: str, source_id: str) -> tuple[list[Subscription], list[str]]:
    """OPML 批量导入（幂等合并）：返回 (新增订阅, 跳过原因列表)。

    文件读不了 / 不是合法 OPML 抛 BadSubscriptionFile（用法级错误）。
    已订阅的地址（含 OPML 内部重复）跳过；label 用 outline 的 text/title，
    缺了回 key；一次落盘（中途失败不写半截配置）。
    """
    feeds = config.feeds_for(source_id)
    cls = get_class(source_id)
    reserved = cls.reserved_feed_keys if cls is not None else ()
    known_urls = {f.url for f in feeds}
    # 保留字并进避让集（与单条添加的派生路径同一条规则）：OPML 里的
    # all.com 订阅派生 all-2，不会产出被读取侧静默跳过的撞名 key
    known_keys = {f.key for f in feeds} | set(reserved)
    added: list[Subscription] = []
    skipped: list[str] = []
    for label, url in parse_opml(text):
        if url in known_urls:
            skipped.append(t("subs.opml_dup", name=label or url))
            continue
        key = config.derive_key(url, known_keys)
        known_urls.add(url)
        known_keys.add(key)
        added.append(Subscription(key=key, label=label or key, url=url))
    if added:
        config.update_feeds(source_id, [*feeds, *added], drop_enabled_when_empty=True)
    return added, skipped


_XML_QUOTE = '"'


def _attr(value: str) -> str:
    """字符串 -> XML 属性值：双引号包裹，& < > 与引号本体转义。"""
    escaped = escape(value, {_XML_QUOTE: "&quot;"})
    return f"{_XML_QUOTE}{escaped}{_XML_QUOTE}"


def export_opml(feeds: list[Subscription]) -> str:
    """订阅表 -> OPML 2.0 文本（标准阅读器都认，可再迁去别家）。"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        f"  <head><title>{t('subs.opml_title')}</title></head>",
        "  <body>",
    ]
    lines += [
        f'    <outline type="rss" text={_attr(f.label)} xmlUrl={_attr(f.url)} />' for f in feeds
    ]
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)
