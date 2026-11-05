"""测试统一隔离用户环境：FEEDSCOPE_CONFIG / FEEDSCOPE_SAMPLE_DIR 都指向临时路径。

防止开发者本机 ~/.config/feedscope/sources.toml 的隐藏流/启用配置泄漏进
断言。默认预创建一个只含注释的空配置（= 已完成首启、全默认全启用），让
既有 TUI 用例不触发首启引导；要测引导/配置本身，用 monkeypatch.setenv
把 FEEDSCOPE_CONFIG 覆盖成 tmp_path 下的目标文件（不存在的路径即首启），
样本落盘也只写进临时目录。
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_env(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 不用 tmp_path：不少用例断言 tmp_path 的文件清单，隔离目录不能混进去
    env_dir = tmp_path_factory.mktemp("fs-user-env")
    config = env_dir / "sources.toml"
    config.write_text("# 测试预创建：跳过首启引导，全默认全启用\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(config))
    monkeypatch.setenv("FEEDSCOPE_SAMPLE_DIR", str(env_dir / "samples"))


@pytest.fixture(autouse=True)
def _no_browser_prewarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 冒烟测试里禁止启动即预热浏览器：测试源全是 stub，拉真 Chrome
    既慢又可能挂起（POINT 预热闸门见 feedscope.ui.app）。"""
    monkeypatch.setattr("feedscope.ui.app.PREWARM", False)


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
