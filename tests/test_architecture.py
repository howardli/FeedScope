"""架构守卫：把代码库的「形状」钉成测试（此前一半靠人肉 grep，现在 CI 常驻）。

用 AST 扫描 import 关系、命名与定义，违反不是「风格不好」而是测试红；每个
测试的 docstring 写着规则、理由与例外——被拦住时先读它。按四组：

依赖方向
- 依赖只指向下方（见 _MAY_IMPORT 的层级表）：cli → commands → services →
  sources → core；ui → services → sources → core。core 永远是最底层，sources
  不得回头 import services/commands/ui，ui 不得越过 services 直插具体源
  （注册引导的包级 import 除外），commands 里只有 tui 这一个组装点碰 ui
- 模块间 import 无环（函数内的惰性导入也算依赖）；依赖写在模块顶部，函数内
  import 只限白名单（_LAZY_IMPORTS_ALLOWED）；不 import 别的模块的下划线私有名

源插件的形状（sources/<id>/）
- parse.py 是纯函数叶子：只依赖 core、不做 IO、不 import 同包 fetch/__init__
  ——即 sources/__init__ 模板自述的「__init__ -> fetch -> parse -> core」
- parse.py 的公开函数一律 parse_ 前缀；__init__.py 只有 @register 的 Source
  子类（Loader 住 fetch.py）

契约与单一实现
- UI 不读 FeedItem.extra 的源私有键；配置落盘只走 core.config._write_config；
  注册表只经 @register / install_sources / 查询函数变更

工程形状
- 命令模块与 commands.ALL 一一对应（NAME = 文件名，顺序 = --help 顺序）
- 测试树镜像 src 分层，测试模块之间不互相 import
- 复杂度预算：模块 ≤ 750 行、函数 ≤ 90 行（豁免在 _BUDGET_EXEMPT 登记理由）
- AGENTS.md 的模块地图与磁盘双向一致

合法例外是演进出来的（如 core 需要共享某个新底层件）：改规则先改这里的
常量与注释，别删测试，也别为了过而放宽——AGENTS.md 第 2 节是同一份清单的
手册视图，两边一起改。
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "feedscope"

# 分层表：键 = 顶层包名（单文件模块也算一层），值 = 它的文件允许 import 的
# feedscope 顶层名。新增模块按所在目录自动归层，不需要来这里登记。
_MAY_IMPORT: dict[str, set[str]] = {
    "core": {"core"},
    "sources": {"sources", "core"},
    "services": {"services", "sources", "core"},
    "commands": {"commands", "services", "sources", "core"},
    "ui": {"ui", "services", "core"},  # `import feedscope.sources` 整包（注册引导）另行放行
    "cli": {"cli", "commands", "services", "sources", "core", "ui"},
}

# parse 层禁入的 IO 通道（网络 / 浏览器 / 落盘）；urllib.parse、feedparser
# 这类纯解析件不在其列
_IO_MODULES = {"httpx", "playwright", "webbrowser", "requests", "aiohttp"}
_IO_FEEDSCOPE = {"feedscope.core.http", "feedscope.core.browser", "feedscope.core.samples"}


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.parent != SRC)  # 包根的 __init__/__main__ 不属于任何层


# 包根下的条目名（子包 / 模块，不含 __init__ / __main__）：`from feedscope import x`
# 的 x 在其中就是一次对 feedscope.x 的依赖
_ROOT_ENTRIES = {
    p.name if p.is_dir() else p.stem for p in SRC.iterdir() if p.name != "__pycache__"
} - {"__init__", "__main__"}


def _layer(path: Path) -> str:
    """文件所属的层 = 相对包根的第一段路径名（单文件模块去掉 .py）。"""
    first = path.relative_to(SRC).parts[0]
    return first[:-3] if first.endswith(".py") else first


def _feedscope_imports(path: Path) -> set[str]:
    """文件 import 的 feedscope.* 模块全名（相对导入按所在包还原）。"""
    package = path.parent.relative_to(SRC.parent).as_posix().replace("/", ".")
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("feedscope"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:  # 相对导入：from .fetch import x / from . import x
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                out.add(f"{base}.{node.module}" if node.module else base)
            elif node.module == "feedscope":
                # from feedscope import x：x 是子包 / 模块即依赖 feedscope.x；
                # 其余（__version__ 这类根常量）只记根
                out.update(
                    f"feedscope.{a.name}" if a.name in _ROOT_ENTRIES else "feedscope"
                    for a in node.names
                )
            elif node.module.startswith("feedscope"):
                out.add(node.module)
    return out


def _imported_layer(module: str) -> str:
    """feedscope.x.y -> x（被 import 模块所在的层）。"""
    return module.split(".")[1] if "." in module else module


def _dotted(path: Path) -> str:
    """文件的模块全名（包的 __init__ 记到包名）。"""
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _outside_type_checking(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """运行时真会执行的 import 语句（TYPE_CHECKING 块里的只是注解，不算依赖）。"""
    checked: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            checked.update(id(x) for x in ast.walk(node))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in checked
    ]


def _runtime_imports(path: Path) -> set[str]:
    """运行时依赖的 feedscope 模块全名（模块级 + 函数内的惰性导入都算）。
    `from feedscope.core import browser` 解析到子模块本身，这样环才查得准。"""
    package = path.parent.relative_to(SRC.parent).as_posix().replace("/", ".")
    out: set[str] = set()
    for node in _outside_type_checking(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("feedscope"))
        elif not node.module:
            continue
        elif node.level:  # 相对导入：from .fetch import x / from . import x
            base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
            out.add(f"{base}.{node.module}" if node.module else base)
        elif node.module.startswith("feedscope"):
            out.update(
                name if (name := f"{node.module}.{a.name}") in _MODULES else node.module
                for a in node.names
            )
    return out


def _function_level_imports(path: Path) -> set[str]:
    """函数体内才执行的 import 的顶层模块名（stdlib / 三方 / feedscope 一并算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = {id(x) for x in tree.body}
    out: set[str] = set()
    for node in _outside_type_checking(tree):
        if id(node) in top_level:
            continue
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif node.module:
            out.add(node.module.split(".")[0])
    return out


def _plain_imports(path: Path) -> set[str]:
    """文件 import 的顶层模块名（stdlib / 三方；本项目 src 无相对导入，绝对名即可判）。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return out


_MODULES = {_dotted(p) for p in SRC.rglob("*.py")}  # 全部模块全名（import 名解析用）


def _sources_packages() -> list[str]:
    """已注册的源包名（sources/ 下有 __init__.py 的子目录）。"""
    return sorted(p.parent.name for p in (SRC / "sources").glob("*/__init__.py"))


# ---- 依赖方向单向（一张层级表管全部）----


def test_every_module_belongs_to_a_declared_layer() -> None:
    """包根下只许有分层表里的条目：新冒出来的顶层模块 / 包先来这里定层。"""
    top = {p.name if p.is_dir() else p.name[:-3] for p in SRC.iterdir() if p.name != "__pycache__"}
    top -= {"__init__", "__main__"}
    assert top == set(_MAY_IMPORT), (
        f"包根条目 {sorted(top)} 与分层表 {sorted(_MAY_IMPORT)} 不一致：新模块先定层"
    )


def test_dependencies_only_point_downward() -> None:
    """每个文件 import 的 feedscope 模块全在它那一层允许的集合里（含函数内的惰性导入）。"""
    for f in _py_files():
        allowed = _MAY_IMPORT[_layer(f)]
        for imp in _feedscope_imports(f):
            if imp == "feedscope":
                continue  # 包根常量（__version__）：哪一层都可以取
            target = _imported_layer(imp)
            if _layer(f) == "ui" and imp == "feedscope.sources":
                continue  # 注册引导的包级 import（app.py 的 noqa import），唯一许可
            if _layer(f) == "commands" and f.name == "tui.py" and target == "ui":
                continue  # tui 命令就是 TUI 的组装点
            assert target in allowed, (
                f"{f.relative_to(SRC)} 越层引用 {imp}（{_layer(f)} 层只许 import {sorted(allowed)}）"
            )


def test_ui_does_not_bypass_business_layer() -> None:
    """ui 不得直插具体源（feedscope.sources.<id>）：源差异经 services 与源词汇表
    （ClassVar）到达 UI；`import feedscope.sources` 整包是注册引导（app.py 的
    noqa import），唯一许可。"""
    for f in (SRC / "ui").rglob("*.py"):
        for imp in _feedscope_imports(f):
            assert imp == "feedscope.sources" or not imp.startswith("feedscope.sources"), (
                f"{f} 直插了具体源 {imp}（应走 services 或源词汇表）"
            )


# ---- 源包内分层：__init__ -> fetch -> parse -> core ----


def test_parse_is_pure_leaf() -> None:
    """parse 只依赖 core 与解析库：不 import 同包 fetch/__init__（叶子层），
    不带 IO 通道（网络/浏览器/落盘），import 的 feedscope 模块全在 core。"""
    for pkg in _sources_packages():
        f = SRC / "sources" / pkg / "parse.py"
        for imp in _feedscope_imports(f):
            assert imp.startswith("feedscope.core"), f"{pkg}/parse 引用了 core 之外的 {imp}"
            assert imp not in _IO_FEEDSCOPE, f"{pkg}/parse 做 IO（{imp}）——解析层保持纯函数"
        assert not (_plain_imports(f) & _IO_MODULES), f"{pkg}/parse 引入了 IO 库"


def test_fetch_depends_only_core_and_own_parse() -> None:
    """fetch 只依赖 core 与同包 parse——不跨界引别的源（源间复用一律下沉 core）。"""
    for pkg in _sources_packages():
        f = SRC / "sources" / pkg / "fetch.py"
        own = f"feedscope.sources.{pkg}"
        for imp in _feedscope_imports(f):
            ok = imp.startswith("feedscope.core") or imp.startswith(own)
            assert ok, f"{pkg}/fetch 引用了 {imp}（只许 core 与本包 parse）"


def test_source_init_dispatches_within_package() -> None:
    """源 __init__ 只 import core 与本包 fetch/parse（能力声明与分派不出包）。"""
    for pkg in _sources_packages():
        f = SRC / "sources" / pkg / "__init__.py"
        own = f"feedscope.sources.{pkg}"
        for imp in _feedscope_imports(f):
            ok = imp.startswith("feedscope.core") or imp.startswith(own)
            assert ok, f"{pkg}/__init__ 引用了 {imp}（能力声明不出包）"


def test_source_init_declares_only_the_source_class() -> None:
    """源 __init__ 的顶层类只有一个：@register 的 Source 子类。

    Loader 一律住 fetch.py（持资源、做 IO 的东西归取数层）；曾有三个只为
    设一行源标签而存在的 *DetailLoader 空壳子类——标签现在是
    SliceDetailLoader.from_detail 的参数，别再长回来。"""
    for pkg in _sources_packages():
        f = SRC / "sources" / pkg / "__init__.py"
        classes = [n.name for n in ast.parse(f.read_text(encoding="utf-8")).body if isinstance(n, ast.ClassDef)]
        assert len(classes) == 1 and classes[0].endswith("Source"), (
            f"{pkg}/__init__ 的顶层类应为单个 Source 子类，实际 {classes}"
        )


def test_parse_public_api_is_parse_prefixed() -> None:
    """parse.py 的公开函数一律 parse_ 前缀（= 该源解析层的入口）；带 _ 的私有件
    不出模块（下一条守）。X / 知乎曾把入口全写成 _私有名、fetch 跨模块去拿——
    同一层两种约定并存，读的人分不清哪些是合同哪些是实现细节。"""
    for pkg in _sources_packages():
        f = SRC / "sources" / pkg / "parse.py"
        public = [
            n.name
            for n in ast.parse(f.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        assert all(name.startswith("parse_") for name in public), (
            f"{pkg}/parse 的公开函数 {public} 应以 parse_ 开头（其余加 _ 前缀留作模块私有）"
        )


def test_no_cross_module_private_imports() -> None:
    """src 内任何模块都不得 import 另一个模块的下划线私有名：私有名是实现的
    细节，跨模块去拿 = 对它耦合，改名即断。要给外面用就给它公开的名字。
    scripts/ 对 src 同理（诊断脚本 x_probe 曾伸手拿 X 取数层的三个私有名——
    现在走公开的 shape_report）。（测试白盒探私有不在此列。）"""
    repo = SRC.parents[1]
    for f in [*_py_files(), *sorted((repo / "scripts").glob("*.py"))]:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name.rsplit(".", 1)[-1] for a in node.names]
            private = [n for n in names if n.startswith("_") and not n.startswith("__")]
            assert not private, f"{f.relative_to(repo)} import 了私有名 {private}"


# ---- 契约与单一实现 ----


def test_ui_never_reads_item_extra() -> None:
    """UI 不读 FeedItem.extra：extra 的键是源内私有约定（详情抓取的上下文，
    见 core/models 的契约），UI 读它等于埋一条绕开模型的隐式依赖。AST 查
    属性访问，docstring 里的提及不算。"""
    for f in (SRC / "ui").rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "extra":
                raise AssertionError(f"{f} 访问了 .extra（extra 是源私有上下文，UI 不读取）")


def test_config_write_path_is_single() -> None:
    """配置落盘唯一走 core.config._write_config：别处手写 open/os.replace
    落 sources.toml 会绕开原子写与保留策略。私有名不被外部引用即守住。"""
    for f in _py_files():
        if f.parent.name == "core" and f.name == "config.py":
            continue
        assert "_write_config" not in f.read_text(encoding="utf-8"), (
            f"{f} 触碰了 _write_config（配置写路径归 core/config 唯一所有）"
        )


def test_registry_is_touched_via_public_api_only() -> None:
    """注册表只经 @register / install_sources / 查询函数变更：_REGISTRY 被
    外部直抠（screenshot 曾这么干）是对实现细节的耦合，换数据结构即断。"""
    for f in _py_files():
        if f.parent.name == "core" and f.name == "registry.py":
            continue
        assert "_REGISTRY" not in f.read_text(encoding="utf-8"), (
            f"{f} 直抠了注册表私有字典（走 registry 的公开 API）"
        )



# ---- 依赖图形状：无环、惰性导入有据、UI 层内方向 ----


def test_no_import_cycles() -> None:
    """feedscope 模块之间的运行时依赖无环（函数内的惰性导入也算依赖）。

    环 = import 顺序敏感、只能靠把 import 藏进函数里绕开——读的人要同时
    理解两边的初始化时序。曾有两处：core.source <-> core.browser（靠 login_gate
    里的惰性导入硬撑）、ui.widgets -> ui.screens.main（列表 resize 反向调屏）。
    要打破环就调整结构（抽公共件 / 改成消息），别把 import 往函数里塞。"""
    graph = {_dotted(f): _runtime_imports(f) for f in SRC.rglob("*.py")}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []
    counter = itertools.count()

    def strong(v: str) -> None:  # Tarjan：递归深度 = 依赖链长度，远不到解释器上限
        index[v] = low[v] = next(counter)
        stack.append(v)
        on_stack.add(v)
        for w in sorted(graph[v]):
            if w not in graph:
                continue  # 包名之类的非模块节点
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                cycles.append(sorted(comp))

    for module in sorted(graph):
        if module not in index:
            strong(module)
    assert not cycles, f"模块间 import 成环：{cycles}"


# 函数内才执行的 import：只许这几处，每处写明理由（白名单精确相等——新增要来
# 这里登记，删掉的要同步清理）
_LAZY_IMPORTS_ALLOWED: dict[str, set[str]] = {
    "commands/tui.py": {"feedscope"},  # Textual 只在真要起界面时才加载，纯命令行路径不碰
    "commands/doctor.py": {"playwright", "textual"},  # 可导入性探测本身（try/except ImportError）
    "core/browser.py": {"playwright"},  # 重设施：真用浏览器时才加载
    "services/login.py": {"playwright"},  # 同上（有头登录）
    "core/store.py": {"fcntl"},  # POSIX only：Windows 上导入失败也不挡启动
}


def test_function_level_imports_are_allowlisted() -> None:
    """依赖写在模块顶部。函数里的 import 是「藏起来的依赖」——读模块头看不出
    它要什么，测试替身也要多绕一步。只有重设施 / 可选依赖 / 探测才放函数里，
    且必须在上表登记。"""
    found: dict[str, set[str]] = {}
    for f in _py_files():
        if names := _function_level_imports(f):
            found[f.relative_to(SRC).as_posix()] = names
    assert found == _LAZY_IMPORTS_ALLOWED, (
        f"函数内 import 与白名单不一致（多出来的要登记理由或提到模块顶部，\n"
        f"少掉的要清理白名单）：\n实际 {found}\n白名单 {_LAZY_IMPORTS_ALLOWED}"
    )


def test_ui_support_modules_do_not_import_screens() -> None:
    """ui 层内的方向：screens -> 支撑件（widgets / dialogs / layout / list_render /
    feedback / …），反过来不行。ui.widgets 曾在函数里懒导入 MainScreen 调它的
    rebuild_items（见无环守卫）；控件要屏做事就 post_message。组装点只有 app.py。"""
    for f in (SRC / "ui").glob("*.py"):
        if f.name in ("__init__.py", "app.py"):
            continue
        bad = [imp for imp in _feedscope_imports(f) if imp.startswith("feedscope.ui.screens")]
        assert not bad, f"{f.name} 是 UI 支撑件，不得 import 屏幕 {bad}（方向只有 screens -> 支撑件）"


# ---- 工程形状：命令契约 / 测试树 / 复杂度预算 ----

_TESTS = Path(__file__).resolve().parent
_COMMAND_HELPERS = {"__init__.py", "base.py", "report.py"}  # commands/ 里不是命令的模块


def test_commands_follow_the_module_contract() -> None:
    """commands/ 一个文件一个子命令，且 commands.ALL 与目录一一对应：

    - 每个命令模块的 NAME 就是文件名（分发键 = 文件名，不用去表里对照）
    - 除三个共用件外的每个模块都登记在 ALL（漏登记 = 写了却到不了 --help），
      ALL 里也没有登记目录里不存在的命令
    - ALL 的顺序 = --help 里的命令顺序；首项是默认命令（无子命令时的落点）
    """
    from feedscope import cli, commands

    modules = {p.name for p in (SRC / "commands").glob("*.py")} - _COMMAND_HELPERS
    listed = [m.NAME for m in commands.ALL]
    assert sorted(f"{name}.py" for name in listed) == sorted(modules), (
        f"commands.ALL {listed} 与目录 {sorted(modules)} 不一致"
    )
    assert len(set(listed)) == len(listed), "commands.ALL 里有重复"
    for module in commands.ALL:
        assert Path(module.__file__ or "").stem == module.NAME, f"{module.NAME} 与文件名不一致"
    subcommands = cli.build_parser()._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert list(subcommands) == listed, "--help 里的命令顺序应等于 commands.ALL 的顺序"
    assert listed[0] == "tui", "首项是默认命令（cli.main 无子命令时的落点）"


def test_test_tree_mirrors_source_tree() -> None:
    """tests/ 的目录镜像 src/feedscope 的分层，找测试不用查地图：

    - tests/ 根目录只放隔离 fixture 与两个跨切面的静态守卫，不再长出平铺的测试文件
      （58 个文件挤在一层、test_parsers 1127 行混装三个源，是这次规整的起点）
    - 每个 src 层在 tests/ 下有同名目录；每个源包有 tests/sources/<id>/ 且解析层
      一定有 test_parse.py（parse 是纯函数，没有测试等于没有安全网）
    """
    root = {p.name for p in _TESTS.glob("*.py")}
    assert root == {"__init__.py", "conftest.py", "test_architecture.py", "test_i18n_no_hardcoded.py"}, (
        f"tests/ 根目录出现了平铺的测试文件：{sorted(root - {'__init__.py', 'conftest.py'})}（按层放进子目录）"
    )
    layers = {p.name for p in SRC.iterdir() if p.is_dir()} | {"commands", "services"} - {"i18n", "themes"}
    for layer in sorted(layers):
        assert (_TESTS / layer).is_dir(), f"src 有 {layer}/ 层，tests/ 下缺同名目录"
    for pkg in _sources_packages():
        assert (_TESTS / "sources" / pkg / "test_parse.py").is_file(), f"tests/sources/{pkg}/ 缺 test_parse.py"


def test_tests_do_not_import_other_test_modules() -> None:
    """测试模块之间不互相 import：共用的东西在 tests/support/（替身与工具）、
    tests/sources/<id>/fixtures.py（夹具）或子树 conftest.py（fixture）。

    测试 import 测试 = 一边收集一边改另一边的模块级状态，且被 import 方一改名
    全线断（test_parsers 曾被三个模块伸手拿私有构造件）。"""
    pattern = re.compile(r"^tests(\.[a-z_0-9]+)*\.test_[a-z_0-9]+$")
    for f in _TESTS.rglob("*.py"):
        if f.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and pattern.match(node.module):
                raise AssertionError(f"{f.relative_to(_TESTS)} import 了另一个测试模块 {node.module}")
            if isinstance(node, ast.Import) and any(
                pattern.match(a.name) for a in node.names
            ):
                raise AssertionError(f"{f.relative_to(_TESTS)} import 了另一个测试模块")


# 复杂度预算：防倒退而不是刷数字。超了就拆（拆法见 AGENTS.md「常见任务」）；
# 确有必要突破的，在下面的豁免表里登记模块与理由——登记是给评审看的，不是绕道。
_MODULE_LINE_BUDGET = 750
_FUNCTION_LINE_BUDGET = 90
_BUDGET_EXEMPT: dict[str, str] = {}  # 相对路径 -> 理由


def test_complexity_budgets() -> None:
    """单个模块不超过 _MODULE_LINE_BUDGET 行、单个函数不超过 _FUNCTION_LINE_BUDGET 行
    （src 与 scripts/ 同样受限：脚本不在 import 链上，最容易长成没人管的巨型文件）。

    这两个数取在当前最大值之上一点（模块 675 / 函数 66）：本仓库不需要巨型
    模块也能把事说清，预算只是把「再往里塞之前先想想拆不拆」变成一条红灯。
    """
    repo = SRC.parents[1]
    governed = [*(f.relative_to(repo).as_posix() for f in _py_files()),
                *(f.relative_to(repo).as_posix() for f in sorted((repo / "scripts").glob("*.py")))]
    long_modules = {}
    for rel in governed:
        n = len((repo / rel).read_text(encoding="utf-8").splitlines())
        if n > _MODULE_LINE_BUDGET and rel not in _BUDGET_EXEMPT:
            long_modules[rel] = n
    assert not long_modules, f"模块超出 {_MODULE_LINE_BUDGET} 行预算：{long_modules}"

    long_functions = []
    for rel in governed:
        if rel in _BUDGET_EXEMPT:
            continue
        f = repo / rel
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = max(getattr(x, "lineno", node.lineno) for x in ast.walk(node))
                if end - node.lineno + 1 > _FUNCTION_LINE_BUDGET:
                    long_functions.append((rel, node.name, end - node.lineno + 1))
    assert not long_functions, f"函数超出 {_FUNCTION_LINE_BUDGET} 行预算：{long_functions}"


def test_agents_module_map_matches_tree() -> None:
    """AGENTS.md 的模块地图与磁盘双向一致：每个模块在地图里有一行，地图里的每个
    路径都真实存在；手册正文引用的仓库路径（tests/ scripts/ …）也都存在。

    手册是代理认识这个代码库的入口——地图漏一个模块，那个模块就「不存在」；
    引用一个已搬走的路径，读的人按图索骥会落空。新增模块 = 改代码 + 补地图一行。
    （子包的 __init__.py 在地图里由分组标题代表，不单列。）"""
    agents = SRC.parents[1] / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    mapped = set(re.findall(r"`(src/feedscope/[\w/]+\.py)`", text))
    actual = {
        f"src/feedscope/{p.relative_to(SRC).as_posix()}"
        for p in SRC.rglob("*.py")
        if not (p.name == "__init__.py" and p.parent != SRC)
    }
    assert mapped - actual == set(), f"AGENTS.md 地图引用了不存在的模块：{sorted(mapped - actual)}"
    assert actual - mapped == set(), f"这些模块不在 AGENTS.md 地图里：{sorted(actual - mapped)}"

    repo = agents.parent
    missing = sorted(
        path
        for path in re.findall(r"`((?:src|tests|scripts|docs)/[\w/.]+\.(?:py|toml|md|png))`", text)
        if not (repo / path).exists()
    )
    assert not missing, f"AGENTS.md 引用了不存在的仓库路径：{missing}"
