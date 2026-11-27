"""订阅管理（源无关）：增删改名的通用编排 + 订阅管理屏的源适配器 SubsStore。

源差异（输入怎么归一、怎么查重、怎么探测取名）全部是 Source 上的三个钩子
（core/source.py「订阅管理」节）；本模块只做通用四步：

    输入归一 → 查重与保留字 → 探测取名 → 落盘（config.update_feeds，enabled 联动在那里）

CLI 的 rss 子命令与 TUI 订阅管理屏共用这里（屏只认 SubsStore，不认识具体源）。
OPML 进出是独立的 services/opml。新增可订阅源不需要改本文件——store_for 按
源声明合成适配器。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import feedscope.sources  # noqa: F401  import 即注册：按 source_id 取源
from feedscope.core import config
from feedscope.core.config import Subscription
from feedscope.core.errors import SubscriptionError
from feedscope.core.i18n import t
from feedscope.core.registry import get_class
from feedscope.core.source import Source
from feedscope.services.opml import import_opml, read_opml_file


def _subscribable(source_id: str) -> Source:
    """按 id 取可订阅源的实例；不是可订阅源（或没注册）尽早炸。"""
    cls = get_class(source_id)
    if cls is None or not cls.subscribable:
        raise ValueError(t("subs.no_store", source=source_id))
    return cls()


async def add_subscription(
    source_id: str, text: str, *, key: str | None = None, label: str | None = None
) -> Subscription:
    """加一条订阅并落盘，返回落盘的订阅。

    调用方显式给了显示名（label）就不探测取名——省一次请求，RSS 的首页自动
    发现也随之不触发（按给的地址原样订）。重复 / 撞保留字 / 订阅物不可用 →
    SubscriptionError（不动配置）。
    """
    source = _subscribable(source_id)
    existing = config.feeds_for(source_id)
    draft = source.subscription_input(text, key=key, existing=existing)
    source.check_subscription(draft, existing)
    resolved = draft if label else await source.probe_subscription(draft)
    feed = Subscription(
        key=resolved.key, label=label or resolved.label or resolved.key, url=resolved.url
    )
    # 探测期间配置可能已变：落盘前重读，不用探测前的旧表覆盖
    config.update_feeds(
        source_id,
        [*config.feeds_for(source_id), feed],
        drop_enabled_when_empty=source.drop_enabled_when_empty,
    )
    return feed


async def add_from_input(source_id: str, text: str) -> tuple[list[Subscription], str]:
    """添加入口的智能分流：http(s) 地址 → 单条添加；本地路径 → OPML 导入
    （只对声明了 sub_imports_opml 的源，其余源把任何文本交给输入归一）。

    返回 (本次新增, 结果描述)；`~` 开头的路径自动展开。CLI 的 add 与 TUI
    订阅管理屏的添加框共用这里，行为永远一致。
    """
    source = _subscribable(source_id)
    text = text.strip()
    if not text:
        raise SubscriptionError(t("subs.empty_input"))
    if not text.startswith(("http://", "https://")) and source.sub_imports_opml:
        added, skipped = import_opml(read_opml_file(text), source_id)
        detail = t("subs.imported_skip", n=len(skipped)) if skipped else ""
        return added, t("subs.imported", n=len(added), detail=detail)
    feed = await add_subscription(source_id, text)
    return [feed], t(source.sub_added, key=feed.key, label=feed.label)


def remove_subscription(source_id: str, key: str) -> Subscription | None:
    """删一条订阅，返回被删的；key 不存在返回 None（不动配置）。"""
    feeds = config.feeds_for(source_id)
    kept = [f for f in feeds if f.key != key]
    if len(kept) == len(feeds):
        return None
    config.update_feeds(
        source_id, kept, drop_enabled_when_empty=_subscribable(source_id).drop_enabled_when_empty
    )
    return next(f for f in feeds if f.key == key)


def rename_subscription(source_id: str, key: str, new_label: str) -> Subscription | None:
    """改显示名；名字清空 / key 不存在返回 None（不动配置）。"""
    new_label = new_label.strip()
    if not new_label:
        return None
    feeds = config.feeds_for(source_id)
    if key not in {f.key for f in feeds}:
        return None
    renamed = Subscription(key=key, label=new_label, url=next(f.url for f in feeds if f.key == key))
    config.update_feeds(source_id, [renamed if f.key == key else f for f in feeds])
    return renamed


# ---- 订阅管理屏的源适配器 ----


@dataclass(frozen=True)
class SubsStore:
    """一个 subscribable 源的订阅管理适配器：四操作 + 文案，订阅管理屏只认它。

    屏保持通用（列表 + 增删改名），源差异收在源声明（文案键）与本模块的通用
    编排里。add 与 add_from_input 同形——(新增, 概要)。
    """

    label: str  # 屏标题「{label} 订阅管理」
    add_placeholder: str  # 添加输入框占位（各源可输入的形态不同）
    hint: str  # 底部键位提示行
    empty: str  # 零订阅占位文案
    feeds: Callable[[], list[Subscription]]  # 当前订阅表（列表顺序 = 配置顺序）
    add: Callable[[str], Awaitable[tuple[list[Subscription], str]]]
    remove: Callable[[str], Subscription | None]
    rename: Callable[[str, str], Subscription | None]


def store_for(source_id: str) -> SubsStore:
    """订阅管理屏的入口工厂：适配器由源声明合成——subscribable=True 即有 store，
    要不到 store 的是声明漏了（尽早炸）。"""
    cls = get_class(source_id)
    if cls is None or not cls.subscribable:
        raise ValueError(t("subs.no_store", source=source_id))
    return SubsStore(
        label=t(cls.label),
        add_placeholder=t(cls.sub_placeholder),
        hint=t(cls.sub_hint),
        empty=t(cls.sub_empty),
        feeds=lambda: config.feeds_for(source_id),
        add=lambda text: add_from_input(source_id, text),
        remove=lambda key: remove_subscription(source_id, key),
        rename=lambda key, new_label: rename_subscription(source_id, key, new_label),
    )
