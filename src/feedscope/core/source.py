"""Source 插件契约：一个网站一个子类，放在 feedscope.sources 下自行注册。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar

from feedscope.core.config import hidden_feeds
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem

logger = logging.getLogger(__name__)


class SourceError(Exception):
    """源侧失败的业务异常：消息为面向用户的中文（含源标签与原因），UI 可直接展示。

    源在取数/解析边界把「自己认识的失败」（HTTP 状态、服务端错误体、超时）
    包成它；UI 出口对它直接展示消息，对其他技术异常才补「源标签 + 失败」前缀。
    """


class LoginRequired(SourceError):
    """源要求登录但当前 profile 未登录（如知乎重定向到 /signin）。"""


class FeedLoader(ABC):
    """信息流加载器：可能持有浏览器页签等资源，切换/退出前必须 close()。"""

    items: list[FeedItem]
    done: bool = False  # 真的没有更多了（拉空不等于 done，可能只是网慢）

    @abstractmethod
    async def load_more(self) -> list[FeedItem]:
        """加载下一批条目；空列表 = 没有更多（或暂时没拉到，看 done）。"""

    @abstractmethod
    async def close(self) -> None:
        """释放资源，幂等，出错自己吞。"""


class StaticFeedLoader(FeedLoader):
    """一次性加载器的默认实现：没有更多。"""

    def __init__(self, items: list[FeedItem]) -> None:
        self.items = items
        self.done = True

    async def load_more(self) -> list[FeedItem]:
        return []

    async def close(self) -> None:
        pass


def dedup_append(
    items: list[FeedItem], seen: set[str], candidates: Iterable[FeedItem | None]
) -> list[FeedItem]:
    """把 candidates 中未见过的条目追加进 items，返回新增列表。

    去重键 = FeedItem.id（源内稳定 id）；None（噪音卡）跳过。
    各源首批提取与翻页 Loader 共用，保证「同一 id 只出现一次」的不变量只有一处实现。
    """
    new: list[FeedItem] = []
    for it in candidates:
        if it is not None and it.id not in seen:
            seen.add(it.id)
            items.append(it)
            new.append(it)
    return new


def require_feed_items(items: list[FeedItem], label: str, feed: str = "") -> list[FeedItem]:
    """信息流首屏守卫：取数成功但解析出 0 条 = 大概率站点改版，显式报错而非静默空列表。

    只用于 open_feed 的首批（各源编排层出口统一调用）；load_more 翻页拉空
    （到底/全重复）是正常语义，不套这层。
    """
    if not items:
        head = f"{label}「{feed}」" if feed else label
        raise SourceError(f"{head} 解析到 0 条内容，站点可能已改版（feedscope probe 可复查）")
    return items


class DetailLoader(ABC):
    """详情加载器：可能持有浏览器页签等资源，UI 用完必须 close()。

    不变量：doc 始终等于已加载的全部内容——load_more 拿到的新回答要
    append 进 doc.answers。UI 依赖这一点做整屏重排（终端改宽度时）。
    """

    doc: DetailDoc
    done: bool = False  # 真的没有更多内容了（拉空不等于 done，可能只是网慢）

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
        raise NotImplementedError(f"{type(self).__name__} 不支持评论层")

    @abstractmethod
    async def close(self) -> None:
        """释放资源，幂等，出错自己吞。"""


class CommentLoader(ABC):
    """评论层加载器（DetailLoader.open_comments 的返回契约）。

    与楼层 loader 同形的不变量：comments 始终等于已加载全部（load_more
    的新评论要 append 进去）；done 只在真到头时为真，拉空可能只是网慢
    可再试；close() 幂等且通常只收评论层，不关它所共享的问题底座。
    """

    comments: list[Comment]
    done: bool = False

    @abstractmethod
    async def load_more(self) -> list[Comment]:
        """加载下一批评论；空列表 = 没有更多（或暂时没拉到，看 done）。"""

    @abstractmethod
    async def close(self) -> None:
        """释放资源，幂等，出错自己吞。"""


class Source(ABC):
    """源基类。子类用 @register 注册，由 TUI 装载。

    一个源可以暴露多个命名信息流（如知乎的热榜/推荐/关注），
    feeds 的顺序就是 Tab 切换顺序。类里写死的是内置默认流；用户可在
    sources.toml 隐藏内置流（见 core/config.py），__init__ 合成实例视图。

    源还可以声明一键直达键 source_key（如 "z"）：TUI 启动时按注册表自动生成
    绑定，无需修改 UI；与 UI 自有键位或其他源冲突的声明会被跳过并警告，
    该源仍可 Tab 循环到达。

    类型词汇表归源所有，UI 不认识任何具体类型：
    - kind_labels：kind -> 元信息列标签（如 pin -> "想法"），预告 Enter 跳浏览器；
    - detail_kinds：Enter 进终端详情屏的 kind，其余一律跳浏览器。
    """

    source_id: ClassVar[str]                    # 注册键，如 "zhihu"
    label: ClassVar[str]                        # 展示名，如 "知乎"
    source_key: ClassVar[str | None] = None     # 一键直达键（单个小写字母，如 "z"）；None = 只能 Tab 循环到达
    feeds: ClassVar[list[str]] = []             # 命名信息流；空 = 只有默认流（name=""）
    feed_labels: ClassVar[dict[str, str]] = {}  # 信息流显示名，缺省用 name 本身
    supports_detail: ClassVar[bool] = False     # 决定 Enter 是否可能进详情屏
    supports_comments: ClassVar[bool] = False   # 决定详情屏 Enter 是否可能进评论层
    subscribable: ClassVar[bool] = False        # 信息流来自用户订阅：F2 里 Enter 进订阅管理屏
    kind_labels: ClassVar[dict[str, str]] = {}  # kind -> 元信息列标签
    detail_kinds: ClassVar[frozenset[str]] = frozenset({""})
    detail_unit_label: ClassVar[str] = "回答"   # 详情屏楼层单位词（X 覆写为"回复"）
    comment_unit_label: ClassVar[str] = "评论"  # 楼层内评论子区的单位词（无评论产出的源用不到）
    login_url: ClassVar[str] = ""               # 有登录需求的源声明登录页；空 = 无需登录
    login_cookie: ClassVar[str] = ""            # 登录成功写入 profile 的 cookie 名（feedscope login 判登依据）
    logout_cookies: ClassVar[list[str]] = []    # 登出要清的 cookie 名（判登全集）；空 = 无需登出

    def __init__(self) -> None:
        """按用户配置合成实例视图：feeds 在类默认之上剔除被隐藏的流。

        类属性 = 内置默认（ClassVar 语义即「默认值」）；实例属性 = 合成后的
        当前视图，UI / open_feed 分派只读实例。
        """
        hidden = hidden_feeds(self.source_id)
        feeds = [f for f in self.feeds if f not in hidden]
        self.feeds = feeds
        if not feeds:
            logger.warning("%s 的信息流已被配置全部隐藏", self.label)

    @abstractmethod
    async def open_feed(self, name: str) -> FeedLoader:
        """打开信息流返回可翻页加载器；子类必须实现（唯一主入口）。"""

    async def fetch_feed(self, name: str) -> list[FeedItem]:
        """一次性抓取原语（脚本/测试用）：打开即取首批并释放，子类不要覆写。"""
        loader = await self.open_feed(name)
        try:
            return loader.items
        finally:
            await loader.close()

    async def open_detail(self, item: FeedItem) -> DetailLoader:
        """打开详情，返回加载器；不支持详情的源不用覆写。"""
        raise NotImplementedError(f"{self.label} 暂不支持终端内详情")
