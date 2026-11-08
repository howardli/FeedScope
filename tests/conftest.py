"""测试统一隔离用户环境：FEEDSCOPE_CONFIG / FEEDSCOPE_SAMPLE_DIR 都指向临时路径。

防止开发者本机 ~/.config/feedscope/sources.toml 的隐藏流/启用配置泄漏进
断言。默认预创建一个只含注释的空配置（= 已完成首启、全默认全启用），让
既有 TUI 用例不触发首启引导；要测引导/配置本身，用 monkeypatch.setenv
把 FEEDSCOPE_CONFIG 覆盖成 tmp_path 下的目标文件（不存在的路径即首启），
样本落盘也只写进临时目录。

通用测试 helper（配置覆写 / HTTP mock / TUI 轮询等待）也收在这里——
之前是各测试文件各抄一份 + 跨模块互相 import，改一处会波及别处。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from feedscope.core import http


@pytest.fixture(autouse=True)
def _isolated_user_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 不用 tmp_path：不少用例断言 tmp_path 的文件清单，隔离目录不能混进去。
    # 预创建的配置显式钉 zh-CN：缺省语言是英文（core.i18n.DEFAULT_LANGUAGE），
    # 而本仓库绝大多数断言写的是中文文案——测试语言必须确定，不随缺省漂
    env_dir = tmp_path_factory.mktemp("fs-user-env")
    config = env_dir / "sources.toml"
    config.write_text('language = "zh-CN"\n# 测试预创建：跳过首启引导，全默认全启用\n', encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(config))
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(env_dir / "samples"))
    # 已读状态与列表缓存同样指进临时目录：不隔离会读写真实用户状态
    # （state.json 的已读标记 / feeds/ 缓存），断言也会被本机历史污染
    monkeypatch.setenv("FEEDSCOPE_STATE", str(env_dir / "state.json"))
    monkeypatch.setenv("FEEDSCOPE_FEED_CACHE", str(env_dir / "feeds"))


@pytest.fixture(autouse=True)
def _pin_chinese_for_direct_calls() -> None:
    """直调 t() 的单元测试（不经 FeedScopeApp/配置）钉中文：语言是进程级
    状态，别的用例（en 冒烟 / 语言屏）改过后不能泄漏过来。App 用例不受
    影响——App.__init__ 总是按自己的配置重设语言。"""
    from feedscope.core import i18n

    i18n.set_language("zh-CN")
    yield
    i18n.set_language("zh-CN")


# ---- 通用 helper（测试模块 `from conftest import …` 直接用）----


def use_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> Path:
    """按用例覆写 FEEDSCOPE_CONFIG 成 tmp_path 下的真实文件，返回文件路径。"""
    p = tmp_path / "sources.toml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(p))
    return p


def mock_http(monkeypatch: pytest.MonkeyPatch, handler) -> httpx.AsyncClient:
    """用 MockTransport 顶替 core/http 的模块级客户端：handler(request) -> Response。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http, "_client", client)
    return client


def recording_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """mock_http 的记录版：包一层把每个请求收进活列表并返回（断言请求序用）。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    mock_http(monkeypatch, wrapped)
    return calls


async def settle(pilot, cond, timeout: float = 5.0) -> bool:
    """轮询等条件成立：worker / 事件循环需要喘息。默认 5s——CI 慢机的冷启动
    常超 3s（历史上 test_account 因此闪断），只在失败路径上多等，不拖慢通过路径。"""
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        await pilot.pause()
        if cond():
            return True
    return cond()


def last_notification(app) -> str:
    """最近一条 notify 的正文（Textual 的通知面板在 _notifications）。"""
    notes = list(getattr(app, "_notifications", []))
    return str(notes[-1].message) if notes else ""


@pytest.fixture(autouse=True)
def _no_browser_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 冒烟测试里禁止启动即预热浏览器：测试源全是 stub，拉真 Chrome
    既慢又可能挂起（开关见 feedscope.ui.app._no_prewarm）。"""
    monkeypatch.setenv("FEEDSCOPE_NO_PREWARM", "1")


# ---- 敌意源（知乎/Reddit/X）共用的通道页签测试替身 ----
# 与生产侧 ChannelTab 对齐：统一取数原语（_FETCH_JS，含 "fetch("）走
# on_fetch 路由，其余脚本（X 的 chunk 收集、知乎的复合脚本）按 markers
# 顺序分派；生命周期计数（resets/opened）供断言。


class FetchReq:
    """一次页签 fetch 的请求记录（断言 URL 与手动补的头）。"""

    def __init__(self, url: str, headers: dict[str, str], referrer: str | None) -> None:
        self.url = url
        self.headers = headers
        self.referrer = referrer

    @property
    def path(self) -> str:
        return urlparse(self.url).path


class FakeTab:
    """ChannelTab 替身：evaluate 按脚本内容分派——非 fetch 脚本查 markers
    （(标记子串, handler(arg)) 按序匹配），统一取数脚本（含 "fetch("）走
    on_fetch(req) -> (状态码或 None, 正文)。"""

    def __init__(self, on_fetch=None, markers: list[tuple[str, object]] | None = None) -> None:
        self._on_fetch = on_fetch
        self._markers = markers or []
        self.resets = 0
        self.opened = 0
        self.fetches: list[FetchReq] = []

    async def get(self):
        self.opened += 1  # harvest 前的重开页签（goto 完成才收割）

    async def fetch(
        self,
        url: str,
        *,
        source: str,
        headers: dict[str, str] | None = None,
        cookie_headers: dict[str, str] | None = None,
        referrer: str | None = None,
        as_text: bool = False,
    ) -> tuple[int | None, object]:
        """统一取数原语的替身：记录请求（含手动头）后走 on_fetch -> (状态码, 正文)。

        生产侧 ChannelTab.fetch 的 __body 解包/报错转译/样本落盘在
        test_channel.py 直测，源内测试只关心路由与语义。
        """
        req = FetchReq(url, dict(headers or {}), referrer)
        self.fetches.append(req)
        status, body = self._on_fetch(req)
        return status, body

    async def eval(self, script: str, arg=None):
        for marker, handler in self._markers:
            if marker in script:
                return handler(arg)
        if "fetch(" in script:
            url, headers, _cookie_headers, referrer, _as_text = arg
            req = FetchReq(url, dict(headers), referrer)
            self.fetches.append(req)
            status, body = self._on_fetch(req)
            return {"__status": status} if status else {"__body": body}
        raise AssertionError(f"FakeTab 未识别的脚本：{script[:40]}…")

    def reset(self) -> None:
        self.resets += 1
