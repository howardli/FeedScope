"""源能力声明（ClassVar 词汇表）的基线测试：UI 与 login 只按声明分派。"""

import feedscope.sources  # noqa: F401  import 即注册
from feedscope.core.registry import all_sources


def test_login_capability_declared() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    # 需要登录的源：知乎（扫码）、X（账号密码）、Reddit（账号密码 + 2FA）
    for sid in ("zhihu", "x", "reddit"):
        assert by_id[sid].login_url and by_id[sid].login_cookie


def test_logout_cookies_declared() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    # 登出要清掉判登 cookie：知乎是 z_c0，X 是 auth_token+ct0 成对；
    # Reddit 两枚都清（token_v2 是部分账号的迁移形态）
    assert by_id["zhihu"].logout_cookies == ["z_c0"]
    assert by_id["x"].logout_cookies == ["auth_token", "ct0"]
    assert by_id["reddit"].logout_cookies == ["reddit_session", "token_v2"]


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
    assert by_id["reddit"].detail_unit_label == "评论"
    assert by_id["hn"].detail_unit_label == "评论"
    assert by_id["v2ex"].detail_unit_label == "回复"
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
    # 评论层（DetailLoader.open_comments，详情屏 Enter）知乎/HN/Reddit 有：
    # X/V2EX 的详情到回复为止、RSS/Telegram 全文无楼层——UI 按此声明决定是否预告 Enter
    assert by_id["zhihu"].supports_comments
    assert by_id["hn"].supports_comments
    assert by_id["reddit"].supports_comments
    assert not by_id["x"].supports_comments
    assert not by_id["v2ex"].supports_comments
    assert not by_id["rss"].supports_comments
    assert not by_id["telegram"].supports_comments


def test_reddit_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    r = by_id["reddit"]
    # 测试配置零订阅：内置流兜底（与 RSS 的纯订阅合成不同）
    assert r.feeds == ["home", "popular"]
    assert r.feed_labels == {"home": "首页", "popular": "热门"}
    assert r.supports_detail and r.detail_kinds == frozenset({"link", "self"})  # 全部条目进终端详情
    assert r.subscribable  # [reddit.feeds] 订阅表：F2 里 → 进订阅管理
    assert r.login_url == "https://www.reddit.com/login"
    assert r.login_cookie == "token_v2"  # 显示用兜底；真实判据是 login_verify 的 JWT 解码
    assert r.login_verify is not None  # cookie 出现不算数（reddit_session/token_v2 匿名流量也发）
    # HN 式三层词汇：楼层叫「评论」（顶层讨论串）、楼层内叫「回复」
    assert r.detail_unit_label == "评论" and r.comment_unit_label == "回复"


def test_hn_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    h = by_id["hn"]
    assert h.feeds == ["front", "new", "ask", "show"]
    assert h.feed_labels == {"front": "首页", "new": "最新", "ask": "提问", "show": "展示"}
    assert h.supports_detail
    assert h.detail_kinds == frozenset({"story", "ask", "show"})  # 全部条目进终端详情
    assert not h.login_url and not h.logout_cookies  # 无登录生态（公开索引 API）
    assert not h.subscribable  # 内置流，非用户订阅
    # 知乎式三层词汇：楼层叫「评论」（顶层讨论串）、楼层内叫「回复」
    assert h.detail_unit_label == "评论"
    assert h.comment_unit_label == "回复"


def test_v2ex_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    v = by_id["v2ex"]
    assert v.feeds == ["hot", "new"]
    assert v.feed_labels == {"hot": "热门", "new": "最新"}
    assert v.supports_detail
    assert v.detail_kinds == frozenset({"topic"})  # 全部条目进终端详情
    assert not v.login_url and not v.logout_cookies  # 无登录生态（v1 公开 API）
    assert not v.subscribable  # 内置流，非用户订阅
    # X 式两层词汇：楼层叫「回复」；回复平铺无嵌套，无评论层
    assert v.detail_unit_label == "回复"


def test_telegram_source_declaration() -> None:
    by_id = {s.source_id: s for s in all_sources()}
    t = by_id["telegram"]
    assert t.feeds == []  # 测试配置零订阅：feeds 完全来自用户配置（同 rss）
    assert not t.feed_labels
    assert t.supports_detail and t.detail_kinds == frozenset({""})  # 全部条目进终端详情
    assert t.subscribable  # [telegram.feeds] 订阅表：F2 里 → 进订阅管理
    assert not t.login_url and not t.logout_cookies  # 免登录（公开预览页）
    assert t.detail_unit_label == "正文"  # 无楼层（同 rss）


def test_registry_order_is_tab_order() -> None:
    # 注册顺序 = Tab 切换顺序 = 启动默认源（知乎在前，登录源聚堆，订阅型源殿后）
    assert [s.source_id for s in all_sources()] == [
        "zhihu", "x", "reddit", "hn", "v2ex", "rss", "telegram",
    ]


def test_subscribable_sources_have_store() -> None:
    """subscribable 声明与 subscriptions._STORES 必须一一对应：
    漏登记 = F2 里按 → 直接 ValueError；多登记 = 永远进不去的死入口。"""
    from feedscope.subscriptions import _STORES

    declared = {s.source_id for s in all_sources() if s.subscribable}
    assert set(_STORES) == declared


def test_reserved_feed_keys_declared() -> None:
    """订阅 key 的保留字从源声明读（subscriptions._reserved 的单一出处）：
    聚合流名（SubscribedSource）与内置流名（reddit）——源新增流后忘了
    同步声明，订阅 key 就能遮住它，这条断言负责抓住漂移。"""
    by_id = {s.source_id: type(s) for s in all_sources()}
    assert by_id["rss"].reserved_feed_keys == ("all",)
    assert by_id["telegram"].reserved_feed_keys == ("all",)
    assert set(by_id["reddit"].reserved_feed_keys) == {"home", "popular"}
    for sid in ("zhihu", "x", "hn", "v2ex"):  # 非 subscribable：没有订阅入口，无保留字
        assert by_id[sid].reserved_feed_keys == ()
