"""共享 HTTP 客户端池：httpx 源的统一取数通道与错误出口。

- 进程级单例 AsyncClient：同 host 连接复用（翻页时省掉每次的 TCP/TLS 握手）
- httpx 异常统一在边界包成 SourceError（中文、含源标签，UI 可直接展示）
- 惰性创建：只有真正发请求才建（测试/脚本不碰网络就不建）
- FEEDSCOPE_SAMPLE_DIR 设定时把响应体落盘（feedscope probe 巡检用，平时零开销）

友好源（RSS/HN/V2EX/Telegram）走这里；敌意源（知乎/Reddit/X）的数据
通道走 core/browser.ChannelTab——但边界按请求定不按源定：敌意源里的公开
请求（X 收割的 CDN chunk 下载）也走这里。
"""

from __future__ import annotations

from typing import Any, TypeVar

import httpx

from feedscope.core import samples
from feedscope.core.const import DESKTOP_UA, HTTP_TIMEOUT
from feedscope.core.source import SourceError

_J = TypeVar("_J", dict, list)  # get_json 的期望顶层形状（dict / list）

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
    allow_304: bool = False,
    sample: bool = True,
) -> httpx.Response:
    """GET 并校验状态码；httpx 异常统一包成 SourceError。

    source 是展示标签（如 "X"），只进错误消息；params / headers 原样透传
    （headers 供带鉴权头的源用，如 X 的 Bearer/Cookie）。allow_304 给条件
    请求的源用（rss 的 ETag/Last-Modified 协商）：304 是「内容没变」的正
    常语义而非失败，放行给调用方自己回放缓存；其余状态码照常校验。sample
    关掉样本落盘（X 收割 chunk 下载的 JS bundle 不是 API 响应，留着只占地方）。
    """
    try:
        r = await get_client().get(url, params=params, headers=headers)
        if not (allow_304 and r.status_code == 304):
            r.raise_for_status()  # httpx 新版对 3xx 也抛，304 必须先放行
    except httpx.TimeoutException:
        raise SourceError(f"{source} 请求超时（{HTTP_TIMEOUT} 秒）：{url}") from None
    except httpx.HTTPStatusError as exc:
        raise SourceError(f"{source} 请求失败（HTTP {exc.response.status_code}）：{url}") from None
    except httpx.HTTPError as exc:
        raise SourceError(f"{source} 请求失败：{exc}") from exc
    if sample:
        samples.save(samples.url_name(source, str(r.url)), r.content)
    return r


async def get_json(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    expect: type[_J],
) -> _J:
    """GET 并按 JSON 解析 + 形状校验：JSON API 源（HN/V2EX）共用的小原语。

    两种失败分开报：「返回的不是 JSON」是网络/代理层问题，「返回结构异常，
    接口可能已变」是改版信号（probe 巡检据此定位）。expect 是期望的顶层
    形状（dict / list），形状不对视为改版而非崩溃。
    """
    r = await get(url, source=source, params=params)
    try:
        data = r.json()
    except ValueError:
        raise SourceError(f"{source} 返回的不是 JSON") from None
    if not isinstance(data, expect):
        raise SourceError(f"{source} 返回结构异常，接口可能已变") from None
    return data


async def close() -> None:
    """退出前释放连接池；幂等，未创建则无事发生。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
