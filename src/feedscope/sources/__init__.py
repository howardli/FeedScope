"""源插件包：import 即注册（类装饰器 @register），import 顺序 = 列表顺序。

import 按 source_id 字母正序书写 = 源选择列表与未配置时的默认 Tab 序（该
不变量由 test_source_caps 钉住：新增源插错位置测试即红）。

新增一个源 = 按下面的模板建一个包 + 在这里挂一行 import，UI 与 core 零改动
（用户侧隐藏内置流走 sources.toml，见 core/config.py，不动这里；可订阅源
另在 subscriptions 登记一个 SubsStore，test_source_caps 会查漏）：

    sources/<id>/
      fetch.py      取数层：拿原始数据（httpx / 通道页签 fetch / DOM 提取），
                    持资源的增量 Loader 也放这里；不碰 UI。
      parse.py      解析层：raw dict/HTML -> FeedItem/Answer/DetailDoc/markdown，
                    纯函数，直接可写 fixture 测试；不碰网络。
      __init__.py   编排层：@register 的 Source 子类，做能力声明与 open_* 分派
                    （出口统一过 require_feed_items 首屏守卫）、判登门
                    （core.source.login_gate，判据是源差异）。

编排层按源形态直接继承共享骨架，别重写机械件（详情骨架 / 批次去重 /
判登 cookie 读取都已收口）：
- 纯订阅聚合（RSS / Telegram 形）：继承 core.source.SubscribedSource——
  feeds 加法合成、「全部」流的四级错误出口全在基类，子类只注入
  _fetch_merged / _open_one 两个差异
- 详情一次拉全树（HN / Reddit / V2EX 形）：楼层/评论 loader 继承
  core.source.SliceDetailLoader，翻页切片零代码
- 正文随列表带回（RSS / Telegram 形）：详情用 core.source.StaticDetailLoader，
  进详情零请求
- 讨论树深层拍平：core.tree（@ 父作者前缀规则全库唯一实现）

依赖方向只允许 __init__ -> fetch -> parse -> core（parse 永远是最底层叶子）。
"""
from feedscope.sources import hn  # noqa: F401
from feedscope.sources import reddit  # noqa: F401
from feedscope.sources import rss  # noqa: F401
from feedscope.sources import telegram  # noqa: F401
from feedscope.sources import v2ex  # noqa: F401
from feedscope.sources import x  # noqa: F401
from feedscope.sources import zhihu  # noqa: F401
