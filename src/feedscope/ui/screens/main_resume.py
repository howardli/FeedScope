"""主屏的续读位置协作者：退出时记下源 / 流 / 选中条目，下次启动回到那里。

状态在 core/store（state.json 的 last），本对象只管「什么时候记、什么时候
用掉」：启动时 restore() 把上次的源与流停回去并记下续读目标（条目要等列表
画出来才能定位）；FeedFlow 上屏时经 index_in() 按目标定位选中，抓取定稿那一轮
用掉它；退出时 save() 落盘。用户自己动了选中 / 换了流或站，目标作废（drop）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedscope.core.models import FeedItem

if TYPE_CHECKING:  # 仅注解用：运行时 main_setup.py 导入本模块
    from feedscope.ui.screens.main import MainScreen


class ResumeTracker:
    """续读位置的记与用（一个屏一份；由 MainSetupScreen.__init__ 创建）。"""

    def __init__(self, screen: MainScreen) -> None:
        self.screen = screen
        self.target: str | None = None  # 续读目标：上次停下的条目 id（列表画出来后按它定位选中）
        self.cursor_id = ""  # 当前选中条目的 id（选中变化的镜像，退出时要读）

    def drop(self) -> None:
        """用户自己动了选中 / 换了流或站：续读目标作废。

        续读目标要活到抓取定稿那一次上屏（缓存先画的那一轮不算数），期间
        用户已经挪了地方的话，迟到的定稿不能再把选中拽回原来那条。"""
        self.target = None

    # ---- 启动：回到上次停下的源与流 ----

    def restore(self) -> None:
        """启动时回到上次停下的源与流，并把那条条目记成续读目标。

        上次的源已不在启用集里（F2 停用、订阅删空被过滤出局）→ 停在第一个源；
        流已不存在（被 hide / 订阅改名）→ 该站的第一个流。条目本身要等列表画出来
        才能定位（见 index_in）。没记过位置（全新安装）= 什么都不做。"""
        last = self.screen._tui.state.last_position()
        source_id = last.get("source", "")
        idx = next((i for i, s in enumerate(self.screen.sources) if s.source_id == source_id), None)
        if idx is None:
            return
        feeds = self.screen.sources[idx].feeds
        feed = last.get("feed", "")
        matched = feed in feeds
        self.screen.source_idx = idx
        self.screen.feed_idx = feeds.index(feed) if matched else 0
        self.screen._feed_memory[source_id] = self.screen.feed_idx  # 切走再切回来还停在这个流
        # 条目目标跟着流走：流已不存在，那个 id 属于另一个列表，不作数
        self.target = (last.get("id") or None) if matched else None

    # ---- 上屏：按目标定位选中 ----

    def index_in(self, shown: list[FeedItem], *, settle: bool) -> int | None:
        """上次停下的那条在本次行集里的下标；没有目标 / 不在行集里回 None。

        按 id 找而不是记下标：两次启动之间新到的条目会把它往下顶，下标早就不
        是原来那个。settle（抓取定稿）用掉目标——之后用户按 F5 照常回第一条；
        目标已滚出列表也就此作废。"""
        target = self.target
        if target is None:
            return None
        if settle:
            self.target = None
        return next((i for i, it in enumerate(shown) if it.id == target), None)

    # ---- 退出：记下看到哪儿了 ----

    def save(self) -> None:
        """退出时记下看到哪儿了（源 / 流 / 选中条目）。

        条目读的是 cursor_id（选中变化的镜像，主屏在 ListView.Highlighted 里记下）
        而不是去查列表：卸载钩子跑到的
        时候列表控件已经不在树上了，查不到。"""
        if not self.screen.sources:
            return  # 一个源都没启用：没有「位置」可记
        source_id, feed = self.screen.current_source.source_id, self.screen.current_feed
        last = self.screen._tui.state.last_position()
        item_id = self.cursor_id
        if not item_id and (last.get("source"), last.get("feed")) == (source_id, feed):
            # 列表还没画出来就退出了（启动后一秒内）：还是同一个流，保留上次记的
            # 条目——拿空值盖掉的话，续读目标白白丢一回
            item_id = last.get("id", "")
        self.screen._tui.state.save_position(source_id, feed, item_id)
