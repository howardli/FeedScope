"""测试包：目录镜像 src/feedscope 的分层（core / sources / services / commands / ui）。

每个子目录是一个包，所以不同层的同名文件（各源都有 test_parse.py）不冲突；
跨模块共用的东西在 tests/support/，子树共享的 fixture 在该子树的 conftest.py。
"""
