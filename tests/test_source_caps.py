"""源能力声明（ClassVar 词汇表）的基线测试：UI 与 login 只按声明分派。"""

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core.registry import all_sources


def test_login_capability_declared() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    # 需要登录的源：知乎（扫码）、X（账号密码）
    for sid in ("zhihu", "x"):
        assert by_id[sid].login_url and by_id[sid].login_cookie


def test_logout_cookies_declared() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    # 登出要清掉判登 cookie：知乎是 z_c0，X 是 auth_token+ct0 成对
    assert by_id["zhihu"].logout_cookies == ["z_c0"]
    assert by_id["x"].logout_cookies == ["auth_token", "ct0"]


def test_zhihu_feed_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    z = by_id["zhihu"]
    assert z.feeds == ["hot", "recommend", "follow"]
    assert z.feed_labels == {"hot": "热榜", "recommend": "推荐", "follow": "关注"}
    assert z.supports_detail  # Enter 进终端详情屏（回答）
    assert z.detail_kinds == frozenset({"", "question"})


def test_x_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    x = by_id["x"]
    assert x.feeds == ["foryou", "following"]  # 与 X 主页页签同序：默认进「推荐」
    assert x.feed_labels == {"foryou": "推荐", "following": "正在关注"}
    assert x.supports_detail  # Enter 进终端详情屏（正文 + 回复）
    assert x.detail_kinds == frozenset({"tweet"})
    assert not x.kind_labels  # 无类型标签：元信息列直接显示作者


def test_detail_unit_label_per_source() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    assert by_id["zhihu"].detail_unit_label == "回答"
    assert by_id["x"].detail_unit_label == "回复"
    assert by_id["rss"].detail_unit_label == "正文"


def test_rss_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    r = by_id["rss"]
    assert r.feeds == []  # 测试配置零订阅：feeds 完全来自用户配置（见 test_rss_source）
    assert not r.feed_labels
    assert r.supports_detail and r.detail_kinds == frozenset({""})  # 全部条目进终端详情
    assert not r.login_url and not r.logout_cookies  # 无登录生态


def test_comments_capability_declared() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    # 评论层（DetailLoader.open_comments，详情屏 Enter）只有知乎有：
    # X 的详情到回复为止、RSS 全文无楼层——UI 按此声明决定是否预告 Enter
    assert by_id["zhihu"].supports_comments
    assert not by_id["x"].supports_comments
    assert not by_id["rss"].supports_comments


def test_registry_order_is_tab_order() -> None:
    # 注册顺序 = Tab 切换顺序 = 启动默认源（知乎在前）
    assert [s.source_id for s in all_sources()] == ["zhihu", "x", "rss"]
