"""国际化（i18n）：文案目录的发现、加载与查找。

- 每种语言一个 TOML 文件（本包内 <code>.toml，如 zh-CN.toml / en.toml）：
  文案按语言分文件维护，新增语言 = 放一个新文件（[meta] name 声明自称），
  语言选择屏自动发现，零代码改动。
- 键为点分嵌套域（main.empty_no_source），占位符 {name} 走 str.format；
  文案里要出现字面花括号得双写 {{。
- 回退链：当前语言 -> zh-CN（基准语言，文案以它对账做全）-> 键名（缺键
  记 warning，交付前由奇偶校验测试清零，见 tests/test_i18n.py）。
- 语言随进程固定：启动时按配置设定（cli 组装点 / TUI 首启），此后全程
  只读。Reddit 详情解析等工作线程并发调 t() 只读已加载目录，无线程安全
  问题——这也是「修改语言重启生效」的依据：解析期烙进数据的数字/单位
  文案（如「4.5 万」）与界面文案天然同语言。

本模块在 core 最底层（架构分层：sources/parse 只许 import core），供
core、sources、顶层业务、ui、cli 五层共用。
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from locale import getlocale

logger = logging.getLogger(__name__)

BASE_LANGUAGE = "zh-CN"  # 基准语言：回退链的底、奇偶校验的对账方（文案最全的对账基准）
DEFAULT_LANGUAGE = "en"  # 语言未设置时的缺省（默认英文；中文用户首启语言屏预选即 zh-CN）

_META = "meta"  # 语言文件里的元信息表（name 等），不是文案，不参与奇偶校验

_current = DEFAULT_LANGUAGE
_loaded = False
_names: dict[str, str] = {}  # code -> 自称（选择屏显示用）
_catalogs: dict[str, dict[str, str]] = {}  # code -> 扁平点分键 -> 文案


@dataclass(frozen=True)
class Language:
    """一种可选语言：code 即语言文件名（去 .toml），name 为其自称。"""

    code: str
    name: str


def _language_files() -> list[tuple[str, dict]]:
    """读包内全部语言 TOML：(code, 数据)，基准语言排最前、其余按文件名稳定排序。

    坏文件记警告跳过——新增语言文件写坏不该挡启动（与 core.config 同一
    容错粒度）。
    """
    out: list[tuple[str, dict]] = []
    for entry in files("feedscope.core.i18n").iterdir():
        if not entry.name.endswith(".toml"):
            continue
        try:
            with entry.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("语言文件 %s 无法解析，已跳过：%s", entry.name, exc)
            continue
        out.append((entry.name[: -len(".toml")], data))
    out.sort(key=lambda item: (item[0] != BASE_LANGUAGE, item[0]))
    return out


def _flatten(table: dict, prefix: str = "") -> dict[str, str]:
    """嵌套表 -> 扁平点分键 -> 文案；非字符串/非表的值不是文案，跳过。"""
    out: dict[str, str] = {}
    for key, value in table.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            out[dotted] = value
        elif isinstance(value, dict):
            out.update(_flatten(value, dotted))
    return out


def _ensure_loaded() -> None:
    """首次使用时加载全部语言文件（之后只读；语言文件极小，全量载入最简）。"""
    global _loaded
    if _loaded:
        return
    for code, data in _language_files():
        meta = data.get(_META)
        name = meta.get("name") if isinstance(meta, dict) else None
        if not isinstance(name, str) or not name:
            logger.warning("语言文件 %s 缺 [meta] name，已跳过", code)
            continue
        _names[code] = name
        _catalogs[code] = _flatten({k: v for k, v in data.items() if k != _META})
    if BASE_LANGUAGE not in _catalogs:
        logger.warning("基准语言 %s 缺失：回退链只剩键名", BASE_LANGUAGE)
    _loaded = True


def available() -> list[Language]:
    """全部可选语言（基准语言在前，其余按 code 排序）：语言选择屏的数据源。"""
    _ensure_loaded()
    return [Language(code, name) for code, name in _names.items()]


def name_of(code: str) -> str:
    """语言代码 -> 自称（如 zh-CN -> 简体中文）；未知代码回退原码。"""
    _ensure_loaded()
    return _names.get(code, code)


def set_language(code: str) -> None:
    """设定当前语言（进程内全局）。未知代码回退缺省并记警告（配置手写坏值
    不挡启动）。"""
    global _current
    _ensure_loaded()
    if code in _names:
        _current = code
    else:
        logger.warning("未知语言代码 %r，回退 %s", code, DEFAULT_LANGUAGE)
        _current = DEFAULT_LANGUAGE


def language() -> str:
    """当前语言代码（i18n 层不读配置：设定时机在组装点 / 首启流程）。"""
    return _current


def t(key: str, /, **kwargs: object) -> str:
    """查文案并填占位符：当前语言 -> 基准语言 -> 键名（缺键记 warning）。

    无参调用也走 format：{{ }} 转义与占位符同一条规则，行为不随调用方
    传不传参而变。不含点号的字符串按最终文案直通（不查不警）——文案键
    恒为点分域，直通语义给测试替身的裸文案与用户数据（订阅名等）留路：
    源词汇表的值可能是键（真源）也可能是裸文案（stub），统一走 t() 不分叉。
    """
    _ensure_loaded()
    if "." not in key:
        return key.format(**kwargs)
    text = _catalogs.get(_current, {}).get(key)
    if text is None and _current != BASE_LANGUAGE:
        text = _catalogs.get(BASE_LANGUAGE, {}).get(key)
    if text is None:
        logger.warning("文案键缺失：%s", key)
        return key
    return text.format(**kwargs)


def catalog_keys(code: str) -> set[str]:
    """某语言的全量文案键（奇偶校验 / 引用存在性测试用）。"""
    _ensure_loaded()
    return set(_catalogs.get(code, {}))


def raw(key: str) -> str:
    """某键的未填充模板（占位符原样保留）。

    极少数「写出去再读回来」的场景用（知乎 meta 里的回答总数回读正则由
    模板生成，与写入同键同语言）；日常展示一律走 t()。
    """
    _ensure_loaded()
    text = _catalogs.get(_current, {}).get(key)
    if text is None:
        text = _catalogs.get(BASE_LANGUAGE, {}).get(key)
    if text is None:
        logger.warning("文案键缺失：%s", key)
        return key
    return text


def has(key: str) -> bool:
    """键是否在当前或基准目录里。

    BINDINGS 的键式描述解析用（ui/labels.py）：description 不是键的（源
    单位词 relabel 后的最终文案）原样直通，不能当键硬翻。
    """
    _ensure_loaded()
    return key in _catalogs.get(_current, {}) or key in _catalogs.get(BASE_LANGUAGE, {})


def messages(code: str) -> dict[str, str]:
    """某语言的全量文案（键 -> 文本；测试与工具用，浅拷贝防误改目录）。"""
    _ensure_loaded()
    return dict(_catalogs.get(code, {}))


def default_language() -> str:
    """按系统 locale 猜语言（语言屏的预选项）：zh* -> zh-CN，其余 en；
    取不到 locale 用缺省（英文）。"""
    try:
        loc = getlocale()[0] or ""
    except ValueError:  # 个别环境 LANG 是坏值（getlocale 会抛）
        loc = ""
    if not loc:
        return DEFAULT_LANGUAGE
    return BASE_LANGUAGE if loc.startswith("zh") else "en"


def placeholders(text: str) -> set[str]:
    """文案里的占位符名集合（{{ }} 转义不算）：奇偶校验用，非运行时路径。"""
    stripped = text.replace("{{", "").replace("}}", "")
    return set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", stripped))
