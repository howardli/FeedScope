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

        # F2 → 选中 rss 行（第四行：知乎、X、HN、RSS）→ → 进订阅管理
        await pilot.press("f2")
        assert await _settle(pilot, lambda: isinstance(app.screen, ManageSourcesScreen))
        await pilot.press("down", "down", "down")
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
