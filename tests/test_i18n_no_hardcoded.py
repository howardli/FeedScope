"""「文案做全、不漏」的第二道防线：src 里不允许再出现硬编码中文文案（AST 扫描）。

- 扫 src/feedscope 全部 .py 的字符串字面量（docstring 除外），含 CJK 的必须
  落在显式豁免清单里；语言文件是 TOML（core/i18n/*.toml）不在扫描范围
- 豁免三类，各有据可查：
  1. logger.* 的参数——日志是开发者诊断，按约定保持中文（不随语言切）
  2. 内嵌脚本/CSS 常量——JS（页签 evaluate 脚本）与 Textual CSS 里的中文
     是给代码/样式的注释，不是用户文案
  3. core/text.py 的 CJK 宽度测量边界字符（「一/鿿」是数据不是词）
- 同时收集 t()/raw()/has() 的静态字面量键，断言都在基准目录里（动态拼键
  由 test_i18n 的 registry 词汇测试兜住）

第一道防线（语言文件键奇偶）在 test_i18n.py；本测试保证新增文案只能走
core/i18n 的目录文件，写完代码不写目录 = 红。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from feedscope.core import i18n

SRC = Path(__file__).parent.parent / "src" / "feedscope"
CJK = re.compile(r"[\u4e00-\u9fff]")

# 豁免清单：(相对路径, 值前缀)。新增豁免必须给理由——这是防线不是绕道。
_ALLOWED: tuple[tuple[str, str], ...] = (
    # CJK 显示宽度的测量边界字符（display_width 的数据，不是文案）
    ("core/text.py", "一"),
    ("core/text.py", "鿿"),
    # 日志上下文前缀（_subscription_from_entry 的 context 参数，只进 logger 输出）
    ("core/config.py", "覆写配置"),
    # 页签统一 fetch 脚本（JS 里的中文注释给维护者）
    ("core/browser.py", "\nasync ([url"),
    # 知乎开题并行拉取脚本（同上）
    ("sources/zhihu/fetch.py", "\nasync (roots"),
    # Textual CSS 常量（样式注释，不上面屏）
    ("ui/dialogs.py", "\n    .dialog-fixed"),
    ("ui/screens/main.py", "\n    ListItem"),
    ("ui/screens/reading.py", "\n    /* 楼层"),
)


def _docstring_values(tree: ast.Module) -> set[int]:
    """模块/类/函数 docstring 的行号集合（第一语句的字符串常量）。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                lines.add(body[0].value.lineno)
    return lines


def _logger_string_lines(tree: ast.Module) -> set[int]:
    """logger.<level>(...) 调用里字符串参数的行号（日志保持中文，见模块头）。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    lines.add(arg.lineno)
    return lines


def test_no_hardcoded_cjk_copy_in_src() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip_lines = _docstring_values(tree) | _logger_string_lines(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and CJK.search(node.value)
                and node.lineno not in skip_lines
            ):
                if any(rel == f and node.value.startswith(p) for f, p in _ALLOWED):
                    continue
                offenders.append(f"{rel}:{node.lineno} {node.value[:50]!r}")
    assert not offenders, (
        "src 里有未进目录的中文文案（写文案必须配 core/i18n/*.toml 的键）：\n"
        + "\n".join(offenders)
    )


def test_static_i18n_keys_exist() -> None:
    """t()/raw()/has() 的静态字面量键都在基准目录：拼错的键当场红。"""
    base = i18n.catalog_keys(i18n.BASE_LANGUAGE)
    missing: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"t", "raw", "has"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "." in node.args[0].value  # 无点 = 直通文案（stub/用户数据），不是键
                and node.args[0].value not in base
            ):
                missing.append(f"{path.relative_to(SRC)}:{node.lineno} {node.args[0].value}")
    assert not missing, "代码里引用了目录不存在的文案键：\n" + "\n".join(missing)
