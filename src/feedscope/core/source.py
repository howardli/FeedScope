"""Source 插件契约：一个网站一个子类，放在 feedscope.sources 下自行注册。

本模块只剩「源怎么声明自己、怎么被打开」：Source / SubscribedSource 两个基类、
首屏 0 条守卫（require_feed_items）与「全部」聚合流的共享编排（merge_feeds）。
加载器与去重在 core.loaders，异常在 core.errors——同族三件各管一摊。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar
from urllib.parse import urlparse

from feedscope.core import browser
from feedscope.core.config import Subscription, feeds_for, hidden_feeds
from feedscope.core.errors import LoginRequired, SourceError
from feedscope.core.i18n import t
from feedscope.core.loaders import (
    DetailLoader,
    FeedLoader,
    SliceFeedLoader,
    StaticDetailLoader,
    StaticFeedLoader,
)
from feedscope.core.models import DetailDoc, FeedItem

logger = logging.getLogger(__name__)


def require_feed_items(items: list[FeedItem], label: str, feed: str = "") -> list[FeedItem]:
    """信息流首屏守卫：取数成功但解析出 0 条 = 大概率站点改版，显式报错而非静默空列表。

    只用于 open_feed 的首批（各源编排层出口统一调用）；load_more 翻页拉空
    （到底/全重复）是正常语义，不套这层。
    """
    if not items:
        raise SourceError(
            t("errors.zero_items_with_feed", label=label, feed=feed)
            if feed
            else t("errors.zero_items", label=label)
        )
    return items


_MERGE_PARALLEL = 8  # 「全部」聚合的并发上限：太猛会被源侧限流（RSS 站点普遍小水管）


async def merge_feeds(
    feeds: Sequence[Any],
    fetch_one: Callable[[Any], Awaitable[list[FeedItem]]],
    *,
    error_label: Callable[[Any], str],
    sort_key: Callable[[FeedItem], Any],
    dedup_urls: bool = False,
) -> tuple[list[FeedItem], list[str]]:
    """「全部」聚合流的共享编排（RSS / Telegram 共用）：并发拉全部订阅、按发布
    时间倒序合并，返回 (合并条目, 失败订阅的短消息列表)。

    - 单订阅失败隔离：一个死源只损失自己的条目（宽捕获——取数/解析的任何
      失败都转短消息上交），全部失败由调用方决定报错
    - 倒序合并用稳定排序：缺时间的条目沉底且保持订阅书写顺序
    - dedup_urls 剔除跨订阅的重复 URL（转载/聚合源常见），保留书写顺序靠前
      的订阅版本；按次注入的差异（怎么拉、怎么排、错误标签叫什么）走参数
    - sort_key 取各源 extra["published"] 的原生形态（RSS float epoch /
      Telegram ISO 串）——键类型源内一致即可，不跨源比较（跨源聚合不在功能面）
    - 一次性快照：与各单订阅流的增量 Loader 相对（聚合流不做翻页）
    """
    sem = asyncio.Semaphore(_MERGE_PARALLEL)

    async def one(feed: Any) -> tuple[list[FeedItem], str | None]:
        async with sem:
            try:
                return await fetch_one(feed), None
            except Exception as exc:  # 单订阅失败隔离：错误上交，不拖垮聚合
                return [], t("errors.merge_one_failed", label=error_label(feed), error=exc)

    pairs = await asyncio.gather(*(one(f) for f in feeds))
    errors = [err for _, err in pairs if err]
    merged: list[FeedItem] = []
    seen: set[str] = set()
    for items, _ in pairs:
        for it in items:
            if dedup_urls and it.url:
                if it.url in seen:
                    continue
                seen.add(it.url)
            merged.append(it)
    merged.sort(key=sort_key, reverse=True)
    return merged, errors


class Source(ABC):
    """源基类。子类用 @register 注册，由 TUI 装载。

    一个源可以暴露多个命名信息流（如知乎的热榜/推荐/关注），
    feeds 的顺序就是左侧边栏的切流顺序。类里写死的是内置默认流；用户可在
    sources.toml 隐藏内置流（见 core/config.py），__init__ 合成实例视图。

    类型词汇表归源所有，UI 不认识任何具体类型：
    - kind_labels：kind -> 元信息列标签（如 video -> "视频"），预告 Enter 跳浏览器；
    - detail_kinds：Enter 进终端详情屏的 kind，其余一律跳浏览器。
    """

    source_id: ClassVar[str]  # 注册键，如 "zhihu"
    # 展示名：文案键（如知乎 "sources.zhihu.label"，经 label_text 解析）或
    # ASCII 字面量（HN / Reddit / X / V2EX / RSS / Telegram——专有名词两种
    # 语言同形，键化六个永不翻译的键是反优雅；t() 对无点字符串直通兜住）
    label: ClassVar[str]
    # feeds/feed_labels 两形同用（故不作 ClassVar）：类体写内置默认流，
    # __init__ 按配置合成实例视图（hide 剔减 / 订阅拼加），分派读实例
    feeds: list[str] = []  # 命名信息流；空 = 无可选流（不进 Tab）
    # 流名 -> 显示名的文案键（订阅型源的订阅行是用户数据不是键，t() 对
    # 无点字符串直通），展示统一经 feed_label() 解析
    feed_labels: dict[str, str] = {}
    supports_detail: ClassVar[bool] = False  # 决定 Enter 是否可能进详情屏
    supports_comments: ClassVar[bool] = False  # 决定详情屏 Enter 是否可能进评论层
    subscribable: ClassVar[bool] = False  # 信息流来自用户订阅：F2 里 → 进订阅管理屏
    kind_labels: ClassVar[dict[str, str]] = {}  # kind -> 元信息列标签的文案键（共享 kinds.* 词表）
    detail_kinds: ClassVar[frozenset[str]] = frozenset({""})
    # 详情屏楼层词键（X/V2EX 回复、HN/Reddit 评论）；楼层内评论子区的单位词键
    detail_unit_label: ClassVar[str] = "units.answer"
    comment_unit_label: ClassVar[str] = "units.comment"
    login_url: ClassVar[str] = ""  # 有登录需求的源声明登录页；空 = 无需登录
    login_cookie: ClassVar[str] = ""  # 判登 cookie 的兜底单枚（未声明 logout_cookies 时用）
    logout_cookies: ClassVar[list[str]] = (
        []  # 判登/登出 cookie 全集；空 = 只认 login_cookie
    )
    reserved_feed_keys: ClassVar[tuple[str, ...]] = (
        ()  # 订阅 key 的保留字（内置流名/聚合流名）：添加入口拒收撞名订阅的唯一依据
    )

    def __init__(self) -> None:
        """按用户配置合成实例视图：feeds 在类默认之上剔除被隐藏的流。

        类属性 = 内置默认（ClassVar 语义即「默认值」）；实例属性 = 合成后的
        当前视图，UI / open_feed 分派只读实例。
        """
        hidden = hidden_feeds(self.source_id)
        feeds = [f for f in self.feeds if f not in hidden]
        self.feeds = feeds
        if not feeds:
            logger.warning("%s 的信息流已被配置全部隐藏", self.label_text)

    # ---- 词汇键 -> 展示文本（i18n；t() 对无点字符串直通，stub 源不受影响）----

    @property
    def label_text(self) -> str:
        """源展示名（label 是文案键，这里给解析后的文本；日志/消息/展示用）。"""
        return t(self.label)

    @property
    def detail_unit(self) -> str:
        """详情屏楼层词（如 回答/评论/回复）的展示文本。"""
        return t(self.detail_unit_label)

    @property
    def comment_unit(self) -> str:
        """楼层内评论子区单位词的展示文本。"""
        return t(self.comment_unit_label)

    def feed_label(self, name: str) -> str:
        """流名 -> 显示名（内置流查词汇键解析；未知流回退流名本身）。"""
        return t(self.feed_labels.get(name, name))

    @property
    def judging_cookies(self) -> list[str]:
        """判登 / 登出要看的 cookie 名全集：logout_cookies 优先，未声明才退 login_cookie 单枚。

        logged_in 的默认判据、logout 的清除名单共用（口径全项目唯一），不给
        「login 报成功、账号屏仍显示未登录」的分裂留缝。
        """
        if self.logout_cookies:
            return list(self.logout_cookies)
        return [self.login_cookie] if self.login_cookie else []

    # ---- 登录：判登谓词与判登门（判据只此一份，三条路径共用）----

    @property
    def login_origin(self) -> str:
        """登录域的源（scheme + 主机，如 https://x.com）：读 profile cookie 时按它圈定域。"""
        return f"https://{urlparse(self.login_url).netloc}"

    def logged_in(self, cookies: dict[str, str]) -> bool:
        """判登谓词：某域的 cookie（名 -> 值）是否构成已登录。纯函数、零请求。

        默认 = 判登 cookie 全集都在且非空。cookie 在不在不可靠的源覆写它
        （Reddit：token_v2 匿名访客也发，以 JWT 解码出的登录态为准）。判登门
        （require_login）、登录轮询（services.login）、账号屏（is_logged_in）
        全走这一个谓词——登录成功的口径不会各说各话。
        """
        needed = self.judging_cookies
        return bool(needed) and all(cookies.get(name) for name in needed)

    def login_evidence(self) -> str:
        """登录成功报喜里「凭什么判定」的一段；覆写 logged_in 的源成对覆写。"""
        return t("login.success_cookies", cookies=t("common.enum_comma").join(self.judging_cookies))

    async def require_login(self, what: str) -> None:
        """判登门：读 profile cookie 过 logged_in，不过抛 LoginRequired。

        取数入口（open_feed / open_detail）与订阅添加先过这道门：未登录就
        明说，而不是带着空 cookie 去撞服务端再猜。cookie 读取在 core.browser
        （经模块属性调用：测试替身换的就是那一处）。
        """
        if not self.logged_in(await browser.profile_cookies(self.login_origin)):
            raise LoginRequired(t("errors.login_required", label=self.label_text, what=what))

    @abstractmethod
    async def open_feed(self, name: str) -> FeedLoader:
        """打开信息流返回可翻页加载器；子类必须实现（唯一主入口）。"""

    def label_of(self, name: str) -> str:
        """流名 -> 显示名（词汇键解析后的文本）；未知流统一抛 ValueError
        （命名流的 open_feed 守卫收一处）。"""
        label = self.feed_labels.get(name)
        if label is None:
            raise ValueError(t("errors.unknown_feed", name=name))
        return t(label)

    def opens_detail(self, item: FeedItem) -> bool:
        """Enter 分派谓词：进终端详情屏还是跳浏览器——supports_detail 与类型
        词汇表（detail_kinds）的合成判断。主屏与收藏屏共用（收藏条目的源可能
        已被 F2 停用，判据仍按源声明走）。"""
        return self.supports_detail and item.kind in self.detail_kinds

    # ---- 订阅管理：subscribable 源的声明与三步钩子（通用编排在 services/subscriptions）----
    # 编排 = 输入归一（subscription_input）→ 查重与保留字（check_subscription）→
    # 探测取名（probe_subscription）→ 落盘。前三步是源差异，落盘与出口通用；
    # 新增可订阅源只在自己包里实现这三步 + 声明文案键，services 不认识它。

    sub_placeholder: ClassVar[str] = ""  # 订阅管理屏添加框的占位文案键
    sub_hint: ClassVar[str] = ""  # 订阅管理屏底部键位提示的文案键
    sub_empty: ClassVar[str] = ""  # 零订阅占位的文案键
    sub_added: ClassVar[str] = ""  # 添加成功概要的文案键（占位符 key / label）
    sub_imports_opml: ClassVar[bool] = False  # 添加入口收不收 OPML 文件路径
    drop_enabled_when_empty: ClassVar[bool] = (
        True  # 订阅删空是否从 enabled 摘掉该源（feeds 全来自订阅的源摘；有内置流的不摘）
    )

    def subscription_input(
        self, text: str, *, key: str | None, existing: list[Subscription]
    ) -> Subscription:
        """用户输入 -> 待校验的订阅草案（key / url 定下，显示名留空）；认不出抛 SubscriptionError。

        纯函数。existing 是当前订阅表（key 派生避让用）；key 是调用方显式
        给定的 key（None = 源自己定）。"""
        raise NotImplementedError

    def check_subscription(self, draft: Subscription, existing: list[Subscription]) -> None:
        """查重与保留字校验：冲突抛 SubscriptionError（文案是源差异），通过则什么都不做。纯函数。"""
        raise NotImplementedError

    async def probe_subscription(self, draft: Subscription) -> Subscription:
        """校验草案可用并取默认显示名（拿不到留空串 = 调用方回退 key）。

        网络在这一步。订阅物不可用抛 SubscriptionError（取数层的 SourceError
        也可顺势上抛）。可以改写草案的 key / url（RSS 首页自动发现真 feed 地址）。"""
        raise NotImplementedError

    def static_feed(self, items: list[FeedItem], label: str) -> StaticFeedLoader:
        """静态首屏出口：0 条守卫 + 一次性 loader（一次拉全的小流用）。

        与 first_batch（增量 Loader 形态）并列的静态形态——「parse 全量 →
        require_feed_items → StaticFeedLoader」三行收一处（知乎热榜 / V2EX
        这类一页几十条的小流同形；上百条的静态流用 sliced_feed 切片上屏）。
        """
        require_feed_items(items, self.label_text, label)
        return StaticFeedLoader(items)

    def sliced_feed(self, items: list[FeedItem], label: str) -> SliceFeedLoader:
        """切片首屏出口：0 条守卫 + 内存切片 loader（数据一次拉全但量大的流用）。

        与 static_feed 同一道守卫，区别只在首屏给 feed_page 条、Ctrl+N
        增量补（零请求）——几百条一次性挂载会把无虚拟化的列表冻住秒级。
        """
        require_feed_items(items, self.label_text, label)
        return SliceFeedLoader(items)

    async def first_batch(self, loader: FeedLoader, label: str) -> FeedLoader:
        """open_feed 的首屏出口：拉首批 + 0 条守卫，返回 loader 供直接 return。

        「构造 loader → load_more 首批 → require_feed_items → return」的样板
        （hn/x/zhihu 信息流同形）收一处；首批后有 bespoke 校验的源（reddit
        的空首页指引）自行拆开调这两步。
        """
        await loader.load_more()
        require_feed_items(loader.items, self.label_text, label)
        return loader

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        """一次性抓取原语（脚本/测试用）：打开即取首批并释放，子类不要覆写。"""
        loader = await self.open_feed(name)
        try:
            return loader.items
        finally:
            await loader.close()

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        """打开详情，返回加载器；不支持详情的源不用覆写。"""
        raise NotImplementedError(t("errors.detail_unsupported", label=self.label_text))


class SubscribedSource(Source):
    """纯订阅聚合源的共享骨架（RSS / Telegram 同形，第三个订阅型源继承即得）。

    feeds 全部来自用户订阅表（数据驱动的加法合成，不走父类的 hide 减法，
    故不调 super().__init__()）：非空时前置聚合流「全部」（hide 可分别隐藏
    「全部」或单个订阅），零订阅 = 没有任何可选信息流。订阅 key 撞
    all_feed（保留字）的条目在这里跳过并警告（手编配置漏网的兜底，添加
    入口的拒收见 subscriptions）。

    「全部」流的四级错误出口收在本类：空表给指引 → 部分失败降级（时间线
    照出、原因进日志）→ 全失败报错 → 全空且无错才走 0 条改版守卫——单订阅
    失败隔离的语义订阅型源之间一致，修复只打一份。子类注入两个差异：
    _fetch_merged（怎么并发聚合）与 _open_one（单个订阅的流怎么开）。
    """

    all_feed: ClassVar[str]  # 聚合流名（「全部」），feeds 首位；订阅 key 的保留字
    sub_noun: ClassVar[str]  # 订阅物名词的文案键（「订阅」/「频道」），展示经 sub_noun_text
    # 详情元信息首段读的 extra 键（rss: feed_label；telegram: channel）
    _meta_extra_key: ClassVar[str] = ""

    def __init__(self) -> None:
        hidden = set(hidden_feeds(self.source_id))
        self._by_key: dict[str, Subscription] = {}
        for f in feeds_for(self.source_id):
            if f.key in hidden:
                continue
            if f.key == self.all_feed:
                logger.warning(
                    "订阅 key %r 与聚合流名 %s 撞名，已跳过（%s.feeds）",
                    f.key, self.all_feed, self.source_id,
                )
                continue
            self._by_key[f.key] = f
        keys = list(self._by_key)
        self.feeds = ([self.all_feed] if keys and self.all_feed not in hidden else []) + keys
        # 「全部」是词汇键（units.all），订阅行的 value 是用户标签（非键，
        # t() 直通）——展示统一走 feed_label()
        self.feed_labels = (
            {self.all_feed: "units.all", **{k: f.label for k, f in self._by_key.items()}}
            if self.feeds
            else {}
        )
        if not keys:
            logger.info(
                "%s 暂无订阅：在 sources.toml 的 [%s.feeds] 配置后，F2 勾选启用",
                self.label_text, self.source_id,
            )

    @property
    def sub_noun_text(self) -> str:
        """订阅物名词（订阅/频道）的展示文本。"""
        return t(self.sub_noun)

    def feed_label(self, name: str) -> str:
        """订阅型流的显示名：聚合流查词汇键，订阅行直接用用户标签。"""
        if name == self.all_feed:
            return t(self.feed_labels[name])
        feed = self._by_key.get(name)
        return feed.label if feed is not None else name

    async def open_feed(self, name: str) -> FeedLoader:
        if name == self.all_feed:
            items = await self._open_merged()
            return SliceFeedLoader(items)  # 几百条聚合一次拉全，切片上屏防秒冻
        feed = self._by_key.get(name)
        if feed is None:
            raise ValueError(t("errors.unknown_feed", name=name))
        return await self._open_one(feed)

    async def _open_merged(self) -> list[FeedItem]:
        """「全部」聚合流的全部条目（一次并发拉全；上屏走 SliceFeedLoader 切片）。"""
        if not self._by_key:
            # 订阅刚被删光的过渡窗口：旧实例还挂着「全部」流，别让空表走到
            # 0 条守卫报「站点可能已改版」——那是误导，指引才是实话
            raise SourceError(
                t(
                    "errors.no_subscriptions_hint",
                    label=self.label_text, sub=self.sub_noun_text,
                )
            )
        items, errors = await self._fetch_merged(list(self._by_key.values()))
        if errors and items:  # 部分失败降级：时间线照出，失败原因进日志
            logger.warning(
                "%s「全部」%d/%d 个%s拉取失败：%s",
                self.label_text, len(errors), len(self._by_key),
                self.sub_noun_text, "；".join(errors[:3]),
            )
        if not items and errors:
            raise SourceError(
                t(
                    "errors.all_feeds_failed",
                    label=self.label_text, feed=t("units.all"),
                    n=len(errors), errors="；".join(errors[:3]),
                )
            )
        return require_feed_items(items, self.label_text, t("units.all"))  # 全空且无错 = 可能改版

    @abstractmethod
    async def _fetch_merged(
        self, feeds: list[Subscription]
    ) -> tuple[list[FeedItem], list[str]]:
        """并发拉全部订阅并聚合：(合并条目, 失败订阅的短消息列表)。"""

    @abstractmethod
    async def _open_one(self, feed: Subscription) -> FeedLoader:
        """打开单个订阅的信息流（各源的取数/解析差异全在这里）。"""

    async def open_detail(self, item: FeedItem) -> StaticDetailLoader:
        """订阅型源的静态详情骨架：正文随列表带回（extra），进详情零请求。

        meta =「来源标签 · 作者」；发布时间与正文的提取是源差异，见
        _detail_time / _detail_body（声明 supports_detail 的子类必给）。
        RSS / Telegram 共用，第三个订阅型源继承即得。
        """
        extra = item.extra
        meta = " · ".join(
            x
            for x in (str(extra.get(self._meta_extra_key) or ""), str(extra.get("author") or ""))
            if x
        )
        return StaticDetailLoader(
            DetailDoc(
                title=item.title,
                meta=meta,
                time=self._detail_time(extra),
                description_md=self._detail_body(extra),
            )
        )

    def _detail_time(self, extra: dict[str, Any]) -> str:
        """extra 里的发布时间 -> 展示文本（源差异：unix 秒 / ISO 字符串）。"""
        raise NotImplementedError

    def _detail_body(self, extra: dict[str, Any]) -> str:
        """extra 里的正文 -> markdown（源差异：content_html 转 / content_md 直取）。"""
        raise NotImplementedError


class PlaceholderSource(Source):
    """注册表里已不存在的源的占位（源下架后，收藏 / 列表缓存里还留着它的条目）：
    详情能力全关、没有任何流——行照常显示（源名列落回裸 id），Enter 走浏览器。
    跨源列表（收藏屏 / 搜索结果）共用一个实例（UNKNOWN_SOURCE）。

    跳过 Source.__init__：占位源不读配置不进任何流（空 feeds 走父类会误报
    「信息流已被全部隐藏」的警告，import 期还可能被真实配置触发）。
    """

    source_id = ""
    label = ""

    def __init__(self) -> None:
        self.feeds = []

    async def open_feed(self, name: str) -> FeedLoader:
        raise NotImplementedError


UNKNOWN_SOURCE = PlaceholderSource()
