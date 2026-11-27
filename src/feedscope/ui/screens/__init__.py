"""TUI 屏幕：按家族分文件，家族内的共享骨架同目录。

本地列表家族（数据全在内存的跨源条目列表）
  local_list.py       LocalListScreen：分页挂载 / 改宽重排 / 已读同步 / 换篇的骨架
  favorites.py        收藏屏（F3）
  search.py           本地搜索屏（F6）
主屏家族（四层继承链 Setup → Nav → Read → Main + 两个协作者，各层一职）
  main_setup.py       MainSetupScreen：启用集与当前位置、首启 / F2 的生效流程
  main_nav.py         MainNavScreen：三面板方向键导航与切站切流
  main_read.py        MainReadScreen：行集、过滤与已读同步
  main.py             MainScreen：compose 与样式、两侧导航的渲染、键位接线
  main_feed.py        FeedFlow：取数上屏协作者（刷新 / Ctrl+N / 整批上屏 / 重排）
  main_resume.py      ResumeTracker：续读位置协作者（回到上次的位置、定稿时用掉）
阅读家族（列表 -> 详情 -> 回答，层层递进）
  reading.py          ReadingScreen / PagedReadingScreen：重排、翻页、换目标的骨架
  detail.py           详情屏（问题 / 帖子 / 推文）
  answer.py           回答屏（回答全文 + 评论区）
弹层
  dialogs.py（上一级）、help.py、language.py、links.py、source_setup.py、
  subscriptions.py、theme.py

主屏家族的状态归属（谁声明、谁初始化、谁改）：

  状态                        归属               谁改
  --------------------------  -----------------  ----------------------------------------
  sources / source_idx /      MainSetupScreen    F2 生效流程、导航层挪位置、
  feed_idx / _feed_memory                        ResumeTracker.restore
  _subs_dirty                 MainSetupScreen    源管理屏的回调
  _pane                       MainNavScreen      方向键导航
  items / _unread_only /      MainReadScreen     FeedFlow 置 items；F4 与 / 切开关
  _title_filter
  loader / new_count /        FeedFlow           只有 FeedFlow
  三列宽度
  resume.target / cursor_id   ResumeTracker      restore / index_in / drop / 主屏的
                                                 ListView.Highlighted 处理器（记 cursor_id）

依赖方向：上层只经「根层声明的协作者（self.flow / self.resume）+ 自己的状态」
做事；仍需叶子实现的只有三个渲染钩子（_render_source_tabs /
_render_feed_sidebar / _refresh_sidebar——控件是叶子 compose 的）。
"""
