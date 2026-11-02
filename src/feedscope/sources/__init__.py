# ruff: noqa: I001  # 注册顺序 = Tab 顺序 = 启动默认源（知乎在前），故意不按字母排
"""源插件包：import 即注册（类装饰器 @register），import 顺序 = Tab 顺序。

新增一个源 = 按下面的模板建一个包 + 在这里挂一行 import，UI 与 core 零改动
（用户侧隐藏内置流走 sources.toml，见 core/config.py，不动这里）：

    sources/<id>/
      fetch.py      取数层：拿原始数据（httpx / 浏览器顶跳 / DOM 提取），
                    持资源的增量 Loader 也放这里；不碰 UI。
      parse.py      解析层：raw dict/HTML -> FeedItem/Answer/DetailDoc/markdown，
                    纯函数，直接可写 fixture 测试；不碰网络。
      __init__.py   编排层：@register 的 Source 子类，只做能力声明
                    （feeds/source_key/kind_labels/detail_kinds/登录声明）与 open_* 分派
                    （出口统一过 require_feed_items 首屏守卫），加无资源的内存分页 Loader。

依赖方向只允许 __init__ -> fetch -> parse -> core（parse 永远是最底层叶子）。
"""
from feedscope.sources import zhihu  # noqa: F401
from feedscope.sources import x  # noqa: F401
from feedscope.sources import rss  # noqa: F401
