"""合并式调度：背靠背触发只跑一次、在途再触发排一次重跑的 worker 收口。

remove_children / clear 的删除是异步落地，两个重建交错挂载会撞重复行
（DuplicateIds / 同一行挂两遍——真机快操作可复现）；exclusive 取消也能
防并发，但被取代的协程从未启动会留 never-awaited 警告——busy/dirty +
重跑循环两全。主屏边栏刷新与收藏屏整屏重建共用（原先两处同形实现收敛
于此）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.widget import Widget


class CoalescedScheduler:
    """一个重建目标的合并式调度器：trigger() 请求一轮，在途只标脏不并跑。"""

    def __init__(
        self,
        holder: Widget,
        run: Callable[[], Awaitable[None]],
        *,
        group: str = "default",
        alive: Callable[[], bool] | None = None,
    ) -> None:
        self._holder = holder
        self._run = run
        self._group = group
        self._alive = alive  # 收尾期不排程（屏已换走还挂 worker 会炸屏内查询）
        self._busy = False
        self._dirty = False

    def trigger(self) -> None:
        """请求一轮重建：在途只标脏（cycle 跑完见脏自动再来一轮）。"""
        if self._alive is not None and not self._alive():
            return
        if self._busy:
            self._dirty = True
            return
        self._busy = True
        self._holder.run_worker(self._cycle(), group=self._group)

    async def _cycle(self) -> None:
        try:
            while True:
                await self._run()
                if not self._dirty:
                    return
                self._dirty = False  # 在途期间又有人要刷新：再来一轮
        finally:
            self._busy = False
