"""`feedscope doctor`：环境自检。逐项检查、逐项打印，退出码 0 = 全部通过。"""

from __future__ import annotations

import sys

from feedscope import __version__
from feedscope.core.browser import PROFILE_DIR
from feedscope.core.const import find_chrome
from feedscope.report import line


def _check(label: str, ok: bool, detail: str = "") -> bool:
    return line("OK" if ok else "FAIL", label, detail)


def run_checks() -> int:
    print(f"FeedScope（观流）{__version__} 环境自检")
    print()

    py_ok = sys.version_info >= (3, 11)
    ok = _check(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        py_ok,
        "" if py_ok else "需要 3.11+",
    )

    chrome = find_chrome()
    ok &= _check(
        "Google Chrome",
        bool(chrome),
        chrome or '未找到（macOS 标准位置或 PATH 里的 google-chrome），channel="chrome" 依赖它',
    )

    try:
        import playwright  # noqa: F401
        import textual  # noqa: F401

        ok &= _check("依赖 textual / playwright", True, "可导入")
    except ImportError as exc:
        ok &= _check("依赖 textual / playwright", False, f"导入失败：{exc}（先 uv sync）")

    # 信息项：不算失败
    state = "已存在" if PROFILE_DIR.exists() else "首次运行时自动创建"
    _check("浏览器 profile（信息）", True, f"{PROFILE_DIR}（{state}）")

    from feedscope.core.config import config_path, describe

    _check("用户配置 sources.toml（信息）", True, f"{config_path()} - {describe()}")

    print()
    print("全部通过" if ok else "存在失败项：按提示处理后重试")
    return 0 if ok else 1
