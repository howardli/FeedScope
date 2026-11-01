"""X 改版自救工具：重新抠 GraphQL queryId，并验证 TweetDetail 是否还能用。

X 的 queryId 会随改版失效（报「时间线/详情接口报错」），到时跑这个脚本：
    uv run python scripts/x_probe.py
- 读 profile cookie（本地操作，不打印 cookie 值）
- 浏览器开 x.com/home 收集 script URL，下载后 grep operationName/queryId 对
  （时间线端点够用：不校验 features，换个 queryId 就能跑）
- TweetDetail 开一条稳定推文页，网络层捕获浏览器自己发的完整请求
  （queryId + variables + features 一次拿全——features 决定响应形状，Grok
  译文字段就靠其中的 grok_show_grok_translated_post 开关，grep 抠不出来），
  再用 httpx 重放验证
输出全部脱敏（无 cookie、无完整响应体），把打印出来的 queryId / 变量集 /
features 抄进 fetch.py 的对应常量即可。
"""

import asyncio
import json
import re
import sys
import urllib.parse

import httpx

from feedscope.core.browser import close_session, get_session
from feedscope.sources.x import LoginRequired, _x_session

# jack 首推，稳定存在；打开它的详情页即触发浏览器发 TweetDetail 请求
_STABLE_TWEET = "https://x.com/jack/status/20"


async def _collect_scripts(context) -> list[str]:
    """开页收集 script src（页面无需渲染完成，脚本标签即可）。"""
    page = await context.new_page()
    try:
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        srcs = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        html = await page.content()
        extra = re.findall(r"https://abs\.twimg\.com/[^\"'\\s]+\.js", html)
        return sorted(set(srcs) | set(extra))
    finally:
        await page.close()


async def _capture_detail_url(context) -> str | None:
    """开稳定推文页，网络层收 TweetDetail 完整请求 URL（渲染期请求，轮询等它出现）。"""
    box: dict[str, str] = {}

    def on_request(req):
        if "/i/api/graphql/" in req.url and req.url.split("?")[0].endswith("/TweetDetail"):
            box["url"] = req.url

    page = await context.new_page()
    try:
        page.on("request", on_request)
        await page.goto(_STABLE_TWEET, wait_until="domcontentloaded", timeout=30000)
        for _ in range(30):  # 最多等 15 秒
            if box:
                break
            await page.wait_for_timeout(500)
    finally:
        await page.close()
    return box.get("url")


async def _probe() -> None:
    try:
        session = await _x_session("探针脚本")  # 判登/请求头策略与源内共用一处
    except LoginRequired:
        print("NO_AUTH：先运行 feedscope login x")
        sys.exit(1)
    print("AUTH_OK")

    urls = await get_session().run(_collect_scripts)
    ops: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": session.ua}
    ) as client:
        for u in urls:
            try:
                text = (await client.get(u)).text
            except Exception as exc:
                print(f"FETCH_FAIL {u} {exc}")
                continue
            for m in re.finditer(r'queryId:"([^"]+)",operationName:"([^"]+)"', text):
                ops.setdefault(m.group(2), m.group(1))

        keep = {k: v for k, v in ops.items() if "Tweet" in k or "Timeline" in k}
        print("OPS:", json.dumps(keep, ensure_ascii=False))

    captured = await get_session().run(_capture_detail_url)
    if not captured:
        print("NO_CAPTURE：页面没发 TweetDetail 请求（改版？换条稳定推文再试）")
        return
    parts = urllib.parse.urlsplit(captured)
    qs = urllib.parse.parse_qs(parts.query)
    qid = parts.path.rsplit("/", 2)[-2]  # /i/api/graphql/{queryId}/TweetDetail
    variables = json.loads(qs["variables"][0])
    features = json.loads(qs.get("features", ["{}"])[0])
    # focalTweetId/cursor 是每次调用按推文拼的（fetch.py 的 _fetch_conversation），
    # 打印时剔除防照抄进常量（会把所有详情都锁死到探针推文）
    printable = {k: v for k, v in variables.items() if k not in ("focalTweetId", "cursor")}
    print("CAPTURED_QUERY_ID =", json.dumps(qid))
    print("CAPTURED_VARIABLES =", json.dumps(printable, ensure_ascii=False, indent=2))
    print("CAPTURED_FEATURES =", json.dumps(features, ensure_ascii=False, indent=2))

    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": session.ua}
    ) as client:
        r = await client.get(captured, headers=session.headers())
    print("REPLAY_STATUS", r.status_code)
    if r.status_code != 200:
        print("ERRORS", r.text[:400])


async def main() -> None:
    try:
        await _probe()
    finally:
        # 任何退出路径（含异常/超时）都要关浏览器：残留的持久 Chrome 会锁住 profile
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
