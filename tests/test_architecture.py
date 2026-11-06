"""架构守卫：分层纪律的自动化检查（此前靠人肉 grep 复核，现在 CI 常驻）。

用 AST 扫描 import 关系与属性访问，把项目文档里声明的分层不变量钉死——
违反不是「风格不好」而是测试红，下一个人不用读全库也知道边界在哪：

- 依赖方向单向：ui -> 顶层业务（login/subscriptions/...）-> sources -> core。
  core 永远是最底层（不得 import ui/sources/顶层业务），sources 不得回头
  import ui/顶层业务，ui 不得越过顶层业务直插具体源（注册引导的包级
  import 除外）
- parse 是纯函数叶子（sources/<id>/parse.py）：只依赖 core 与解析库，
  不做 IO（http/browser/playwright 一概不进），不依赖同包 fetch/__init__
  ——即 sources/__init__ 模板自述的「__init__ -> fetch -> parse -> core」
- UI 不读 FeedItem.extra 的源私有键（core/models 的契约：extra 是
  「源 -> 详情抓取」的上下文，UI 只消费公开字段与源词汇表）
- 单一实现不被绕开：配置落盘只走 core.config._write_config，注册表只经
  @register / install_sources 变更（_REGISTRY 不被外部直抠）

合法例外是演进出来的（如 core 需要共享某个新底层件）：改规则先改这里的
常量与注释，别删测试。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "feedscope"

# 顶层业务模块（ui 之下、sources 之上；cli 也在此列——它是组装点，可以引 ui）
_TOP = {"cli", "login", "subscriptions", "probe", "doctor", "report"}

# parse 层禁入的 IO 通道（网络 / 浏览器 / 落盘）；urllib.parse、feedparser
# 这类纯解析件不在其列
_IO_MODULES = {"httpx", "playwright", "webbrowser", "requests", "aiohttp"}
_IO_FEEDSCOPE = {"feedscope.core.http", "feedscope.core.browser", "feedscope.core.samples"}


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


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
            elif node.module.startswith("feedscope"):
                out.add(node.module)
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


def _sources_packages() -> list[str]:
    """已注册的源包名（sources/ 下有 __init__.py 的子目录）。"""
    return sorted(p.parent.name for p in (SRC / "sources").glob("*/__init__.py"))


# ---- 依赖方向单向 ----


def test_core_stays_bottom() -> None:
    """core 是最底层：不得 import ui / sources / 任何顶层业务模块。"""
    for f in (SRC / "core").glob("*.py"):
        for imp in _feedscope_imports(f):
            assert imp.startswith("feedscope.core"), f"{f.name} 越层引用 {imp}（core 之上没有依赖）"


def test_sources_never_reach_upward() -> None:
    """sources 不得回头 import ui / 顶层业务（探针脚本走 scripts/，不进源码）。"""
    for f in (SRC / "sources").rglob("*.py"):
        for imp in _feedscope_imports(f):
            assert not imp.startswith("feedscope.ui"), f"{f} 引用了 UI"
            beyond = imp.split(".")
            assert not (len(beyond) > 1 and beyond[1] in _TOP), f"{f} 引用了顶层业务 {imp}"


def test_ui_does_not_bypass_business_layer() -> None:
    """ui 不得直插具体源（feedscope.sources.<id>）：源差异经顶层业务
    （subscriptions）与源词汇表（ClassVar）到达 UI；`import feedscope.sources`
    整包是注册引导（app.py 的 noqa import），唯一许可。"""
    for f in (SRC / "ui").rglob("*.py"):
        for imp in _feedscope_imports(f):
            assert imp == "feedscope.sources" or not imp.startswith("feedscope.sources"), (
                f"{f} 直插了具体源 {imp}（应走顶层业务或源词汇表）"
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
