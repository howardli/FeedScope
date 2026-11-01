"""跨源共享常量与工厂：改这里即改全部源。

站点特有调参（滚动步长、选择器、httpx 之外的取数细节）仍留在各源文件--
只有「多源复制粘贴且必须同步变」的值才收进来。
"""

from __future__ import annotations

import html2text

# 桌面 UA 与请求超时：core/http 共享客户端的默认头，X 显式传头时也用同一份
DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HTTP_TIMEOUT = 20  # 秒

# Playwright 页面导航超时（毫秒）
PAGE_TIMEOUT_MS = 30_000


def new_h2t() -> html2text.HTML2Text:
    """HTML2Text 统一配置：不硬换行（body_width 会把中文撕碎）；img/video 在进它
    之前已换成记号（见各源 _md），ignore_images 兜底丢掉漏网残片。
    """
    h2t = html2text.HTML2Text()
    h2t.body_width = 0
    h2t.ignore_images = True
    return h2t


def chrome_launch_kwargs() -> dict:
    """Chrome persistent context 公共启动参数（channel 固定本机 Chrome，无开关）。

    browser.py（TUI 内无头）与 login.py（手动登录有头）共用；headless 由调用方给。
    """
    return {
        "channel": "chrome",
        "viewport": {"width": 1280, "height": 900},  # 桌面布局，避免命中移动端 DOM
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "args": ["--disable-blink-features=AutomationControlled"],
    }
