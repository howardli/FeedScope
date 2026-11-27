"""scripts/ 导入冒烟：独立脚本不在任何 import 链上，坏导入只能靠这里拦。

x_probe 的 `_read_auth` 导入曾在 ef289b2 收拢后静默烂掉，直到 54a2e2d 才修
——生产代码与测试都没引用 scripts/，同类腐烂会再次无声发生。这里按文件路径
逐个导入（scripts/ 不是包），只验证模块级代码能跑通；带 main() 的脚本必须有
`if __name__ == "__main__"` 入口守卫，否则导入即真跑。

scripts/ 内部按模块名互相 import（screenshot → demo_sources / rasterize）：
直接运行时脚本目录天然在 sys.path 上，这里把它补进去，走的是同一条路。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# 仓库根/scripts（本文件在 tests/scripts/ 下：上三级才是仓库根——指错了会把测试目录
# 自己当成脚本目录，「通过」是空转）
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def test_scripts_import_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = sorted(_SCRIPTS_DIR.glob("*.py"))
    assert paths, "scripts/ 没有脚本了？冒烟测试该跟着挪走"
    assert Path(__file__).resolve().parent not in {p.parent for p in paths}, "指到了测试目录自己"
    monkeypatch.syspath_prepend(str(_SCRIPTS_DIR))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if "def main() -> None:" in source:
            assert 'if __name__ == "__main__":' in source, f"{path.name} 有 main() 却没有入口守卫"
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
