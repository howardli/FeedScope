"""订阅管理屏（F2 → →）全流程：TUI 内增删改订阅 + 勾选同步 + 保存生效。

用真实注册表（zhihu/x/rss）+ 隔离配置 + MockTransport；从「零订阅的 rss」
起步，覆盖鸡生蛋路径：空订阅的 rss 在 F2 列表可见（subscribable 放行），
第一条订阅在屏内添加。入口键是 → 不是 Enter——SelectionList 把 Enter 截停
转成勾选切换，屏级绑定压不过（见 source_setup.py 注释）。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_rss_parse import RSS_XML
from test_tui_smoke import _settle
from textual.widgets import Input, ListView, SelectionList

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core import config, http
from feedscope.ui.app import FeedScopeApp
from feedscope.ui.screens.source_setup import ManageSourcesScreen
from feedscope.ui.screens.subscriptions import ConfirmModal, InputScreen, SubscriptionsScreen


@pytest.fixture()
def rss_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """零订阅起步 + enabled 空（空态主屏）+ 全部请求走 Mock。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".xml"):  # 任意 feed 地址都回样例 RSS
            return httpx.Response(200, text=RSS_XML, request=request)
        return httpx.Response(200, text="<html><body>普通页面</body></html>", request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = tmp_path / "sources.toml"
    cfg.write_text("enabled = []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))
    return cfg


def _rows(app: FeedScopeApp) -> int:
    screen = app.screen
    return (
        len(screen.query_one("#subs-list", ListView).children)
        if isinstance(screen, SubscriptionsScreen)
        else -1
    )


async def _run_flow(cfg, monkeypatch) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        assert await _settle(pilot, lambda: app.query_one("#empty-hint").display)

        # F2 → 选中 rss 行（第六行：知乎、X、Reddit、HN、V2EX、RSS）→ → 进订阅管理
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("down", "down", "down", "down", "down")
        await pilot.press("right")
        assert await _settle(pilot, lambda: isinstance(app.screen, SubscriptionsScreen))
        assert _rows(app) == 0, "零订阅起步"

        # Insert 添加：输入框贴地址（终端粘贴在真机走 bracketed paste，测试直设值）
        await pilot.press("insert")
        assert await _settle(pilot, lambda: isinstance(app.screen, InputScreen))
        app.screen.query_one(Input).value = "https://one.test/feed.xml"
        await pilot.press("enter")
        assert await _settle(pilot, lambda: _rows(app) == 1), "添加后列表 1 行"
        assert [f.url for f in config.rss_feeds()] == ["https://one.test/feed.xml"]

        # Enter 改名（ListView 的 Selected 路径，与主列表同源）
        await pilot.press("enter")
        assert await _settle(pilot, lambda: isinstance(app.screen, InputScreen))
        assert app.screen.query_one(Input).value == "测试源"  # 预填当前显示名
        app.screen.query_one(Input).value = "我的新名字"
        await pilot.press("enter")
        assert await _settle(pilot, lambda: config.rss_feeds()[0].label == "我的新名字")

        # Delete 删除（轻确认）
        await pilot.press("delete")
        assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmModal))
        await pilot.click("#confirm-ok")
        assert await _settle(pilot, lambda: _rows(app) == 0)
        assert config.rss_feeds() == []

        # 再加回来一条，Esc 退出订阅屏：F2 里 rss 应被自动勾上（加订阅 = 想用）
        await pilot.press("insert")
        app.screen.query_one(Input).value = "https://two.test/feed.xml"
        await pilot.press("enter")
        assert await _settle(pilot, lambda: _rows(app) == 1)
        await pilot.press("escape")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        sl = app.screen.query_one(SelectionList)
        assert "rss" in sl.selected, "订阅管理返回后 rss 自动勾选"
        assert app.screen.selected_ids == ["rss"]

        # 保存：rss 进 tab，侧栏出现「全部」+ 订阅流，列表加载出内容
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: [s.source_id for s in app.sources] == ["rss"])
        # 聚合视图不给无地址条目兜底地址：样例 RSS 里 guid/link 双缺的第三条被跳过
        assert await _settle(pilot, lambda: len(app.items) == 2), "刷新拉到订阅内容"
        nav_text = " ".join(str(n.content) for n in app.query(".feed-nav-item"))
        assert "全部" in nav_text and "我的新名字" not in nav_text, "侧栏有全部流"


def test_subs_screen_full_flow(rss_env, monkeypatch) -> None:
    asyncio.run(_run_flow(rss_env, monkeypatch))


# ---- Reddit 订阅流：SubsStore 分派 + about 校验 + 落盘 + 勾选同步 ----


@pytest.fixture()
def reddit_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """零订阅起步 + 版块页走假通道页签 + 假判登 cookie（不弹真 Chrome）。"""
    cfg = tmp_path / "sources.toml"
    cfg.write_text("enabled = []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))

    from test_reddit_source import _mock

    from feedscope.sources import reddit as reddit_mod

    async def fake_cookies(url: str) -> dict[str, str]:
        import base64

        def b64(d: bytes) -> str:
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        token = f"{b64(b'{}')}.{b64(json.dumps({'sub': 'user'}).encode())}.sig"
        return {"reddit_session": "s", "token_v2": token}

    def route(req) -> tuple[int | None, str]:
        if req.path == "/r/rust/":
            return None, '<div subreddit-name="rust"></div>'
        return None, "<html>壳页</html>"

    monkeypatch.setattr(reddit_mod, "profile_cookies", fake_cookies)
    _mock(monkeypatch, route)  # 通道页签替身接管取数（get_session/单例/版本全复位）
    return cfg


async def _run_reddit_flow(cfg, monkeypatch) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        assert await _settle(pilot, lambda: app.query_one("#empty-hint").display)

        # F2 → reddit 行（第三行：知乎、X、Reddit）→ → 进订阅管理（store 分派）
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("down", "down")
        await pilot.press("right")
        assert await _settle(pilot, lambda: isinstance(app.screen, SubscriptionsScreen))
        assert _rows(app) == 0, "零订阅起步"

        # Insert 添加：贴站点地址（四形态归一在 parse 层测，这里走 URL 形态）
        await pilot.press("insert")
        assert await _settle(pilot, lambda: isinstance(app.screen, InputScreen))
        assert "subreddit" in app.screen.query_one(Input).placeholder  # 文案按源注入
        app.screen.query_one(Input).value = "https://www.reddit.com/r/rust/"
        await pilot.press("enter")
        assert await _settle(pilot, lambda: _rows(app) == 1), "about 校验通过后落盘"
        from feedscope.core import config as cfg_mod

        assert [f.key for f in cfg_mod.feeds_for("reddit")] == ["rust"]

        # Esc 返回：reddit 本就有内置流（首页/热门），勾选保持
        await pilot.press("escape")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        sl = app.screen.query_one(SelectionList)
        assert "reddit" in sl.selected, "订阅管理返回后 reddit 保持勾选"


def test_subs_screen_reddit_flow(reddit_env, monkeypatch) -> None:
    asyncio.run(_run_reddit_flow(reddit_env, monkeypatch))


# ---- 勾选零订阅的可订阅源：保存成功但空源不进 Tab，必须有下一步指引 ----


async def _run_empty_telegram_flow(cfg, monkeypatch) -> None:
    app = FeedScopeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        assert await _settle(pilot, lambda: app.query_one("#empty-hint").display)

        # F2 → 第七行（Telegram，零订阅）→ 空格勾选 → 保存
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("down", "down", "down", "down", "down", "down")
        await pilot.press("space")
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: not isinstance(app.screen, ManageSourcesScreen))

        from feedscope.core import config as cfg_mod

        assert cfg_mod.enabled_ids() and "telegram" in cfg_mod.enabled_ids()  # 勾选确实落盘
        assert "telegram" not in [s.source_id for s in app.sources]  # 零订阅不进 Tab（既有语义）
        notes = list(getattr(app, "_notifications", []))
        assert any("还没有订阅" in str(n.message) for n in notes), (
            "给下一步指引，而不是看起来没生效"
        )


def test_empty_subscribable_hint_on_save(rss_env, monkeypatch) -> None:
    asyncio.run(_run_empty_telegram_flow(rss_env, monkeypatch))


# ---- 删光订阅后仍勾选保存：停在原源的重建不能崩，空表不能误报改版 ----


async def _run_delete_all_flow(monkeypatch, tmp_path) -> None:
    """当前源停在 Telegram、订阅删光、又在 F2 勾上 Telegram 保存：
    prev_id 在 chosen 里但被 feeds 过滤出局——_rebuild_enabled 的
    「停在原源」按过滤后名单找，不能 StopIteration 崩掉保存链路。"""
    cfg = tmp_path / "sources.toml"
    cfg.write_text(
        'enabled = ["v2ex", "telegram"]\n[telegram.feeds]\ndurov = { url = "https://t.me/durov/" }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>普通页面</body></html>", request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    app = FeedScopeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("tab")  # 切到 Telegram（当前源停在这）
        for _ in range(6):
            await pilot.pause()

        await pilot.press("f2")  # → Telegram 行（第七行）→ → 进订阅管理 → Delete 删光
        await pilot.press("down", "down", "down", "down", "down", "down")
        await pilot.press("right")
        assert await _settle(pilot, lambda: isinstance(app.screen, SubscriptionsScreen))
        await pilot.press("delete")
        assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmModal))
        await pilot.click("#confirm-ok")

        await pilot.press("escape")  # 回 F2；_sync_row 已取消勾选，空格再勾上（复现用户操作）
        await pilot.press("space")
        await pilot.click("#setup-confirm")
        assert await _settle(pilot, lambda: not isinstance(app.screen, ManageSourcesScreen))

        # 保存链路完整走完：worker 不崩（崩溃会让 setup-confirm 后屏幕卡死）、
        # telegram 零订阅不进 Tab、当前源回退到 v2ex
        assert [s.source_id for s in app.sources] == ["v2ex"]
        assert app.current_source.source_id == "v2ex"
        notes = [str(n.message) for n in getattr(app, "_notifications", [])]
        assert any("还没有订阅" in m for m in notes), "给下一步指引"


def test_delete_all_then_keep_checked_rebuilds_without_crash(monkeypatch, tmp_path) -> None:
    asyncio.run(_run_delete_all_flow(monkeypatch, tmp_path))


def test_open_feed_all_with_empty_table_gives_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """空订阅表对「全部」流：给指引，不误报「站点可能已改版」（telegram 与 rss 同修）。"""
    from feedscope.core.source import SourceError
    from feedscope.sources.rss import RssSource
    from feedscope.sources.telegram import TelegramSource

    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(tmp_path / "sources.toml"))
    for src, label in ((TelegramSource(), "Telegram"), (RssSource(), "RSS")):
        assert src.feeds == []  # 零订阅时「全部」本不该出现在 feeds；防的是删光过渡期的旧实例
        src.feeds = [src.ALL_FEED]  # 模拟旧实例残留的流视图
        with pytest.raises(SourceError, match="还没有订阅"):
            asyncio.run(src.open_feed(src.ALL_FEED))
