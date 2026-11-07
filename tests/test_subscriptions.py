"""订阅管理（feedscope rss 子命令 / subscriptions 模块）基线测试。

写路径全部走隔离配置（conftest 已指 FEEDSCOPE_CONFIG 到临时文件）；add 的
取标题走 MockTransport（补丁 core/http 模块池，同 test_http.py）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import use_config
from test_rss_parse import RSS_XML

from feedscope import subscriptions as subs
from feedscope.cli import main
from feedscope.core import config, http
from feedscope.core.source import SourceError


def _mock_title_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """add 取标题的请求：one.test 回 RSS_XML（标题「测试源」），其余 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "one.test":
            return httpx.Response(200, text=RSS_XML, request=request)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# ---- key 生成 ----


def test_derive_key_from_domain() -> None:
    assert subs.derive_key("https://www.ruanyifeng.com/blog/atom.xml", []) == "ruanyifeng-com"
    assert subs.derive_key("https://hnrss.org/frontpage", []) == "hnrss-org"
    assert subs.derive_key("https://user@hnrss.org:8443/f", []) == "hnrss-org"  # userinfo/端口剥掉


def test_derive_key_collision_numbered() -> None:
    assert subs.derive_key("https://a.test/f", ["a-test"]) == "a-test-2"
    assert subs.derive_key("https://a.test/f", ["a-test", "a-test-2"]) == "a-test-3"


def test_add_feed_derived_key_avoids_reserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """派生 key 不撞聚合流保留字 all：all.com 派生 all-2 而不是 all——
    撞名订阅会被读取侧静默跳过，等于加了不生效（回归：派生路径曾绕过检查）。"""
    use_config(monkeypatch, tmp_path, "")
    _mock_title_fetch(monkeypatch)
    feed = asyncio.run(subs.add_feed("https://all/atom.xml"))  # 单标签主机清洗后恰为 all
    assert feed.key == "all-2"
    assert [(f.key, f.url) for f in config.rss_feeds()] == [("all-2", "https://all/atom.xml")]


# ---- add / remove ----


def test_add_feed_fetches_title_and_writes_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, "")
    _mock_title_fetch(monkeypatch)
    feed = asyncio.run(subs.add_feed("https://one.test/feed.xml"))

    assert (feed.key, feed.label) == ("one-test", "测试源")  # key 按域名、label 用 feed 标题
    assert [(f.key, f.label, f.url) for f in config.rss_feeds()] == [
        ("one-test", "测试源", "https://one.test/feed.xml")
    ]  # 已落盘
    assert config.enabled_ids() is None  # enabled 原本未配置：保持缺省全启用，不擅自写死


def test_add_feed_title_fetch_fails_falls_back_to_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, "")
    _mock_title_fetch(monkeypatch)
    feed = asyncio.run(subs.add_feed("https://dead.test/feed.xml"))  # 404
    assert (feed.key, feed.label) == ("dead-test", "dead-test")  # 取不到标题回 key，不挡加订阅


def test_add_feed_duplicate_url_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, '[rss.feeds]\nold = { url = "https://one.test/feed.xml" }\n')
    with pytest.raises(subs.SubscriptionError, match="已经订阅过"):
        asyncio.run(subs.add_feed("https://one.test/feed.xml"))
    assert len(config.rss_feeds()) == 1  # 配置不动


def test_add_feed_explicit_key_conflicts_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, '[rss.feeds]\nold = { url = "https://a.test/f" }\n')
    _mock_title_fetch(monkeypatch)
    with pytest.raises(subs.SubscriptionError, match="old"):
        asyncio.run(subs.add_feed("https://one.test/f", key="old"))
    with pytest.raises(subs.SubscriptionError, match="保留字"):
        asyncio.run(subs.add_feed("https://one.test/f", key="all"))


def test_update_rss_feeds_syncs_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(
        monkeypatch,
        tmp_path,
        'enabled = ["zhihu", "x"]\n[zhihu]\nhide = ["follow"]\n',
    )
    config.update_rss_feeds([config.Subscription(key="a", label="A", url="https://a.test/f")])
    assert config.enabled_ids() == ["zhihu", "x", "rss"]  # 加订阅 = 想用，自动补启用
    assert config.hidden_feeds("zhihu") == ["follow"]  # 其他源的 hide 原样保留

    config.update_rss_feeds([])  # 删空
    assert config.enabled_ids() == ["zhihu", "x"]  # 空源从启用列表移除
    assert config.rss_feeds() == []


def test_remove_feed_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(
        monkeypatch,
        tmp_path,
        '[rss.feeds]\na = { label = "A", url = "https://a.test/f" }\nb = { url = "https://b.test/f" }\n',
    )
    removed = subs.remove_feed("a")
    assert removed is not None and removed.label == "A"
    assert [f.key for f in config.rss_feeds()] == ["b"]
    assert subs.remove_feed("nope") is None  # 未知 key：不动配置
    assert [f.key for f in config.rss_feeds()] == ["b"]


# ---- OPML ----

_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>订阅</title></head>
  <body>
    <outline text="技术" >
      <outline type="rss" text="阮一峰周刊" xmlUrl="https://r.test/atom.xml" htmlUrl="https://r.test"/>
    </outline>
    <outline type="rss" text="HN" xmlUrl="https://hnrss.org/frontpage"/>
    <outline type="rss" xmlUrl="https://notitle.test/f"/>
    <outline text="无地址目录"/>
  </body>
</opml>"""


def test_parse_opml_flattens_and_takes_label() -> None:
    got = subs.parse_opml(_OPML)
    assert got == [
        ("阮一峰周刊", "https://r.test/atom.xml"),  # 嵌套目录拍平
        ("HN", "https://hnrss.org/frontpage"),
        ("", "https://notitle.test/f"),  # text/title 缺失 → 空 label
    ]  # 无 xmlUrl 的目录节点只当容器


def test_import_opml_merges_and_skips_known(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, '[rss.feeds]\nr-test = { url = "https://r.test/atom.xml" }\n')
    added, skipped = subs.import_opml(_OPML)

    assert [(f.key, f.label) for f in added] == [("hnrss-org", "HN"), ("notitle-test", "notitle-test")]
    assert skipped == ["阮一峰周刊：地址已订阅"]  # 已订阅的跳过
    assert [f.key for f in config.rss_feeds()] == ["r-test", "hnrss-org", "notitle-test"]  # 幂等合并


def test_import_opml_dedups_within_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, "")
    hn = '<outline type="rss" text="HN" xmlUrl="https://hnrss.org/frontpage"/>'
    dup = _OPML.replace(
        hn,
        hn + '\n    <outline type="rss" text="HN-again" xmlUrl="https://hnrss.org/frontpage"/>',
    )
    added, skipped = subs.import_opml(dup)
    assert len([f for f in added if f.url == "https://hnrss.org/frontpage"]) == 1
    assert any("HN-again" in s for s in skipped)  # 文件内重复地址只进一次


def test_import_opml_avoids_reserved_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """OPML 派生 key 同样避开保留字 all（回归：导入路径曾绕过检查）。"""
    use_config(monkeypatch, tmp_path, "")
    opml = '<opml version="2.0"><body><outline type="rss" text="A" xmlUrl="https://all/a.xml"/></body></opml>'
    added, _skipped = subs.import_opml(opml)
    assert [f.key for f in added] == ["all-2"]


def test_export_import_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(
        monkeypatch,
        tmp_path,
        '[rss.feeds]\n'
        'a = { label = "阮一峰周刊 & 博客", url = "https://r.test/atom.xml" }\n'
        'b = { label = "HN", url = "https://hnrss.org/frontpage" }\n',
    )
    opml = subs.export_opml(config.rss_feeds())
    assert 'text="阮一峰周刊 &amp; 博客"' in opml  # 特殊字符转义合法

    use_config(monkeypatch, tmp_path, "")  # 空配置再导回来
    added, _skipped = subs.import_opml(opml)
    assert {(f.key, f.label, f.url) for f in added} == {
        ("r-test", "阮一峰周刊 & 博客", "https://r.test/atom.xml"),
        ("hnrss-org", "HN", "https://hnrss.org/frontpage"),
    }


# ---- cli 冒烟：argparse 接线 + 输出/退出码 ----


def test_cli_rss_list_add_remove_export(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    use_config(monkeypatch, tmp_path, 'language = "zh-CN"\n')  # cli.main 按配置设语言：断言中文
    _mock_title_fetch(monkeypatch)

    assert main(["rss", "list"]) == 0
    assert "还没有订阅" in capsys.readouterr().out

    assert main(["rss", "add", "https://one.test/feed.xml"]) == 0
    assert "已订阅 one-test：测试源" in capsys.readouterr().out

    assert main(["rss", "list"]) == 0
    out = capsys.readouterr().out
    assert "one-test" in out and "共 1 条订阅" in out

    assert main(["rss", "remove", "one-test"]) == 0
    assert "已删除 one-test" in capsys.readouterr().out
    assert main(["rss", "remove", "one-test"]) == 1  # 再删报错
    assert "没有 key" in capsys.readouterr().out

    main(["rss", "add", "https://one.test/feed.xml"])
    capsys.readouterr()
    out = tmp_path / "sub.opml"
    assert main(["rss", "export", str(out)]) == 0
    assert "one.test/feed.xml" in out.read_text(encoding="utf-8")


def test_cli_rss_export_stdout(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    use_config(monkeypatch, tmp_path, '[rss.feeds]\na = { url = "https://a.test/f" }\n')
    assert main(["rss", "export"]) == 0
    assert "xmlUrl=" in capsys.readouterr().out  # 缺省打 stdout，可重定向


def test_cli_rss_import_file(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    use_config(monkeypatch, tmp_path, 'language = "zh-CN"\n')
    opml_file = tmp_path / "subs.opml"
    opml_file.write_text(_OPML, encoding="utf-8")

    assert main(["rss", "import", str(opml_file)]) == 0
    out = capsys.readouterr().out
    assert "导入 3 条" in out
    assert [f.key for f in config.rss_feeds()] == ["r-test", "hnrss-org", "notitle-test"]

    bad = tmp_path / "bad.opml"
    bad.write_text("<not-opml>", encoding="utf-8")
    assert main(["rss", "import", str(bad)]) == 2  # 坏 XML：退出码 2
    assert main(["rss", "import", str(tmp_path / "absent.opml")]) == 2  # 文件不存在


# ---- 自动发现 / 改名 / 添加入口分流（订阅管理屏与 CLI 共用）----

_HOME_HTML = (
    "<html><head>"
    '<link rel="alternate" type="application/rss+xml" href="feed.xml" title="blog"/>'
    "</head><body>只是个首页</body></html>"
)


def test_add_feed_autodiscovers_from_homepage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """粘博客首页：HTML 里的 <link rel=alternate> 自动发现 feed，改订真地址。"""
    use_config(monkeypatch, tmp_path, "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=_HOME_HTML, request=request)
        return httpx.Response(200, text=RSS_XML, request=request)  # 发现的地址是真 feed

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    feed = asyncio.run(subs.add_feed("https://blog.test/"))
    assert feed.url == "https://blog.test/feed.xml"  # 订的是发现出来的地址
    assert feed.key == "blog-test"  # key 沿用首页域名生成的那个
    assert feed.label == "测试源"  # 显示名取自真 feed 标题


def test_add_feed_plain_page_without_link_still_adds(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """页面不是 feed 也没有声明链接：尊重用户给的地址照常添加（显示名回 key）。"""
    use_config(monkeypatch, tmp_path, "")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>普通网页</body></html>", request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    feed = asyncio.run(subs.add_feed("https://plain.test/"))
    assert (feed.url, feed.label) == ("https://plain.test/", "plain-test")


def test_rename_feed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, '[rss.feeds]\na = { label = "旧名", url = "https://a.test/f" }\n')
    renamed = subs.rename_feed("a", "  新名  ")  # 顺手 strip
    assert renamed is not None and renamed.label == "新名"
    assert config.rss_feeds()[0].label == "新名"
    assert subs.rename_feed("a", "   ") is None  # 空名不动配置
    assert subs.rename_feed("nope", "x") is None  # 未知 key 不动配置
    assert config.rss_feeds()[0].label == "新名"


def test_add_from_input_dispatches_url_and_opml(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    use_config(monkeypatch, tmp_path, "")
    _mock_title_fetch(monkeypatch)

    added, summary = asyncio.run(subs.add_from_input("  https://one.test/feed.xml  "))
    assert len(added) == 1 and "已订阅 one-test" in summary

    opml = tmp_path / "s.opml"
    opml.write_text(_OPML, encoding="utf-8")
    added, summary = asyncio.run(subs.add_from_input(str(opml)))
    assert len(added) == 3 and "导入 3 条" in summary
    assert [f.key for f in config.rss_feeds()] == [
        "one-test",  # URL 先加的
        "r-test", "hnrss-org", "notitle-test",  # OPML 全新 3 条
    ]


def test_add_from_input_bad_input_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    use_config(monkeypatch, tmp_path, "")
    for text in ("", "   ", str(tmp_path / "absent.opml")):
        with pytest.raises(subs.SubscriptionError):
            asyncio.run(subs.add_from_input(text))
    bad = tmp_path / "bad.opml"
    bad.write_text("<broken", encoding="utf-8")
    with pytest.raises(subs.SubscriptionError, match="OPML"):
        asyncio.run(subs.add_from_input(str(bad)))


# ---- OPML 文件出口统一（read_opml_file / BadSubscriptionFile）----


def test_read_opml_file_missing_raises_bad_file(tmp_path) -> None:
    with pytest.raises(subs.BadSubscriptionFile, match="读不了"):
        subs.read_opml_file(tmp_path / "absent.opml")


def test_import_opml_bad_xml_raises_bad_file() -> None:
    with pytest.raises(subs.BadSubscriptionFile, match="不是合法的 OPML"):
        subs.import_opml("<not-opml")


def test_add_from_input_bad_opml_path_is_usage_error(tmp_path) -> None:
    """坏 OPML 是用法级错误（CLI 退 2），不是业务冲突（退 1）——与 import 同规则。"""
    with pytest.raises(subs.BadSubscriptionFile):
        asyncio.run(subs.add_from_input(str(tmp_path / "absent.opml")))


def test_cli_opml_exit_codes_consistent(tmp_path, capsys) -> None:
    """同一坏文件：add 与 import 的退出码一致（都按用法级错误退 2）。"""
    bad = tmp_path / "bad.opml"
    bad.write_text("<not-opml", encoding="utf-8")
    assert main(["rss", "add", str(bad)]) == 2
    assert main(["rss", "import", str(bad)]) == 2
    out = capsys.readouterr().out
    assert "不是合法的 OPML" in out


# ---- Telegram 频道添加：输入归一 / 保留字 / 预览页校验 / 重复 / 删改 ----


def _mock_channel_html(monkeypatch: pytest.MonkeyPatch, html: str) -> None:
    """替身 t.me/s 预览页（add_channel 按需导入 fetch_channel_html，patch 模块属性即生效）。"""

    async def fake(name: str, before: int | None = None) -> str:
        return html

    monkeypatch.setattr("feedscope.sources.telegram.fetch.fetch_channel_html", fake)


def test_add_channel_rejects_bad_input(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """认不出：不动配置直接拒。

    撞保留字（all）的拒收分支对当前保留字集不可达——Telegram 用户名最短
    5 字符、all 只有 3 字符，先在输入归一就被拦；那是防保留字集日后扩展
    的防御分支（见 subscriptions.add_channel 注释），不为它伪造可达性。"""
    use_config(monkeypatch, tmp_path, "")
    with pytest.raises(subs.SubscriptionError, match="没认出"):
        asyncio.run(subs.add_channel("!!!不是频道!!!"))
    assert config.feeds_for("telegram") == []


def test_add_channel_rejects_silent_preview(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """预览页一条消息都没有（不存在/为空/关了 Web 预览）：给专属指引而不是误报改版。"""
    use_config(monkeypatch, tmp_path, "")
    _mock_channel_html(monkeypatch, "<html><body></body></html>")
    with pytest.raises(subs.SubscriptionError, match="拉不到任何帖子"):
        asyncio.run(subs.add_channel("durov"))


def test_add_channel_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """正常闭环：帖子直链形态归一成频道名、显示名取预览页 og:title、重复拒收、删改就位。"""
    from test_telegram_parse import HTML

    use_config(monkeypatch, tmp_path, "")
    _mock_channel_html(monkeypatch, HTML)
    feed = asyncio.run(subs.add_channel("https://t.me/durov/531"))  # 帖子直链形态
    assert (feed.key, feed.label, feed.url) == ("durov", "Pavel Durov", "https://t.me/durov/")
    with pytest.raises(subs.SubscriptionError, match="已经订阅过"):
        asyncio.run(subs.add_channel("@durov"))
    renamed = subs.rename_channel("durov", "杜频道")
    assert renamed is not None and renamed.label == "杜频道"
    removed = subs.remove_channel("durov")
    assert removed is not None and removed.key == "durov"
    assert config.feeds_for("telegram") == []
    assert subs.rename_channel("durov", "x") is None  # 已删：None 不炸


# ---- Reddit subreddit 添加的拒收分支（页面校验路径另见 test_subs_screen 的 reddit 流）----


def test_add_subreddit_rejects_bad_input(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """认不出 / 撞内置流名（home/popular 真实存在，撞名会遮住内置流）。"""
    use_config(monkeypatch, tmp_path, "")
    with pytest.raises(subs.SubscriptionError, match="没认出"):
        asyncio.run(subs.add_subreddit("!!!"))
    with pytest.raises(subs.SubscriptionError, match="内置流重名"):
        asyncio.run(subs.add_subreddit("home"))
    assert config.feeds_for("reddit") == []


def test_add_subreddit_wraps_fetch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """页面校验失败（不存在/未登录）：消息带「加不上」指引，不动配置。"""
    use_config(monkeypatch, tmp_path, "")

    async def ok_cookies(url: str) -> dict[str, str]:
        return {}  # 判登门过（profile_cookies 替身；fetch 侧再抛业务错）

    async def broken(name: str) -> str:
        raise SourceError("Reddit r/ghost 请求失败（HTTP 404）")

    monkeypatch.setattr("feedscope.core.browser.profile_cookies", ok_cookies)
    monkeypatch.setattr("feedscope.sources.reddit.fetch.fetch_subreddit", broken)
    with pytest.raises(subs.SubscriptionError, match="加不上"):
        asyncio.run(subs.add_subreddit("ghost"))
    assert config.feeds_for("reddit") == []


def test_store_for_unknown_id_fail_fast() -> None:
    """未登记的源要 store：subscribable 与 SubsStore 缺一不可，尽早炸。"""
    with pytest.raises(ValueError, match="没有订阅管理适配器"):
        subs.store_for("zhihu")
