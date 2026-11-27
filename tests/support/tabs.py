from __future__ import annotations

from urllib.parse import urlparse


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
