"""主题加载器：ui/themes/ 下一个 TOML 一个主题，自动发现（镜像 core/i18n）。

- 新增主题 = 复制一份 TOML 改色，零代码改动；键校验测试（tests/test_theme.py）
  钉住完整性——缺色槽/缺变量名 CI 红，坏文件运行时跳过 + 警告（i18n 同款容错）
- 主题与语言同为「一文件一资源」，但生效模型不同：语言烙进抓取数据需重启
  （Ctrl+L 落盘后重启生效），主题纯显示层——App.theme = name 即时生效
  （Ctrl+T 当场换肤）
- Textual 8 的 Theme 是数据类：TOML 的 [theme] 表直接喂字段，
  [theme.variables] 进 CSS 变量（$link-color 等三处消费点见各屏 CSS）
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from textual.theme import Theme

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent / "themes"
DEFAULT = "editor-dark"

# 必填色槽（键校验的钉子）：漏一个 = 整文件跳过，不部分上色
_SLOTS = (
    "name",
    "dark",
    "primary",
    "secondary",
    "accent",
    "warning",
    "error",
    "success",
    "foreground",
    "background",
    "surface",
    "panel",
    "boost",
)
# CSS 变量消费点（main/reading 的 CSS 里 $xxx 引用），缺了对应样式静默回退
_VARIABLES = ("link-color", "footer-key-foreground", "nav-sel-background")

_cache: list[Theme] | None = None


def available() -> list[Theme]:
    """全部可用主题（按 name 排序；坏文件跳过 + 警告，绝不挡启动）。"""
    global _cache
    if _cache is None:
        themes: list[Theme] = []
        for path in sorted(_DIR.glob("*.toml")):
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))["theme"]
                variables = data["variables"]
                missing = [k for k in (*_SLOTS, *_VARIABLES) if k not in ({**data, **variables})]
                if missing:
                    logger.warning("主题 %s 缺字段 %s，已跳过", path.name, missing)
                    continue
                themes.append(_build(data))
            except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
                logger.warning("主题 %s 无法解析，已跳过：%s", path.name, exc)
        if not themes:  # 内置主题全坏 = 安装损坏，退 Textual 缺省而不是黑屏
            logger.warning("ui/themes/ 下没有可用主题，回退 Textual 内置主题")
            return []
        _cache = themes
    return list(_cache)


def _build(data: dict) -> Theme:
    return Theme(
        name=str(data["name"]),
        dark=bool(data["dark"]),
        primary=str(data["primary"]),
        secondary=str(data["secondary"]),
        accent=str(data["accent"]),
        warning=str(data["warning"]),
        error=str(data["error"]),
        success=str(data["success"]),
        foreground=str(data["foreground"]),
        background=str(data["background"]),
        surface=str(data["surface"]),
        panel=str(data["panel"]),
        boost=str(data["boost"]),
        variables={k: str(v) for k, v in data["variables"].items()},
    )


def resolve(name: str | None) -> str:
    """配置值 -> 可用主题名；缺失/非法回退 DEFAULT（配置坏不该黑屏）。"""
    if name and any(t.name == name for t in available()):
        return name
    return DEFAULT


def register_all(app) -> None:  # noqa: ANN001 - Textual App（避免运行时导入成环）
    for theme in available():
        app.register_theme(theme)
