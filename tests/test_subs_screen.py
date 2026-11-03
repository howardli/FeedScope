"""订阅管理屏（F2 → →）全流程：TUI 内增删改订阅 + 勾选同步 + 保存生效。

用真实注册表（zhihu/x/rss）+ 隔离配置 + MockTransport；从「零订阅的 rss」
起步，覆盖鸡生蛋路径：空订阅的 rss 在 F2 列表可见（subscribable 放行），
第一条订阅在屏内添加。入口键是 → 不是 Enter——SelectionList 把 Enter 截停
转成勾选切换，屏级绑定压不过（见 source_setup.py 注释）。
"""

from __future__ import annotations

import asyncio

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
    return len(screen.query_one("#subs-list", ListView).children) if isinstance(
        screen, SubscriptionsScreen
    ) else -1


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
    """零订阅起步 + about.json 走 Mock + 假浏览器会话（判登不弹真 Chrome）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r/rust/about.json":
            return httpx.Response(200, json={"kind": "t5", "data": {"display_name": "rust"}}, request=request)
        return httpx.Response(404, text="gone", request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = tmp_path / "sources.toml"
    cfg.write_text("enabled = []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDSCOPE_CONFIG", str(cfg))

    from feedscope.sources import reddit as reddit_mod

    class _FakeBrowser:
        ua = "Mozilla/5.0 Chrome/131.0.0.0"

    async def fake_cookies(url: str) -> dict[str, str]:
        return {"reddit_session": "s", "token_v2": "t"}

    monkeypatch.setattr(reddit_mod, "profile_cookies", fake_cookies)
    monkeypatch.setattr(reddit_mod, "get_session", lambda: _FakeBrowser())
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
        assert any("还没有订阅" in str(n.message) for n in notes), "给下一步指引，而不是看起来没生效"


def test_empty_subscribable_hint_on_save(rss_env, monkeypatch) -> None:
    asyncio.run(_run_empty_telegram_flow(rss_env, monkeypatch))
