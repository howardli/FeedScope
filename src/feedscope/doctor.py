"""`feedscope doctor`：环境自检。逐项检查、逐项打印，退出码 0 = 全部通过。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from platformdirs import user_cache_path

from feedscope import __version__

# channel="chrome" 硬编码依赖的标准安装位置（macOS）
CHROME_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
# Linux/Windows 下 Playwright 的 channel="chrome" 从 PATH 解析这些二进制名
CHROME_BINS = ("google-chrome", "google-chrome-stable", "chrome", "google-chrome-beta")


def _find_chrome() -> str:
    """Chrome 可执行文件路径；找不到回空串（Linux 包名带 -stable 等也认）。"""
    if CHROME_PATH.exists():
        return str(CHROME_PATH)
    return next((p for name in CHROME_BINS if (p := shutil.which(name))), "")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "[OK]  " if ok else "[FAIL]"
    print(f"{mark} {label}" + (f" - {detail}" if detail else ""))
    return ok


def run_checks() -> int:
    print(f"FeedScope（观流）{__version__} 环境自检")
    print()

    ok = True

    ok &= _check(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        sys.version_info >= (3, 11),
        "" if sys.version_info >= (3, 11) else "需要 3.11+",
    )

    chrome = _find_chrome()
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
    profile = user_cache_path("feedscope") / "chromium-profile"
    state = "已存在" if profile.exists() else "首次运行时自动创建"
    _check("浏览器 profile（信息）", True, f"{profile}（{state}）")

    from feedscope.core.config import config_path, describe

    _check("用户配置 sources.toml（信息）", True, f"{config_path()} - {describe()}")

    print()
    print("全部通过" if ok else "存在失败项：按提示处理后重试")
    return 0 if ok else 1
