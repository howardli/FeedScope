"""用户配置：sources.toml 选启用的源 + 隐藏不想看的内置信息流 + 各源订阅表。

- 路径：FEEDSCOPE_CONFIG 环境变量（测试、多套配置切换），缺省用户配置目录下
  sources.toml（platformdirs，Linux 为 ~/.config/feedscope/sources.toml）
- 容错哲学：配置问题绝不挡启动——文件不存在 = 全默认；TOML 语法错 / 结构
  不符 = 警告日志 + 当作不存在（feedscope doctor 可诊断）。
- 写入走 save_enabled（首启引导 / F2 管理界面）、update_feeds（订阅管理
  屏 / rss 子命令）与 save_language / save_theme（选择屏落盘）：**只改受管
  键、其余原样落盘**（tomlkit 往返）——手编的注释/未知键/排版不再被覆写
  洗掉，订阅是用户数据（覆写丢段 = 丢订阅）更是如此；原子落盘（临时文件
  + os.replace），不留半截文件。
- 本模块对「源」只认 id 字符串，不认识任何具体源：订阅 key 的保留字
  （聚合流名 / 内置流名）由各源自己声明（Source.reserved_feed_keys），
  读取侧的过滤在 SubscribedSource、添加侧的拒收在 subscriptions。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tomlkit
from platformdirs import user_config_path

from feedscope.core import i18n, timefmt
from feedscope.core.i18n import t
from feedscope.core.jsonio import write_atomic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Subscription:
    """一条订阅（rss 的 feed / reddit 的 subreddit / telegram 的频道同形）：
    key 即信息流名（侧栏与 open_feed 的寻址键）。"""

    key: str
    label: str  # 侧栏显示名；配置缺省 = key 本身
    url: str


def derive_key(url: str, existing: Iterable[str]) -> str:
    """URL -> 订阅 key：域名清洗成 ASCII 段（www 剥掉、非法字符折叠成 -），
    与 existing 撞名则加 -2、-3…；域名清洗后为空（如纯 IP 端口）回退 feed。

    existing 是「要避开的 key 全集」——调用方把保留字（聚合流名等，见
    Source.reserved_feed_keys）并进已有订阅一起传：派生出的 key 撞保留字会
    同样加后缀绕开，显式 key 撞保留字的报错路径只在用户真的手输时才触发。
    """
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    host = re.sub(r"^www\.", "", host)
    base = re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "feed"
    keys = set(existing)
    if base not in keys:
        return base
    n = 2
    while f"{base}-{n}" in keys:
        n += 1
    return f"{base}-{n}"


def config_path() -> Path:
    if p := os.environ.get("FEEDSCOPE_CONFIG"):
        return Path(p)
    return user_config_path("feedscope") / "sources.toml"


def _read() -> tuple[dict[str, Any], str]:
    """读配置返回 (数据, 状态描述)；不存在 / 坏 = (空文档, 原因)，不抛异常。

    解析用 tomlkit 而非 tomllib：保存路径要「改受管键、原样落盘」，注释与
    排版只有在解析层就留着才写回得回去（tomllib 到手就是纯 dict，注释已
    丢失）。TOMLDocument 是 dict 子类，读侧的 isinstance/迭代/取值照常。
    非 UTF-8 字节（手编/别的工具写坏）也是「坏配置」：容错哲学要求绝不
    挡启动，不能让它以 UnicodeDecodeError 逃出 _read。
    """
    path = config_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            return tomlkit.parse(f.read()), ""
    except FileNotFoundError:
        return tomlkit.document(), t("config.describe_not_created")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("用户配置 %s 无法解析，已忽略：%s", path, exc)
        return tomlkit.document(), t("config.describe_unparsable", error=exc)


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


def feeds_for(source_id: str) -> list[Subscription]:
    """读某源的订阅表（书写顺序即侧栏顺序），结构与坏条目逐条容错。

    期望结构（label 缺省用 key；url 必填）：

        [rss.feeds]
        ruanyq = { label = "阮一峰周刊", url = "https://….com/atom.xml" }
        hn = { url = "https://hnrss.org/frontpage" }

        [reddit.feeds]            # url 存 subreddit 站内地址，自解释
        rust = { url = "https://www.reddit.com/r/rust/" }

        [telegram.feeds]
        durov = { url = "https://t.me/durov/" }

    条目不是表 / 缺 url = 警告并跳过该条，不波及其他订阅（同一容错粒度：
    文件级坏 = 全默认已在上层 _read，这里是条目级）。保留字（聚合流名 /
    内置流名）撞名的 key 原样返回——过滤在各源的 SubscribedSource（读取
    侧会跳过并警告），拒收在 subscriptions 的添加入口，本模块不认识源。
    """
    section = _read()[0].get(source_id)
    feeds_raw = section.get("feeds") if isinstance(section, dict) else None
    if feeds_raw is None:
        return []
    if not isinstance(feeds_raw, dict):
        logger.warning("配置 [%s] feeds 应为订阅表，已忽略", source_id)
        return []
    feeds: list[Subscription] = []
    for key, entry in feeds_raw.items():
        feed = _subscription_from_entry(key, entry)
        if feed is not None:
            feeds.append(feed)
    return feeds


def _subscription_from_entry(
    key: str, entry: object, *, context: str = ""
) -> Subscription | None:
    """订阅表条目 -> Subscription；坏条目记警告并跳过。

    读路径（feeds_for）与写路径（_valid_feeds）共用同一条校验规则——
    「读得进来、写得回去」不再靠两处各自维护。
    """
    prefix = f"{context}：" if context else ""
    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not entry["url"]:
        logger.warning("%s订阅 %r 应为含非空 url 的表，已跳过", prefix, key)
        return None
    label = entry.get("label")
    if label is not None and not isinstance(label, str):
        logger.warning("%s订阅 %r 的 label 应为字符串，已回退用 key", prefix, key)
        label = None
    return Subscription(key=key, label=label or key, url=entry["url"])



def enabled_ids() -> list[str] | None:
    """读顶层启用的源 id 列表；None = 未配置（默认全部启用，老文件只有 hide 的用户不受影响）。

    期望结构：

        enabled = ["zhihu", "x"]

    列表里的未知 id 不在此报错——注册表过滤在调用方（新装/卸载源后残留条目无害）。
    手编重复 id 按首次出现去重：重复项会让主屏建出重复 tab（DuplicateIds），
    顺序语义也只剩第一份有意义。
    """
    enabled = _read()[0].get("enabled")
    if enabled is None:
        return None
    if not isinstance(enabled, list) or not all(isinstance(i, str) for i in enabled):
        logger.warning("配置 enabled 应为源 id 列表，已忽略（默认全部启用）")
        return None
    return list(dict.fromkeys(enabled))


def is_configured() -> bool:
    """是否已完成首次设置（配置文件存在）。

    只看文件存在与否：TOML 坏文件是 doctor 的职责，不重新引导——
    引导保存会覆写，坏文件里可能还有用户手写的其他内容。
    """
    return config_path().exists()


def get_language() -> str | None:
    """读顶层语言设置（i18n 语言代码，如 "zh-CN" / "en"）；None = 未设置。

    未设置不是错误：读侧缺省在 core.i18n（回退基准语言），未设置的用户
    会在下次启动被语言选择屏询问（首启流程见 ui/screens/language.py）。
    """
    lang = _read()[0].get("language")
    if lang is None:
        return None
    if not isinstance(lang, str) or not lang:
        logger.warning("配置 language 应为语言代码字符串，已忽略")
        return None
    return lang


def save_language(code: str) -> None:
    """保存语言设置（语言选择屏的落盘路径）；其余内容（注释/未知键）原样保留。

    坏配置拒绝落盘（_read_writable）：设置只留在本次进程，doctor 修复后重存。
    """
    doc = _read_writable()
    if doc is None:
        return
    doc["language"] = code
    _write_config(doc)


def get_theme() -> str | None:
    """配置的主题名（ui/themes.py 的注册键）；缺省 None = 加载器回退默认深色。"""
    theme = _read()[0].get("theme")
    return theme if isinstance(theme, str) and theme else None


def save_theme(name: str) -> None:
    """保存主题设置（Ctrl+T 即时生效 + 这里落盘）；其余内容原样保留。

    坏配置拒绝落盘（_read_writable）：主题只在本会话生效，doctor 修复后重选。
    """
    doc = _read_writable()
    if doc is None:
        return
    doc["theme"] = name
    _write_config(doc)


def get_timezone() -> str | None:
    """配置的展示时区（IANA 名，如 "Asia/Shanghai"）；None = 未设置（默认北京时间）。

    合法性校验在消费侧 timefmt.set_timezone（ZoneInfo 解析）：这里只把
    字符串读出来，坏值由 set_timezone 记警告并回退，doctor 可见。
    """
    tz = _read()[0].get("timezone")
    return tz if isinstance(tz, str) and tz else None


def get_browser() -> str | None:
    """配置的「打开链接」命令（shell 词法切分，%u 换成链接）；None = 未配置。

    缺省走 webbrowser.open()——SSH / 无桌面环境里它拉不起浏览器。配置示例：
    browser = "open -a Firefox"      # macOS
    browser = "firefox %u"           # Linux
    """
    cmd = _read()[0].get("browser")
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def get_clipboard() -> str | None:
    """配置的「复制」命令（从 stdin 读文本）；None = 未配置（自动探测 / OSC 52）。

    缺省探测 pbcopy / wl-copy / xclip / xsel，都没有才退回终端的 OSC 52。
    配置示例：clipboard = "tmux load-buffer -"
    """
    cmd = _read()[0].get("clipboard")
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def _enabled_from_data(data: dict[str, Any]) -> list[str] | None:
    """从配置数据里取出规范化的 enabled（坏值按未配置；空列表是合法的零启用）。"""
    raw = data.get("enabled")
    if not isinstance(raw, list):
        return None
    kept = list(dict.fromkeys(x for x in raw if isinstance(x, str)))
    return kept if kept or not raw else None


def _dump(doc: dict[str, Any], *, fresh: bool) -> str:
    """文档 -> 落盘文本；落盘前文件还不存在（首启保存）才补标准头注释——
    键含义的说明书只在这一种情况注入，既有文件的头/注释是用户手笔或上次
    写入的原样，覆写头等于洗掉用户注释。"""
    text = tomlkit.dumps(doc)
    if not fresh:
        return text
    header = "\n".join(f"# {line}" for line in t("config.file_header").splitlines())
    return f"{header}\n\n{text}" if text.strip() else header + "\n"


def _read_writable() -> dict[str, Any] | None:
    """写路径专用读档：既有文件解析失败时拒绝落盘（返回 None，调用方放弃保存）。

    坏但可人工抢救的 sources.toml 里可能还有用户手写的订阅与注释——拿空
    文档继续 _write_config 会把它洗成只剩一个受管键，与 is_configured
    「坏文件是 doctor 的职责，不重新引导」的哲学冲突。文件不存在（首启
    保存建新文件）不受影响。
    """
    doc, reason = _read()
    if reason and config_path().exists():
        logger.warning(
            "配置文件存在但无法解析，拒绝写入以免覆写（%s）——本次保存未落盘，feedscope doctor 可见",
            reason,
        )
        return None
    return doc


def _write_config(doc: dict[str, Any]) -> None:
    """原子落盘（原语见 core/jsonio）：读者要么旧文件要么新文件，没有半截。"""
    path = config_path()
    fresh = not path.exists()  # 判定要在落盘前：保存动作本身就会建出文件
    write_atomic(path, _dump(doc, fresh=fresh))


def save_enabled(ids: list[str]) -> None:
    """保存启用的源列表（首启引导 / F2 管理界面的落盘路径）。

    坏配置拒绝落盘（_read_writable）：勾选只留在本次会话，doctor 修复后重存。
    """
    doc = _read_writable()
    if doc is None:
        return
    doc["enabled"] = list(dict.fromkeys(ids))
    _write_config(doc)


def _sync_enabled_link(
    doc: dict[str, Any], source_id: str, feeds: list[Subscription], *, drop_when_empty: bool
) -> None:
    """enabled 联动（语义见 update_feeds docstring）：订阅非空自动补、删到空按需摘。

    坏 enabled（字符串/表/全是非 str）按「未配置」处理：既不参与联动，也不把
    畸形值原样写回去——读侧本来就是当默认全启用；空列表是合法状态（零启用），
    保留。
    """
    enabled = _enabled_from_data(doc)
    if enabled is None:
        return  # 未配置不动——缺省本就是全启用，写了反而丢失「未配置」语义
    if feeds and source_id not in enabled:
        enabled.append(source_id)
    if not feeds and drop_when_empty and source_id in enabled:
        enabled.remove(source_id)
    doc["enabled"] = enabled


def update_feeds(
    source_id: str, feeds: list[Subscription], *, drop_enabled_when_empty: bool = False
) -> None:
    """整体替换某源的订阅表（订阅管理屏 / rss 子命令的落盘路径）；其余原样保留。

    enabled 联动让「加订阅」一步到位：订阅非空且 enabled 已配置但缺该源
    → 自动补上（加订阅显然是想用，省掉再开 TUI 勾一次）。删到空是否摘
    enabled 由 drop_enabled_when_empty 决定：rss / telegram 摘（feeds 全
    来自订阅，没订阅就没有任何流）；reddit 不摘（还有首页/热门内置流）。
    坏配置拒绝落盘（_read_writable）：订阅变更只在内存，doctor 修复后重做。
    """
    doc = _read_writable()
    if doc is None:
        return
    section = doc.get(source_id)
    if section is not None and not isinstance(section, dict):
        # 顶层已有同名标量（手编冲突）：不能既保它又挂 [source_id] 段——保用户
        # 数据优先，本次保存整体放弃（早退而不是半套用，否则会落下「enabled
        # 里补了源、订阅表却没写」的中间态），日志可见、doctor 可修
        logger.warning(
            "配置顶层已存在非表键 %r，%d 条订阅未写入（请手动清理该键）",
            source_id, len(feeds),
        )
        return
    if section is None:
        section = tomlkit.table()
        doc[source_id] = section
    if feeds:
        section["feeds"] = _merge_feeds_table(section.get("feeds"), feeds)
    else:
        section.pop("feeds", None)
        if not section:  # 没剩任何键：空段不保留（有手编键则连段一起留着）
            doc.pop(source_id, None)
    _sync_enabled_link(doc, source_id, feeds, drop_when_empty=drop_enabled_when_empty)
    _write_config(doc)


def _merge_feeds_table(old: object, feeds: list[Subscription]) -> dict[str, Any]:
    """订阅表的原位合并：既有的行对象按 key 复用，只改 url / label。

    0.4.0 的 tomlkit 往返保住了文件其余部分的注释与未知键，但订阅表本身
    仍是整表重建——表内行上/行尾的注释、条目里的自定义键，经订阅管理屏或
    rss rename 保存一次就没了（README 承诺的「原样保留」在表内并不成立）。
    原位合并后：改名/改地址只动那两个键，删掉的整行摘除，新增的追加在表尾
    （内联表一行一条，label/url 的引号转义交给 tomlkit）。old 不是表（首次
    写入 / 手编坏了，doctor 会报）才新建。
    """
    table: dict[str, Any] = old if isinstance(old, dict) else tomlkit.table()
    wanted = {f.key for f in feeds}
    for key in [k for k in table if k not in wanted]:
        del table[key]
    for f in feeds:
        row = table.get(f.key)
        if not isinstance(row, dict):  # 新订阅，或旧条目不是表
            row = table[f.key] = tomlkit.inline_table()
        row["url"] = f.url
        row["label"] = f.label
    return table



def describe() -> str:
    """doctor 用的一句话状态：未创建 / 解析失败原因 / 语言、启用与隐藏概况。"""
    data, reason = _read()
    if reason:
        return reason
    parts: list[str] = []
    lang = data.get("language")
    if isinstance(lang, str) and lang:
        parts.append(t("config.describe_language", lang=lang))
    enabled = data.get("enabled")
    if isinstance(enabled, list):
        joiner = t("common.enum_comma")
        ids = joiner.join(str(i) for i in enabled)
        parts.append(
            t("config.describe_enabled_with_ids", n=len(enabled), ids=ids)
            if enabled
            else t("config.describe_enabled", n=len(enabled))
        )
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
        parts.append(t("config.describe_overrides", n=n_sources, hidden=n_hidden))
    if n_feeds:
        parts.append(t("config.describe_feeds", n=n_feeds))
    return " · ".join(parts) if parts else t("config.describe_not_created")


def parse_error() -> str | None:
    """配置文件存在却解析不了的原因；不存在 / 正常 = None。

    doctor 据此判 FAIL：保存路径遇到坏文件会拒绝写入并指向 doctor，doctor
    自己不能对坏文件报 OK。
    """
    _, reason = _read()
    return reason if reason and config_path().exists() else None


def lint() -> list[str]:
    """配置体检（doctor 用）：逐条报「不挡启动、但等于没生效」的问题。

    读取侧对这些全是静默容错（坏值回退默认、坏条目跳过、只记日志）——用户
    不翻日志就不知道自己的时区/订阅其实没生效。返回的每条都是面向用户的
    文案。只做文件结构这一层的检查；要认识源词表的部分（未知源 id、hide 名
    对不上流）在 doctor 里——本模块不认识任何具体的源。
    """
    data = _read()[0]
    problems: list[str] = []
    enabled = data.get("enabled")
    if enabled is not None and (
        not isinstance(enabled, list) or not all(isinstance(i, str) for i in enabled)
    ):
        problems.append(t("config.lint_enabled"))
    lang = data.get("language")
    if lang is not None and (not isinstance(lang, str) or lang not in _language_codes()):
        problems.append(t("config.lint_language", lang=str(lang)))
    tz = data.get("timezone")
    if tz is not None and (not isinstance(tz, str) or not timefmt.valid_timezone(tz)):
        problems.append(t("config.lint_timezone", tz=str(tz)))
    for key in ("browser", "clipboard"):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(t("config.lint_command", key=key))
    for source, section in data.items():
        if not isinstance(section, dict):
            continue
        hide = section.get("hide")
        if hide is not None and (
            not isinstance(hide, list) or not all(isinstance(n, str) for n in hide)
        ):
            problems.append(t("config.lint_hide", source=source))
        feeds = section.get("feeds")
        if feeds is None:
            continue
        if not isinstance(feeds, dict):
            problems.append(t("config.lint_feeds_table", source=source))
            continue
        for key, entry in feeds.items():
            url = entry.get("url") if isinstance(entry, dict) else None
            if not isinstance(url, str) or not url:
                problems.append(t("config.lint_feed_url", source=source, key=str(key)))
            elif (label := entry.get("label")) is not None and not isinstance(label, str):
                problems.append(t("config.lint_feed_label", source=source, key=str(key)))
    return problems


def _language_codes() -> set[str]:
    """随包发行的全部语言代码（set_language 对未知代码静默回退缺省）。"""
    return {language.code for language in i18n.available()}
