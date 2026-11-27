"""测试支撑件（不是测试，不被收集）：跨模块共用的替身、隔离环境与 TUI 轮询。

测试模块之间不互相 import——共用的东西要么在这里，要么在所在子树的
conftest.py（fixture）。按用途分模块：

    env.py    用户配置的按用例覆写
    http.py   core/http 的 MockTransport 替身
    tabs.py   敌意源（知乎 / Reddit / X）的通道页签替身
    stubs.py  桩源与桩 loader（驱动 TUI 与跨源列表的测试）
    tui.py    Textual Pilot 的轮询等待与通知读取
"""
