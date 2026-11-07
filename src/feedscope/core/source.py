"""Source 插件契约：一个网站一个子类，放在 feedscope.sources 下自行注册。"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, ClassVar, TypeVar

from feedscope.core.config import Subscription, feeds_for, hidden_feeds
from feedscope.core.i18n import t
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class SourceError(Exception):
    """源侧失败的业务异常：消息为面向用户的当前语言文案（含源标签与原因，
    经 core.i18n.t 生成），UI 可直接展示。

    源在取数/解析边界把「自己认识的失败」（HTTP 状态、服务端错误体、超时）
    包成它；UI 出口对它直接展示消息，对其他技术异常才补「源标签 + 失败」前缀。
    """


class LoginRequired(SourceError):
    """源要求登录但当前 profile 未登录（如知乎重定向到 /signin）。"""


class FeedLoader(ABC):
    """信息流加载器：可能持有浏览器页签等资源，切换/退出前必须 close()。"""

    items: list[FeedItem]

    @property
    @abstractmethod
    def done(self) -> bool:
        """真的没有更多了（只读派生态：拉空不等于 done，可能只是网慢）。"""

    @abstractmethod
    async def load_more(self) -> list[FeedItem]:
        """加载下一批条目；空列表 = 没有更多（或暂时没拉到，看 done）。"""

    async def close(self) -> None:
        """释放资源，幂等，出错自己吞。

        默认 no-op：不持资源的源（通道页签/连接池进程级共享）不覆写，
        持浏览器页签等资源的子类才需要。"""


class StaticFeedLoader(FeedLoader):
    """一次性加载器的默认实现：没有更多。"""

    def __init__(self, items: list[FeedItem]) -> None:
        self.items = items

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[FeedItem]:
        return []


def dedup_by(
    candidates: Iterable[_T | None], seen: set[str], *, key: Callable[[_T], str]
) -> list[_T]:
    """按 key 去重地放行 candidates（未见过的），并把新键登记进 seen。

    None（噪音卡）与键已见过的条目跳过；键为空（拿不到稳定 id）不去重
    直接放行——宁可重复不可丢条。FeedItem（dedup_append）/ Answer /
    Comment / 源内原始 dict 的批次去重共用，「同一键只出现一次」的不变量
    只有这一处实现。
    """
    new: list[_T] = []
    for it in candidates:
        if it is None:
            continue
        k = key(it)
        if not k or k not in seen:
            if k:
                seen.add(k)
            new.append(it)
    return new


def dedup_append(
    items: list[FeedItem], seen: set[str], candidates: Iterable[FeedItem | None]
) -> list[FeedItem]:
    """把 candidates 中未见过的条目追加进 items，返回新增列表。

    去重键 = FeedItem.id（源内稳定 id）；None（噪音卡）跳过。
    各源首批提取与翻页 Loader 共用（dedup_by 的 FeedItem 特化）。
    """
    new = dedup_by(candidates, seen, key=lambda it: it.id)
    items.extend(new)
    return new


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


class DetailLoader(ABC):
    """详情加载器：可能持有浏览器页签等资源，UI 用完必须 close()。

    不变量：doc 始终等于已加载的全部内容——load_more 拿到的新回答要
    append 进 doc.answers。UI 依赖这一点做整屏重排（终端改宽度时）。
    """

    doc: DetailDoc

    @property
    @abstractmethod
    def done(self) -> bool:
        """真的没有更多内容了（只读派生态：拉空不等于 done，可能只是网慢）。"""

    @abstractmethod
    async def load_more(self) -> list[Answer]:
        """加载下一批回答；空列表 = 没有更多。"""

    async def open_comments(self, index: int) -> CommentLoader:
        """按需打开第 index 个楼层（回答）的评论层，返回评论 loader。

        多层递进的关键：评论不随首批抓取（首载要快），用户进到某条回答
        才请求。不支持的源不覆写（配合 Source.supports_comments，UI 不给
        入口）。评论 loader 契约见 CommentLoader——通常与问题 loader 共享
        资源，close 只收评论层不关底座。
        """
        raise NotImplementedError(t("errors.comments_unsupported", name=type(self).__name__))

    async def close(self) -> None:
        """释放资源，幂等，出错自己吞（默认 no-op，语义同 FeedLoader.close）。"""


class CommentLoader(ABC):
    """评论层加载器（DetailLoader.open_comments 的返回契约）。

    与楼层 loader 同形的不变量：comments 始终等于已加载全部（load_more
    的新评论要 append 进去）；done 只在真到头时为真，拉空可能只是网慢
    可再试；close() 幂等且通常只收评论层，不关它所共享的问题底座。
    """

    comments: list[Comment]

    @property
    @abstractmethod
    def done(self) -> bool:
        """真的到头了（只读派生态：拉空不等于 done，可能只是网慢）。"""

    @abstractmethod
    async def load_more(self) -> list[Comment]:
        """加载下一批评论；空列表 = 没有更多（或暂时没拉到，看 done）。"""

    async def close(self) -> None:
        """释放资源，幂等，出错自己吞（默认 no-op，语义同 FeedLoader.close）。"""


class SliceFeedLoader(FeedLoader):
    """整批条目已在内存的信息流加载器：翻页是本地切片，零额外请求。

    「一次拉全」的流（RSS 聚合「全部」/ 单订阅）共用：数据全量在内存，
    但首屏只上屏 feed_page 条、Ctrl+N 增量补——Textual 的 ListView 没有
    虚拟化，几百行一次性挂载/改宽重排会冻结秒级，切片把它压到常数。
    与 SliceCommentsLoader / SliceDetailLoader 同形（「items = 已加载
    全部」的不变量由本类保证）。
    """

    feed_page: ClassVar[int] = 100  # 首批与每次 Ctrl+N 的条数（约三屏）

    def __init__(self, items: Sequence[FeedItem]) -> None:
        self.items = list(items[: self.feed_page])
        self._rest = list(items[self.feed_page :])

    @property
    def done(self) -> bool:
        return not self._rest

    async def load_more(self) -> list[FeedItem]:
        if self.done:
            return []
        batch, self._rest = self._rest[: self.feed_page], self._rest[self.feed_page :]
        self.items.extend(batch)  # 「items = 已加载全部」不变量
        return batch


class StaticDetailLoader(DetailLoader):
    """一次性到头的详情加载器：doc 即全部内容，无楼层可翻、无资源可释。

    正文已随列表带回的源（RSS / Telegram）用；feed 侧的对称物是
    StaticFeedLoader。
    """

    def __init__(self, doc: DetailDoc) -> None:
        self.doc = doc

    @property
    def done(self) -> bool:
        return True

    async def load_more(self) -> list[Answer]:
        return []


class SliceCommentsLoader(CommentLoader):
    """整串评论已在内存的评论层加载器：翻页是本地切片，零额外请求。

    「详情一次拉全树」的源（HN / Reddit）的评论层共用；页宽
    comment_page 控制首批与 Ctrl+N 增量。
    """

    comment_page: ClassVar[int] = 20

    def __init__(self, comments: Sequence[Comment]) -> None:
        self.comments = list(comments[: self.comment_page])
        self._rest = list(comments[self.comment_page :])

    @property
    def done(self) -> bool:
        return not self._rest

    async def load_more(self) -> list[Comment]:
        if self.done:
            return []
        batch, self._rest = self._rest[: self.comment_page], self._rest[self.comment_page :]
        self.comments.extend(batch)  # 「comments = 已加载全部」不变量
        return batch


class SliceDetailLoader(DetailLoader):
    """楼层已全在内存的详情加载器：load_more 是本地切片，零额外请求。

    「详情一次拉全树」的源（HN / Reddit / V2EX）共用：构造时给抽好的楼层
    Answer 序列（首批 = 前 floor_page 条）；带评论层的源再给与楼层对齐的
    comment_trees（open_comments 按下标切出对应树的 SliceCommentsLoader，
    不给则与本类约定不支持评论层）。「doc = 已加载全部」的不变量由本类
    保证（load_more 的新楼层 append 进 doc.answers）。子类只剩薄适配：
    from_detail 吃源 parse 的 detail 产物，再声明 source_label（越界报错
    文案）；页宽 floor_page 有默认值，真要差异化再覆写。
    """

    source_label: ClassVar[str] = ""  # 报错文案里的源标签（如 "HN"）
    floor_page: ClassVar[int] = 15  # 首批与每次 Ctrl+N 的楼层数（内存切片，不是请求）

    def __init__(
        self,
        floors: Sequence[Answer],
        *,
        title: str,
        meta: str = "",
        time: str = "",
        description_md: str = "",
        comment_trees: Sequence[Sequence[Comment]] | None = None,
    ) -> None:
        self._floors = list(floors)
        self._comments = list(comment_trees) if comment_trees is not None else None
        first = self._floors[: self.floor_page]
        self._given = len(first)
        self.doc = DetailDoc(
            title=title,
            meta=meta,
            time=time,
            description_md=description_md,
            answers=list(first),
        )

    @classmethod
    def from_detail(cls, detail: Any, *, fallback_title: str = "") -> SliceDetailLoader:
        """楼层型 detail 产物的替代构造（HN / Reddit / V2EX 的 parse 产物）。

        两形鸭子类型（字段同名，title/meta/time/description_md + 楼层）：
        - .floors：Floor 列表（HN 的 StoryDetail / Reddit 的 ThreadDetail），
          answer 与评论树成对进切片（支持评论层）
        - .replies：裸 Answer 列表（V2EX 的 TopicDetail，无评论层）
        title 为空时退 fallback_title（列表端标题）。
        """
        floors = getattr(detail, "floors", None)
        kwargs: dict[str, Any] = {
            "title": str(getattr(detail, "title", "") or "") or fallback_title,
            "meta": getattr(detail, "meta", ""),
            "time": getattr(detail, "time", ""),
            "description_md": getattr(detail, "description_md", ""),
        }
        if floors is not None:
            return cls(
                [f.answer for f in floors],
                comment_trees=[f.comments for f in floors],
                **kwargs,
            )
        return cls(list(getattr(detail, "replies", [])), **kwargs)

    @property
    def done(self) -> bool:
        return self._given >= len(self._floors)

    async def load_more(self) -> list[Answer]:
        if self.done:
            return []
        batch = self._floors[self._given : self._given + self.floor_page]
        self._given += len(batch)
        self.doc.answers.extend(batch)  # 「doc = 已加载全部」不变量
        return batch

    async def open_comments(self, index: int) -> SliceCommentsLoader:
        if self._comments is None:
            raise NotImplementedError(
                t("errors.comments_unsupported", name=self.source_label)
            )
        if not 0 <= index < len(self.doc.answers):
            raise SourceError(
                t("errors.comment_out_of_range", label=self.source_label)
            )
        return SliceCommentsLoader(self._comments[index])


async def login_gate(
    url: str, ok: Callable[[dict[str, str]], bool], *, label: str, what: str
) -> None:
    """判登门的共享实现：读 profile cookie 过谓词，不过抛 LoginRequired。

    x（auth_token+ct0 成对在）与 Reddit（token_v2 JWT 解码）共用——判据是
    源差异（注入谓词），cookie 读取与登录提示的消息模板只有这一份，不会
    措辞漂移。
    """
    from feedscope.core.browser import profile_cookies  # 惰性导入：browser 已 import 本模块

    if not ok(await profile_cookies(url)):
        raise LoginRequired(t("errors.login_required", label=label, what=what))


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
    label: ClassVar[str]  # 展示名的文案键（如 "sources.zhihu.label"），展示经 label_text 解析
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
    login_cookie: ClassVar[str] = (
        ""  # 登录轮询的兜底 cookie（未声明 logout_cookies / login_verify 时用）
    )
    login_verify: ClassVar[Any | None] = (
        None  # async (browser_context) -> bool；登录成功的浏览器级判据（优先）
    )
    # （cookie 判登不可靠的源覆盖此钩子，如 Reddit：token_v2 匿名访客也发，
    #   登录成功以 JWT 解码出 sub="user" 为准；None = 判登 cookie 全集（再退 login_cookie））
    logout_cookies: ClassVar[list[str]] = (
        []  # 判登/登出 cookie 全集；空 = 只认 login_cookie / login_verify
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
        """判登要看的 cookie 名全集：logout_cookies 优先，未声明才退 login_cookie 单枚。

        login() 轮询 / is_logged_in / logout 三条路径共用（口径全项目唯一），
        不给「login 报成功、账号屏仍显示未登录」的分裂留缝。
        """
        if self.logout_cookies:
            return list(self.logout_cookies)
        return [self.login_cookie] if self.login_cookie else []

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
