"""共享 HTTP 客户端池：httpx 源的统一取数通道与错误出口。

- 进程级单例 AsyncClient：同 host 连接复用（翻页时省掉每次的 TCP/TLS 握手）
- httpx 异常统一在边界包成 SourceError（中文、含源标签，UI 可直接展示）
- 惰性创建：只有真正发请求才建（测试/脚本不碰网络就不建）
- FEEDSCOPE_SAMPLE_DIR 设定时把响应体落盘（feedscope probe 巡检用，平时零开销）

httpx 源（X）走这里；浏览器源走 core/browser.py。
"""

from __future__ import annotations

from typing import Any

import httpx

from feedscope.core import samples
from feedscope.core.const import DESKTOP_UA, HTTP_TIMEOUT
from feedscope.core.source import SourceError

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={"User-Agent": DESKTOP_UA},
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        )
    return _client


async def get(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET 并校验状态码；httpx 异常统一包成 SourceError。

    source 是展示标签（如 "X"），只进错误消息；params / headers 原样透传
    （headers 供带鉴权头的源用，如 X 的 Bearer/Cookie）。
    """
    try:
        r = await get_client().get(url, params=params, headers=headers)
        r.raise_for_status()
    except httpx.TimeoutException:
        raise SourceError(f"{source} 请求超时（{HTTP_TIMEOUT} 秒）：{url}") from None
    except httpx.HTTPStatusError as exc:
        raise SourceError(f"{source} 请求失败（HTTP {exc.response.status_code}）：{url}") from None
    except httpx.HTTPError as exc:
        raise SourceError(f"{source} 请求失败：{exc}") from exc
    samples.save(samples.url_name(source, str(r.url)), r.content)
    return r


async def close() -> None:
    """退出前释放连接池；幂等，未创建则无事发生。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
