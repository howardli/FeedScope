"""业务服务层：CLI 与 TUI 共用的跨源业务编排。

在 sources 之上、ui / commands 之下（方向由 tests/test_architecture.py 守着）：
服务经 Source 的声明与公开 API 使用源，自身不碰界面、不解析命令行——所以
同一个登录动线既能被 `feedscope login` 调，也能被 TUI 的 Ctrl+U 调。

    login.py           登录 / 判登 / 登出：有头 Chrome 手动登录 + profile cookie 判据
    subscriptions.py   订阅管理：增删改名的通用编排 + 订阅管理屏的源适配器
    opml.py            OPML 进出：订阅表与外部阅读器互通

服务不认识具体源的内部实现：源差异经 Source 的声明与钩子到达这里
（登录判据 Source.logged_in、订阅三钩子），所以新增一个源不需要改本包。
"""
