"""源注册器：@register + sources 包导入 = 插件机制。"""

from __future__ import annotations

from feedscope.core.source import Source

_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """类装饰器：把 Source 子类登记进全局注册表。"""
    _REGISTRY[cls.source_id] = cls
    return cls


def source_classes() -> list[type[Source]]:
    """全部已注册的源类（顺序即注册顺序）。

    供只需要类的路径（启动时「有没有注册任何源」检查）——要看运行时
    实例（读配置过滤后的源）走 all_sources。
    """
    return list(_REGISTRY.values())


def all_sources() -> list[Source]:
    """实例化全部已注册源（顺序即注册顺序）。"""
    return [cls() for cls in _REGISTRY.values()]


def get_class(source_id: str) -> type[Source] | None:
    """按 id 取源类（不实例化）；未注册返回 None。

    供按声明分派的调用方（UI 的源重建、订阅入口的保留字检查）——它们只要
    ClassVar，实例化反而会读配置做合成。取不到直接炸的路径用 get_source。
    """
    return _REGISTRY.get(source_id)


def get_source(source_id: str) -> Source:
    """按 id 实例化源；未注册直接 KeyError（登录/巡检等内部路径 fail-fast）。"""
    return _REGISTRY[source_id]()


def install_sources(classes: list[type[Source]]) -> None:
    """整体替换注册表为给定源类（离线脚本 / 截图驱动 stub 源用）。

    测试与 scripts/screenshot.py 之前直接改 _REGISTRY 私有字典——那是对
    实现细节的耦合（换数据结构即断）；公开入口把「替换 + 恢复」的语义
    收进本模块。
    """
    _REGISTRY.clear()
    for cls in classes:
        register(cls)
