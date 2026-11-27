"""源插件包：import 即注册（类装饰器 @register），import 顺序 = 列表顺序。

import 按 source_id 字母正序书写 = 源选择列表与未配置时的默认 Tab 序（该
不变量由 test_source_caps 钉住：新增源插错位置测试即红）。

新增一个源 = 按下面的模板建一个包 + 在这里挂一行 import，core / services /
UI 零改动（用户侧隐藏内置流走 sources.toml，见 core/config.py，不动这里；
可订阅的源只在自己包里实现订阅钩子，见下）：

    sources/<id>/
      fetch.py      取数层：拿原始数据（httpx / 通道页签 fetch / DOM 提取）；
                    持资源的增量 Loader 也放这里。不碰 UI。
      parse.py      解析层：raw dict/HTML -> FeedItem/Answer/DetailDoc/markdown，
                    纯函数，直接可写 fixture 测试；不碰网络。公开函数一律
                    parse_ 前缀（= 解析层入口），带 _ 的是模块私有、不出模块。
      __init__.py   编排层：只有 @register 的 Source 子类——能力声明与 open_*
                    分派（出口统一过 require_feed_items 首屏守卫）。需要登录
                    的源在取数入口先过 self.require_login（判据覆写
                    Source.logged_in）；可订阅的源实现三个订阅钩子
                    （subscription_input / check_subscription / probe_subscription）
                    并声明订阅管理屏的文案键。

编排层按源形态直接继承共享骨架，别重写机械件（详情骨架 / 批次去重 /
判登 cookie 读取都已收口）：
- 纯订阅聚合（RSS / Telegram 形）：继承 core.source.SubscribedSource——
  feeds 加法合成、「全部」流的四级错误出口全在基类，子类只注入
  _fetch_merged / _open_one 两个差异
- 详情一次拉全树（HN / Reddit / V2EX 形）：open_detail 返回
  core.loaders.SliceDetailLoader.from_detail(...)，楼层/评论翻页切片零代码
- 正文随列表带回（RSS / Telegram 形）：详情用 core.loaders.StaticDetailLoader，
  进详情零请求
- 讨论树深层拍平：core.tree（@ 父作者前缀规则全库唯一实现）

依赖方向只允许 __init__ -> fetch -> parse -> core（parse 永远是最底层叶子）。
"""
from feedscope.sources import (
    hn,  # noqa: F401
    reddit,  # noqa: F401
    rss,  # noqa: F401
    telegram,  # noqa: F401
    v2ex,  # noqa: F401
    x,  # noqa: F401
    zhihu,  # noqa: F401
)
