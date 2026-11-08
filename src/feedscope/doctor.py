"""`feedscope doctor`：环境自检。逐项检查、逐项打印，退出码 0 = 全部通过。"""

from __future__ import annotations

import sys

from feedscope import __version__
from feedscope.core.browser import PROFILE_DIR
from feedscope.core.const import find_chrome
from feedscope.report import line
from feedscope.core.i18n import t


def _check(label: str, ok: bool, detail: str = "") -> bool:
    return line("OK" if ok else "FAIL", label, detail)


def run_checks() -> int:
    print(t("doctor.title", version=__version__))
    print()

    py_ok = sys.version_info >= (3, 11)
    ok = _check(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        py_ok,
        "" if py_ok else t("doctor.need_py"),
    )

    chrome = find_chrome()
    ok &= _check(
        "Google Chrome",
        bool(chrome),
        chrome or t("doctor.chrome_missing"),
    )

    try:
        import playwright  # noqa: F401
        import textual  # noqa: F401

        ok &= _check(t("doctor.deps_label"), True, t("doctor.importable"))
    except ImportError as exc:
        ok &= _check(t("doctor.deps_label"), False, t("doctor.import_failed", error=exc))

    # 信息项：不算失败
    state = t("doctor.profile_exists") if PROFILE_DIR.exists() else t("doctor.profile_will_create")
    _check(t("doctor.profile_label"), True, f"{PROFILE_DIR}（{state}）")

    from feedscope.core.config import config_path, describe

    _check(t("doctor.config_label"), True, f"{config_path()} - {describe()}")

    from feedscope.core.store import state_path

    state = t("doctor.profile_exists") if state_path().exists() else t("doctor.profile_will_create")
    _check(t("doctor.state_label"), True, f"{state_path()}（{state}）")

    print()
    print(t("doctor.all_ok") if ok else t("doctor.has_failures"))
    return 0 if ok else 1
