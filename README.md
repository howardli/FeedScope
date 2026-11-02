# FeedScope（观流）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/howardli/FeedScope/actions/workflows/ci.yml/badge.svg)](https://github.com/howardli/FeedScope/actions/workflows/ci.yml)

> 终端里的信息流聚合阅读器——知乎、X（Twitter）、Hacker News、RSS 订阅一个终端全看。

**语言**：中文 · [English](README.en.md)

## 特性

- **多源聚合**：知乎热榜 / 推荐 / 关注，X 推荐 / 正在关注，HN 首页 / 最新 / Ask / Show，一个界面切换
- **RSS 订阅**：任意 RSS / Atom 源，「全部」流按时间合并（跨订阅去重、单源失败不拖垮），终端内读 feed 自带全文，条件请求刷新对源友好
- **终端内完整阅读**：信息流 → 问题 → 回答 → 评论，逐层按需加载
- **HN 讨论树**：顶层评论当楼层、回复树再深一层（`Enter`），深层嵌套拍平两层、`@父作者` 标出回复指向
- **X 外文推自动附 Grok 译文**，媒体记号 `[图片]` / `[视频]` 可点击直达原图 / 视频直链
- **登录闭环**：未登录自动弹 Chrome 扫码 / 输密码，`Ctrl+U` 一键登出换账号
- **源按需启用**：首启勾选，`F2` 随时增删，站点顺序 = 勾选顺序
- **编辑器风配色**：VS Code Dark+ 取向深色主题，久读不累
- **键位输入法友好**：全部为非打印键（Tab / 方向键 / 功能键），中文状态照常可用
- **改版自检**：解析出 0 条立即报「站点可能已改版」，`feedscope probe` 一条命令巡检定位

## 界面一览

知乎热榜（左侧信息流边栏，元信息列右对齐自适应）：

![知乎热榜列表](docs/screenshot-zhihu.png)

X 推荐时间线（署名「名字 @handle」完整可见，媒体记号可点击直达原图）：

![X 时间线列表](docs/screenshot-x.png)

X 推文详情（媒体直链、译文块、引用块）：

![X 推文详情](docs/screenshot-detail.png)

HN 首页（免登录即用，「N 点 · N 评 · 作者」就是讨论热度雷达）：

![HN 首页列表](docs/screenshot-hn.png)

HN story 详情（原文一行 + 顶层评论当楼层，楼层头带「N 条回复」引导）：

![HN 详情](docs/screenshot-hn-detail.png)

HN 评论的回复树（深层讨论拍平成两层，隔层回复带 `@父作者` 前缀保住对话指向）：

![HN 回复树](docs/screenshot-hn-thread.png)

## 安装

要求：Python 3.11+、本机已装 Chrome（无需 `playwright install`）、[uv](https://docs.astral.sh/uv/)（或 pip）。

```bash
git clone https://github.com/howardli/FeedScope.git
cd FeedScope
uv tool install --editable .   # 全局 feedscope 命令：任意目录可启动，改代码即生效
```

- 开发 / 测试：`uv sync` 后 `uv run feedscope`、`uv run pytest`
- 没有 uv：`pip install .`（非 editable，改代码需重装）
- PyPI 慢可配镜像：`export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`
- 装完跑 `feedscope doctor` 自检环境

## 使用

```bash
feedscope
```

### 键盘快捷键

| 按键 | 功能 |
| --- | --- |
| `Tab` / `Shift+Tab` | 切换站点 |
| `←` / `→` | 列表切信息流；详情换上 / 下一篇；回复层换楼层 |
| `↑` / `↓` | 移动选中项 / 滚动正文 |
| `Enter` | 进详情；详情屏内进「正在阅读」的那个楼层（回答 / 评论）的讨论层 |
| `Ctrl+N` | 加载更多（列表 / 楼层 / 评论，逐层各自生效） |
| `Ctrl+O` | 浏览器打开当前条目 |
| `F5` | 刷新 |
| `Ctrl+U` | 账号：登录 / 登出 |
| `F2` | 源管理：勾选启用 / 停用，保存即生效；`→` 进订阅管理（RSS） |
| `Esc` | 逐层返回 |
| `Ctrl+Q` | 退出（`Ctrl+C` 连按两次兜底） |

键位全部为非打印键，输入法中文状态照常可用；误把中文打进来会弹提示。顶部 tab 条与左侧边栏可点击，与键盘走同一条路径。

### 登录 / 登出

知乎与 X 需要登录（无匿名流）；HN 与 RSS 免登录开箱即用。未登录打开源会自动弹登录框，选「登录」弹 Chrome 完成后自动刷新，不用退出 TUI；取消后本次运行不再追问（`Ctrl+U` 仍可手动登录）。已登录按 `Ctrl+U` 登出换账号。纯命令行：`feedscope login [zhihu|x]`。

### 站点一览

| 站点 | 信息流 | 需登录 |
| --- | --- | --- |
| 知乎 | 热榜 / 推荐 / 关注 | ✅ |
| X（Twitter） | 推荐 / 正在关注 | ✅ |
| HN | 首页 / 最新 / Ask / Show | — |
| RSS | 全部 / 每订阅一流 | — |

### 子命令

```bash
feedscope [tui]           # 启动 TUI（默认）
feedscope login [源 id]   # 弹浏览器登录（默认 zhihu，如 x）
feedscope probe [源 id…]  # 全源巡检：定位改版，原始样本落盘
feedscope doctor          # 环境自检（Chrome / profile / 配置）
feedscope rss add <url> [--key k --label 名]  # 加订阅：feed 直链 / 站点首页（自动发现）/ OPML 路径
feedscope rss list | remove <key>             # 查看 / 删除订阅
feedscope rss import <x.opml> | export [x.opml]  # OPML 批量迁入（幂等合并）/ 导出
```

### 源与信息流配置

首次启动先弹勾选引导；之后 `F2` 随时增删。勾选落盘到用户配置（Linux 为 `~/.config/feedscope/sources.toml`，`FEEDSCOPE_CONFIG` 可换路径），也可手动编辑，改完重启生效：

```toml
enabled = ["zhihu", "x", "hn", "rss"]  # 书写顺序即站点排列顺序；去掉某项 = 停用该源

[zhihu]
hide = ["follow"]    # 隐藏不想看的内置流

[rss.feeds]          # RSS 订阅表：key 即流名（避免与保留字 all 撞名）
ruanyq = { label = "阮一峰周刊", url = "https://www.ruanyifeng.com/blog/atom.xml" }
hnrss = { url = "https://hnrss.org/newest?q=rust" }   # label 缺省用 key（关键字订阅仍可走 hnrss，HN 本站用内置源）

[rss]
hide = ["all"]       # 可隐藏「全部」聚合流，或填订阅 key 隐藏单个订阅
```

`enabled` 缺省 = 全部启用；管理界面保存会保留各源 `hide` 段与 `[rss.feeds]` 订阅表。调整站点顺序：`F2` 里「取消勾选再重新勾选」即移到末尾。

### RSS 订阅

订阅管理三条路，任选：

- **TUI 内（F2）**：`F2` 源管理里光标移到 RSS 行按 `→` 进订阅管理屏——`Insert` 添加、`Enter` 改名、`Delete` 删除、`↑↓` 选择，改动即时落盘；返回后 RSS 自动勾上，保存即生效
- **子命令**：`feedscope rss add / list / remove` 增删查；从别的阅读器迁移用 `feedscope rss import 订阅.opml`（Feedly / Inoreader / NetNewsWire 等导出的 OPML 都认，已订阅的自动跳过）；`export` 随时可带走订阅防锁定
- **手编配置**：直接改 `[rss.feeds]` 表（见上节示例），加完 `F2` 勾选 RSS 启用

添加入口的便利设计（TUI 添加框与 `rss add` 行为一致）：

- 地址粘 RSS/Atom 直链，**或直接粘站点首页**——自动发现页面上声明的 feed
- 粘**本地 OPML 文件路径** = 批量导入（从 Finder 把文件拖进终端即得路径）
- 显示名缺省拉一次 feed 标题；add / import 会自动把 RSS 加进启用列表，不用再开 F2

零订阅时 RSS 不出现在源列表（F2 里仍可选中它进订阅管理添加第一条）。行为要点：

- 「全部」流 = 全部订阅按发布时间倒序合并，同文多源（转载 / 聚合）按 URL 去重；单个订阅拉取失败只损失自己的条目，全部失败才报错
- 详情读 feed 自带正文（`content` 全文优先，`summary` 兜底），图片转原图直链；摘要型源正文短，`Ctrl+O` 跳浏览器读全文
- 刷新走条件请求（ETag / Last-Modified），源没变不重复传正文
- RSS 无登录、无评论层

### Hacker News

免登录免 key，数据走 [Algolia 公开索引 API](https://hn.algolia.com/api)（HN 官网站内搜索同源）。行为要点：

- 三层结构与知乎同形：story（链接帖正文 = 原文一行，Ask/Show 带自述全文）→ 顶层评论当楼层（Enter 进单条评论的回复树）
- 元信息列「N 点 · N 评 · 作者」；深层嵌套讨论拍平成两层，隔层回复带 `@父作者` 前缀保住对话指向
- 索引有分钟级延迟，列表按热度排序（非官方榜序）；无回复的楼层不预告 Enter

## 免责声明

本项目通过本机浏览器会话访问知乎与 X 的网页接口，仅供个人学习与研究：

- 请遵守所在地区法律法规及两站用户协议，账号风控风险自担
- 不带任何内置凭据，登录态只存于本机浏览器 profile，不与第三方共享
- 站点改版导致失效属正常损耗（`feedscope probe` 可巡检定位）；请勿用于批量抓取或商业用途

## 许可证

[MIT](LICENSE)
