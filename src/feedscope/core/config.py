"""用户配置：sources.toml 选启用的源 + 隐藏不想看的内置信息流 + 各源订阅表。

- 路径：FEEDSCOPE_CONFIG 环境变量（测试、多套配置切换），缺省用户配置目录下
  sources.toml（platformdirs，Linux 为 ~/.config/feedscope/sources.toml）
- 容错哲学：配置问题绝不挡启动——文件不存在 = 全默认；TOML 语法错 / 结构
  不符 = 警告日志 + 当作不存在（feedscope doctor 可诊断）。
- 写入只走 save_enabled（首启引导 / F2 管理界面）：保留各源 hide 段与
  feeds 订阅表（订阅是用户数据，覆写丢段 = 丢订阅；rss 的 feed 地址、
  reddit 的 subreddit 同款），无法识别的键丢弃；原子落盘（临时文件 +
  os.replace），不留半截文件。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

logger = logging.getLogger(__name__)

# rss 源的聚合流名（「全部」），保留字：订阅 key 与它同名会遮住聚合流
RSS_ALL_FEED = "all"


@dataclass(frozen=True)
class RssFeed:
    """一条 RSS 订阅：key 即信息流名（侧栏与 open_feed 的寻址键）。"""

    key: str
    label: str  # 侧栏显示名；配置缺省 = key 本身
    url: str


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


def feeds_for(source_id: str) -> list[RssFeed]:
    """读某源的订阅表（书写顺序即侧栏顺序），结构与坏条目逐条容错。

    期望结构（label 缺省用 key；url 必填）：

        [rss.feeds]
        ruanyq = { label = "阮一峰周刊", url = "https://….com/atom.xml" }
        hn = { url = "https://hnrss.org/frontpage" }

        [reddit.feeds]            # url 存 subreddit 站内地址，自解释
        rust = { url = "https://www.reddit.com/r/rust/" }

    条目不是表 / 缺 url = 警告并跳过该条，不波及其他订阅（同一容错粒度：
    文件级坏 = 全默认已在上层 _read，这里是条目级）。rss 另有保留字 all
    （聚合流名，订阅 key 撞它 = 跳过）；其他源的保留字在各源的添加入口拦
    （reddit 的 home/popular 与内置流撞名，见 subscriptions.add_subreddit）。
    """
    section = _read()[0].get(source_id)
    feeds_raw = section.get("feeds") if isinstance(section, dict) else None
    if feeds_raw is None:
        return []
    if not isinstance(feeds_raw, dict):
        logger.warning("配置 [%s] feeds 应为订阅表，已忽略", source_id)
        return []
    feeds: list[RssFeed] = []
    for key, entry in feeds_raw.items():
        if source_id == "rss" and key == RSS_ALL_FEED:
            logger.warning("订阅 key %r 与聚合流名 all 撞名，已跳过", key)
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not entry["url"]:
            logger.warning("订阅 %r 应为含非空 url 的表，已跳过", key)
            continue
        label = entry.get("label")
        if label is not None and not isinstance(label, str):
            logger.warning("订阅 %r 的 label 应为字符串，已回退用 key", key)
            label = None
        feeds.append(RssFeed(key=key, label=label or key, url=entry["url"]))
    return feeds


def rss_feeds() -> list[RssFeed]:
    """rss 源的订阅表（feeds_for 的 rss 特例，保留既有调用名）。"""
    return feeds_for("rss")


def enabled_ids() -> list[str] | None:
    """读顶层启用的源 id 列表；None = 未配置（默认全部启用，老文件只有 hide 的用户不受影响）。

    期望结构：

        enabled = ["zhihu", "x"]

    列表里的未知 id 不在此报错——注册表过滤在调用方（新装/卸载源后残留条目无害）。
    """
    enabled = _read()[0].get("enabled")
    if enabled is None:
        return None
    if not isinstance(enabled, list) or not all(isinstance(i, str) for i in enabled):
        logger.warning("配置 enabled 应为源 id 列表，已忽略（默认全部启用）")
        return None
    return list(enabled)


def is_configured() -> bool:
    """是否已完成首次设置（配置文件存在）。

    只看文件存在与否：TOML 坏文件是 doctor 的职责，不重新引导——
    引导保存会覆写，坏文件里可能还有用户手写的其他内容。
    """
    return config_path().exists()


def _toml_str(s: str) -> str:
    # json 字符串即合法 TOML 基本串（转义规则同源），不为此引入写库
    return json.dumps(s, ensure_ascii=False)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(s: str) -> str:
    """键/段名：能裸写就裸写（[rss.feeds] 好读），含特殊字符才引用 _toml_str。"""
    return s if _BARE_KEY.match(s) else _toml_str(s)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def _valid_feeds(raw: object) -> list[tuple[str, str, str]]:
    """feeds 表的写前校验：留 (key, label, url)，坏条目丢弃记日志。

    与 rss_feeds 的读校验同规则（表 + 非空 str url），保证读写往返一致；
    label 只收 str，缺省回 key——读侧重抛 RssFeed，写侧要的是行参数。
    """
    if raw is None:
        return []
    if not isinstance(raw, dict):
        logger.warning("覆写配置：feeds 应为订阅表，已丢弃 %r", raw)
        return []
    out: list[tuple[str, str, str]] = []
    for key, entry in raw.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not entry["url"]:
            logger.warning("覆写配置：订阅 %r 缺非空 url，已丢弃", key)
            continue
        label = entry.get("label")
        label = label if isinstance(label, str) and label else key
        out.append((key, label, entry["url"]))
    return out


def _render_config(ids: list[str] | None, data: dict) -> list[str]:
    """配置全量渲染为 TOML 行；ids 为 None = 不写 enabled 行（缺省 = 全启用）。

    只写本工具认识的键：enabled + 各源非空 hide 段 + feeds 订阅表（其余
    丢弃，记 info——这里的「重新生成」与手改文件的自由度是此消彼长，注释头
    已说明归属）。feeds 是 rss 的订阅（用户数据，不是工具生成的状态），必须
    原样保留——丢段等于丢订阅。
    """
    lines = [
        "# FeedScope 源配置：enabled = 启用的源；各源 hide = 隐藏的信息流",
        "# feeds = 订阅表（key = { label, url }）：rss 订 feed 地址、reddit 订 subreddit",
        "# 由 feedscope 自动维护（首启引导 / F2 管理 / rss 子命令），也可手动编辑",
    ]
    if ids is not None:
        lines += ["", f"enabled = {_toml_array(ids)}"]
    for sid, section in data.items():
        if not isinstance(section, dict):
            logger.info("覆写配置：忽略无法识别的顶层键 %r", sid)
            continue
        if set(section) - {"hide", "feeds"}:
            logger.info(
                "覆写配置：[%s] 里除 hide/feeds 外的键不被保留 %r",
                sid, sorted(set(section) - {"hide", "feeds"}),
            )
        hide = section.get("hide")
        feeds = _valid_feeds(section.get("feeds"))
        if (isinstance(hide, list) and hide) or feeds:
            lines += ["", f"[{_toml_key(sid)}]"]
            if isinstance(hide, list) and hide:
                lines.append(f"hide = {_toml_array(hide)}")
            if feeds:
                # 订阅行用内联表：一行一条，手编/比对都清爽；键引用字符串形式
                # （json 串即合法 TOML），key 带空格等字符也不会写出坏文件
                lines.append(f"[{_toml_key(sid)}.feeds]")
                lines += [
                    f"{_toml_key(key)} = {{ url = {_toml_str(url)}, label = {_toml_str(label)} }}"
                    for key, label, url in feeds
                ]
    return lines


def _write_config(lines: list[str]) -> None:
    """原子落盘（临时文件 + os.replace）：读者要么旧文件要么新文件，没有半截。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_enabled(ids: list[str]) -> None:
    """保存启用的源列表（首启引导 / F2 管理界面的落盘路径）。"""
    _write_config(_render_config(list(ids), _read()[0]))


def update_feeds(source_id: str, feeds: list[RssFeed], *, drop_enabled_when_empty: bool = False) -> None:
    """整体替换某源的订阅表（订阅管理屏 / rss 子命令的落盘路径）；enabled / hide 原样保留。

    enabled 联动让「加订阅」一步到位：订阅非空且 enabled 已配置但缺该源
    → 自动补上（加订阅显然是想用，省掉再开 TUI 勾一次）。删到空是否摘
    enabled 由 drop_enabled_when_empty 决定：rss 摘（feeds 全来自订阅，
    没订阅就没有任何流）；reddit 不摘（还有首页/热门内置流）。enabled
    未配置不动——缺省本就是全启用，写了反而丢失「未配置」语义。
    """
    data = _read()[0]
    section = data.get(source_id)
    if not isinstance(section, dict):
        section = {}
    if feeds:
        section["feeds"] = {f.key: {"label": f.label, "url": f.url} for f in feeds}
    else:
        section.pop("feeds", None)
    if section:
        data[source_id] = section
    elif source_id in data:
        del data[source_id]
    enabled = data.get("enabled")
    if isinstance(enabled, list):
        if feeds and source_id not in enabled:
            enabled.append(source_id)
        if not feeds and drop_enabled_when_empty and source_id in enabled:
            enabled.remove(source_id)
    _write_config(_render_config(enabled, data))


def update_rss_feeds(feeds: list[RssFeed]) -> None:
    """整体替换 rss 订阅表（update_feeds 的 rss 特例，保留既有调用名）。"""
    update_feeds("rss", feeds, drop_enabled_when_empty=True)


def describe() -> str:
    """doctor 用的一句话状态：未创建 / 解析失败原因 / 启用与隐藏概况。"""
    data, reason = _read()
    if reason:
        return reason
    parts: list[str] = []
    enabled = data.get("enabled")
    if isinstance(enabled, list):
        ids = "、".join(str(i) for i in enabled)
        parts.append(f"启用 {len(enabled)} 源" + (f"（{ids}）" if enabled else ""))
    n_hidden = n_sources = n_feeds = 0
    for section in data.values():
        if not isinstance(section, dict):
            continue
        n_sources += 1
        hide = section.get("hide") or []
        n_hidden += len(hide) if isinstance(hide, list) else 0
        feeds = section.get("feeds")
        n_feeds += len(feeds) if isinstance(feeds, dict) else 0
    if n_sources:
        parts.append(f"{n_sources} 源覆写：{n_hidden} 隐藏")
    if n_feeds:
        parts.append(f"{n_feeds} 订阅")
    return " · ".join(parts) if parts else "未创建（全部用内置默认）"
