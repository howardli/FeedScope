"""加载器契约与通用实现：翻页的统一形状（信息流 / 详情楼层 / 楼层内评论三层）。

Source 的 open_feed / open_detail / DetailLoader.open_comments 返回的就是这里的
类型；UI 只认这三个 ABC，不认识任何具体源：

    FeedLoader        信息流（列表）——items 永远等于已加载的全部条目
    DetailLoader      详情楼层——doc.answers 永远等于已加载的全部楼层
    CommentLoader     楼层内评论层——comments 永远等于已加载的全部评论

「X 永远等于已加载全部」的不变量是 UI 整屏重排（终端改宽）的地基，三处 ABC
的 docstring 各自重申。现成实现两种形态，源的编排层按需直接取用、不必重写
机械件：Static*（一次到头，零请求）/ Slice*（整批已在内存，翻页是本地切片）。
dedup_by 是「同一键只出现一次」的唯一实现（条目 / 回答 / 评论 / 源内原始 dict
的批次去重共用）。需要真翻页的源在各自 fetch.py 里继承 ABC 自写。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from typing import Any, ClassVar, TypeVar

from feedscope.core.errors import SourceError
from feedscope.core.i18n import t
from feedscope.core.models import Answer, Comment, DetailDoc, FeedItem

_T = TypeVar("_T")


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

    async def close(self) -> None:  # noqa: B027 - 可选钩子（docstring 即说明），非漏标 abstract
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

    async def close(self) -> None:  # noqa: B027 - 可选钩子，语义同 FeedLoader.close
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

    async def close(self) -> None:  # noqa: B027 - 可选钩子，语义同 FeedLoader.close
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
    保证（load_more 的新楼层 append 进 doc.answers）。源不必为它建子类：
    from_detail 吃 parse 的 detail 产物，source_label 给报错文案里的源标签
    （如 "HN"）；页宽 floor_page 有默认值，真要差异化再覆写。
    """

    floor_page: ClassVar[int] = 15  # 首批与每次 Ctrl+N 的楼层数（内存切片，不是请求）

    def __init__(
        self,
        floors: Sequence[Answer],
        *,
        title: str,
        source_label: str = "",
        meta: str = "",
        time: str = "",
        description_md: str = "",
        comment_trees: Sequence[Sequence[Comment]] | None = None,
    ) -> None:
        self._source_label = source_label  # 报错文案里的源标签（越界 / 不支持评论层）
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
    def from_detail(
        cls, detail: Any, *, fallback_title: str = "", source_label: str = ""
    ) -> SliceDetailLoader:
        """楼层型 detail 产物的替代构造（HN / Reddit / V2EX 的 parse 产物）。

        两形鸭子类型（字段同名，title/meta/time/description_md + 楼层）：
        - .floors：Floor 列表（HN 的 StoryDetail / Reddit 的 ThreadDetail），
          answer 与评论树成对进切片（支持评论层）
        - .replies：裸 Answer 列表（V2EX 的 TopicDetail，无评论层）
        title 为空时退 fallback_title（列表端标题）；source_label 进报错文案。
        """
        floors = getattr(detail, "floors", None)
        kwargs: dict[str, Any] = {
            "title": str(getattr(detail, "title", "") or "") or fallback_title,
            "meta": getattr(detail, "meta", ""),
            "time": getattr(detail, "time", ""),
            "description_md": getattr(detail, "description_md", ""),
            "source_label": source_label,
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
                t("errors.comments_unsupported", name=self._source_label)
            )
        if not 0 <= index < len(self.doc.answers):
            raise SourceError(
                t("errors.comment_out_of_range", label=self._source_label)
            )
        return SliceCommentsLoader(self._comments[index])
