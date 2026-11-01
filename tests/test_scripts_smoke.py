"""scripts/ 导入冒烟：独立脚本不在任何 import 链上，坏导入只能靠这里拦。

x_probe 的 `_read_auth` 导入曾在 ef289b2 收拢后静默烂掉，直到 54a2e2d 才修
——生产代码与测试都没引用 scripts/，同类腐烂会再次无声发生。这里按文件路径
逐个导入（scripts/ 不是包），只验证模块级代码能跑通；脚本自身必须保持
`if __name__ == "__main__"` 入口守卫，否则导入即真跑。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def test_scripts_import_cleanly() -> None:
    paths = sorted(_SCRIPTS_DIR.glob("*.py"))
    assert paths, "scripts/ 没有脚本了？冒烟测试该跟着挪走"
    for path in paths:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
