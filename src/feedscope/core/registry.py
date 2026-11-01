"""源注册器：@register + sources 包导入 = 插件机制。"""

from __future__ import annotations

from feedscope.core.source import Source

_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """类装饰器：把 Source 子类登记进全局注册表。"""
    _REGISTRY[cls.source_id] = cls
    return cls


def all_sources() -> list[Source]:
    """实例化全部已注册源（顺序即注册顺序）。"""
    return [cls() for cls in _REGISTRY.values()]


def get_source(source_id: str) -> Source:
    return _REGISTRY[source_id]()
