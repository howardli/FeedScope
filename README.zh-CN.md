# FeedScope（观流）

[![PyPI](https://img.shields.io/pypi/v/feedscope.svg)](https://pypi.org/project/feedscope/)
[![Python](https://img.shields.io/pypi/pyversions/feedscope.svg)](https://pypi.org/project/feedscope/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/howardli/FeedScope/actions/workflows/ci.yml/badge.svg)](https://github.com/howardli/FeedScope/actions/workflows/ci.yml)

> 终端里的信息流聚合阅读器——知乎、X（Twitter）、Reddit、Hacker News、V2EX、RSS 与 Telegram 频道，一个终端全看。

**语言**：中文 · [English](README.md)

## 动图演示

知乎热榜 → X 时间线 → 推文详情（自动译文、引用块）→ Reddit → 帖子详情 → HN → story 详情 → 回复树 → V2EX → 主题详情——全程只是 `Tab` 与 `Enter`：

![FeedScope 演示](docs/demo.gif)

## 特性

- **多源聚合**：知乎热榜 / 推荐 / 关注，X 推荐 / 正在关注，Reddit 首页 / 热门 / 自选 subreddit，HN 首页 / 最新 / Ask / Show，V2EX 热门 / 最新，Telegram 频道订阅，一个界面切换
- **RSS 订阅**：任意 RSS / Atom 源，「全部」流按时间合并（跨订阅去重、单源失败不拖垮），终端内读 feed 自带全文，条件请求刷新对源友好
- **终端内完整阅读**：信息流 → 问题 → 回答 → 评论，逐层按需加载
- **已读与未读**：行首 `●` 标未读，打开即灭（`Ctrl+R` 可改回），刷新/重启都不动它；「N 新」计数在顶部状态行；列表缓存本地，重启秒开、断网可翻（RSS / Telegram 详情离线可读）
- **收藏/稍后读**：`Ctrl+F` 收藏（列表或阅读中都行），`F3` 看跨源收藏列表；存全量快照——条目从热榜流走、回答被删之后仍可读，`feedscope favorites export` 随时带走防锁定
- **HN 讨论树**：顶层评论当楼层、回复树再深一层（`Enter`），深层嵌套拍平两层、`@父作者` 标出回复指向
- **X 外文推自动附 Grok 译文**，媒体记号 `[图片]` / `[视频]` 可点击直达原图 / 视频直链
- **登录闭环**：未登录自动弹 Chrome 扫码 / 输密码，`Ctrl+U` 一键登出换账号
- **源按需启用**：首启勾选，`F2` 随时增删，站点顺序 = 勾选顺序
- **中英双语**：首启选语言（跟随系统预选），`Ctrl+L` 随时改（重启生效）；文案按语言分文件维护，新增语言放一个 TOML 即被自动发现
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

Reddit 热门（元信息列「N 点 · N 评 · r/版块」，subreddit 订阅在 F2 里加）：

![Reddit 热门列表](docs/screenshot-reddit.png)

Reddit 帖子详情（markdown 自述正文 + 评论楼层带赞数，「[置顶]」标出版务帖）：

![Reddit 详情](docs/screenshot-reddit-detail.png)

HN 首页（免登录即用，「N 点 · N 评 · 作者」就是讨论热度雷达）：

![HN 首页列表](docs/screenshot-hn.png)

HN story 详情（原文一行 + 顶层评论当楼层，楼层头带「N 条回复」引导）：

![HN 详情](docs/screenshot-hn-detail.png)

HN 评论的回复树（深层讨论拍平成两层，隔层回复带 `@父作者` 前缀保住对话指向）：

![HN 回复树](docs/screenshot-hn-thread.png)

V2EX 热门（免登录，元信息列「节点 · N 回复 · 用户名」扫一眼就是板块和热度）：

![V2EX 热门列表](docs/screenshot-v2ex.png)

V2EX 主题详情（markdown 正文 + 回复平铺当楼层，楼层号对齐站内楼层文化）：

![V2EX 主题详情](docs/screenshot-v2ex-detail.png)

## 安装

要求：Python 3.11+、本机已装 Google Chrome（无需 `playwright install`）。支持 macOS 与 Linux；Windows 暂不支持。

```bash
pipx install feedscope        # 或：uv tool install feedscope
```

从源码安装（开发）：

```bash
git clone https://github.com/howardli/FeedScope.git
cd FeedScope
uv sync                            # 开发 / 测试环境
uv tool install --editable .       # 全局 feedscope 命令：任意目录可启动，改代码即生效
```

- 装完跑 `feedscope doctor` 自检环境
- PyPI 慢可配镜像：`export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`
- 开发 / 测试：`uv run feedscope`、`uv run pytest`——详见 [CONTRIBUTING](CONTRIBUTING.md)

## 使用

```bash
feedscope
```

### 键盘快捷键

| 按键 | 功能 |
| --- | --- |
| `Tab` / `Shift+Tab` | 切换站点（切后键盘先落在左侧边栏挑流，`Enter` 回列表） |
| `←` / `→` | 区域移动：列表 `←` 进边栏（停当前流）、`→` 抵右缘；边栏 `→` 回列表、`←` 去源栏；源栏就地切站；详情换上 / 下一篇；回复层换楼层 |
| `↑` / `↓` | 移动选中项 / 滚动正文；任何区域的首行再 `↑` 都上探顶部源栏 |
| `Enter` | 进详情；详情屏内进「正在阅读」的那个楼层（回答 / 评论）的讨论层；边栏 / 源栏聚焦时回列表 |
| `Ctrl+N` | 加载更多（列表 / 楼层 / 评论，逐层各自生效） |
| `Ctrl+O` | 浏览器打开当前条目 |
| `Ctrl+R` | 当前条目已读 / 未读切换（想重看的标回未读） |
| `Ctrl+F` | 收藏 / 取消收藏当前条目（阅读中也生效） |
| `F3` | 收藏列表：跨源按收藏时间倒序，Enter 进详情，Ctrl+F 取消 |
| `F5` | 刷新 |
| `Ctrl+U` | 账号：登录 / 登出 |
| `Ctrl+L` | 界面语言：选择后保存，重启 `feedscope` 生效 |
| `F2` | 源管理：勾选启用 / 停用，保存即生效；`→` 进订阅管理（RSS / Reddit / Telegram） |
| `Esc` | 逐层返回（边栏 / 源栏聚焦时先回列表） |
| `Ctrl+Q` | 退出（`Ctrl+C` 连按两次兜底） |

键位全部为非打印键，输入法中文状态照常可用；误把中文打进来会弹提示。顶部 tab 条与左侧边栏可点击，与键盘走同一条路径。方向键按 CLI 惯例处理：`↑`/`↓` 在当前列表里移动选中；任何区域的首行再 `↑` 都上探源栏（顶部区域在所有内容之上），源栏 `↓` 下探边栏（与边栏首流 `↑` 互逆）；其余区域移动走横向——源栏里 `←`/`→` 就地切站，边栏里 `↑`/`↓` 就地切流、`→` 回列表、`←` 去源栏，列表里 `←` 进边栏（停当前流）、`→` 抵右缘；`Enter` / `Esc` 随时回列表。弹层（源管理 / 订阅管理 / 输入 / 确认 / 账号）里方向键同样移动焦点：列表到底再 `↓` 进按钮行、`↑` 返回列表、`←`/`→` 在按钮间平移、`Enter` 按下聚焦的按钮。

### 已读与离线

行首圆点标记阅读状态：`●` 未读（编辑器蓝）、空白 = 已读。未读是持久状态——刷新、切流、重启都不会动它，圆点只随「打开」消失（`Enter` 进详情、`Ctrl+O` 开浏览器、详情屏 `←`/`→` 换篇都算）；`Ctrl+R` 手动切换当前条目，想重看的标回未读。本次刷新首见几条会计数在顶部状态行（「N 条 · M 新」），不做行级标记。

收藏（`Ctrl+F`）存的是**收藏那一刻的全量快照**（标题/链接/元信息，RSS / Telegram 连正文一起）——条目从热榜流走、回答被删之后收藏仍可读；`F3` 列表读快照零请求、离线可用，Enter 才按原源在线抓最新评论。收藏条目豁免已读状态的淘汰；`feedscope favorites` 终端看列表、`feedscope favorites export [x.md]` 导出 Markdown 随时带走。

状态与缓存都是本机纯文本文件（删掉即重置，不含任何凭据）：

| 文件 | 内容 |
| --- | --- |
| `state.json`（状态目录，Linux 为 `~/.local/state/feedscope/`） | 已读/已见/收藏条目 id 与时间戳（上限一万条按最旧淘汰；收藏条目豁免） |
| `favorites.json`（同状态目录） | 收藏条目的全量快照（用户数据，永不淘汰） |
| `feeds/`（缓存目录，Linux 为 `~/.cache/feedscope/feeds/`） | 每流一个 JSON 的列表缓存（每流 200 条、留最近 60 流） |

启动或刷新先画缓存再抓新数据：重启秒开列表；断网或站点改版时旧内容保留可翻，RSS / Telegram 的详情正文随列表缓存离线可读。`feedscope doctor` 会列出状态文件位置。

### 界面语言

首启先弹语言选择（预选跟随系统语言，`Enter` 确认、`Esc` 沿用默认），之后 `Ctrl+L` 随时修改；修改保存到配置后**重启 `feedscope` 生效**（数字/单位等文案在抓取时就随语言烙进数据，语言随进程固定是最稳的模型）。**未配置语言时默认英文**——中文用户首启直接回车选简体中文，或 `Ctrl+L` 随时切换。子命令（`doctor` / `probe` / `rss` / `login`）的输出与 TUI 同语言：

```toml
language = "zh-CN"   # 或 "en"（缺省）；删掉这行 = 回到默认英文（只有全新安装会重新弹选）
```

新增一种语言：复制 `src/feedscope/core/i18n/zh-CN.toml` 为 `<语言码>.toml`（如 `ja.toml`），改 `[meta]` 里的自称（`name = "日本語"`）并翻译文案——语言选择屏自动发现它，零代码改动。两个语言文件的键树由测试强制一致（`tests/test_i18n.py` 奇偶校验），漏译会红；`src` 里硬编码中文文案同样被测试拦下（`tests/test_i18n_no_hardcoded.py`）。

### 登录 / 登出

知乎、X 与 Reddit 需要登录（无匿名流）；HN、V2EX、RSS 与 Telegram 免登录开箱即用。未登录打开源会自动弹登录框，选「登录」弹 Chrome 完成后自动刷新，不用退出 TUI；取消后本次运行不再追问（`Ctrl+U` 仍可手动登录）。已登录按 `Ctrl+U` 登出换账号。纯命令行：`feedscope login [zhihu|x|reddit]`。

登录态被服务端吊销（改密码、异地登出、定期轮换）不会让你对着 403 猜：X 的 401 语义唯一（清失效 cookie 后引导重登）；知乎、Reddit 与 X 的取数都走常驻浏览器页签的真 Chrome（TLS/头序/cookie 与登录会话完全自洽，不吃非浏览器画像的封锁）。Reddit 的判据是 token_v2 的 JWT 本地解码（登录态 `sub="user"`、匿名态 `sub="loid"`）——它的判登 cookie 匿名流量也发（打开登录页即有，cookie 出现 ≠ 登录完成），而本地解码零网络零伪造，「登录成功」和判登门都以此为准，反复要求登录的循环在结构上不可能。

### 站点一览

| 站点 | 信息流 | 需登录 |
| --- | --- | --- |
| 知乎 | 热榜 / 推荐 / 关注 | ✅ |
| X（Twitter） | 推荐 / 正在关注 | ✅ |
| Reddit | 首页 / 热门 / 自选 subreddit | ✅ |
| HN | 首页 / 最新 / Ask / Show | — |
| V2EX | 热门 / 最新 | — |
| RSS | 全部 / 每订阅一流 | — |
| Telegram | 全部 / 每频道一流 | — |

### 子命令

```bash
feedscope [tui]           # 启动 TUI（默认）
feedscope login [源 id]   # 弹浏览器登录（默认 zhihu，如 x、reddit）
feedscope probe [源 id…]  # 全源巡检：定位改版，原始样本落盘
feedscope doctor          # 环境自检（Chrome / profile / 配置）
feedscope rss add <url> [--key k --label 名]  # 加订阅：feed 直链 / 站点首页（自动发现）/ OPML 路径
feedscope rss list | remove <key>             # 查看 / 删除订阅
feedscope rss import <x.opml> | export [x.opml]  # OPML 批量迁入（幂等合并）/ 导出
feedscope favorites [export [x.md]]           # 收藏列表 / 导出 Markdown（防锁定）
```

### 源与信息流配置

首次启动先弹勾选引导；之后 `F2` 随时增删。勾选落盘到用户配置（Linux 为 `~/.config/feedscope/sources.toml`，`FEEDSCOPE_CONFIG` 可换路径），也可手动编辑，改完重启生效：

```toml
language = "zh-CN"  # 界面语言（首启 / Ctrl+L 维护）
enabled = ["zhihu", "x", "reddit", "hn", "v2ex", "rss"]  # 书写顺序即站点排列顺序；去掉某项 = 停用该源

[zhihu]
hide = ["follow"]    # 隐藏不想看的内置流

[reddit.feeds]       # subreddit 订阅表：key 即流名（r/home、r/popular 与内置流撞名的拒收）
rust = { label = "Rust", url = "https://www.reddit.com/r/rust/" }

[telegram.feeds]     # 频道订阅表：key 即流名（避免与聚合流名 all 撞名）
durov = { url = "https://t.me/durov/" }

[rss.feeds]          # RSS 订阅表：key 即流名（避免与保留字 all 撞名）
ruanyq = { label = "阮一峰周刊", url = "https://www.ruanyifeng.com/blog/atom.xml" }
hnrss = { url = "https://hnrss.org/newest?q=rust" }   # label 缺省用 key（关键字订阅仍可走 hnrss，HN 本站用内置源）

[rss]
hide = ["all"]       # 可隐藏「全部」聚合流，或填订阅 key 隐藏单个订阅
```

`enabled` 缺省 = 全部启用；管理界面保存会保留各源 `hide` 段与 `feeds` 订阅表（`[rss.feeds]` / `[reddit.feeds]` / `[telegram.feeds]`）。调整站点顺序：`F2` 里「取消勾选再重新勾选」即移到末尾。

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

### V2EX

免登录，数据走官方 v1 公开端点（hot / latest / replies，无需 token；[官方 API 参考](https://www.v2ex.com/help/api)）。行为要点：

- 两层结构：主题（markdown 正文，自动内联图片换 `[图片]` 记号、`@` 提及转可点击链接）→ 回复平铺当楼层；V2EX 回复没有嵌套结构（「楼中楼」只是正文里的 `@` 文字），故无第三层
- 元信息列「节点 · N 回复 · 用户名」；楼层号对齐站内楼层文化，楼层直达链接是原帖页的 `#reply` 锚点
- 官方接口一次给全量（列表一页、回复不分页），楼层翻页零请求；正文随列表透传，打开一篇详情只花 1 个请求

### X（Twitter）

需要登录，数据走网页端 GraphQL——请求由常驻浏览器页签的真 Chrome 发出（与知乎 / Reddit 同款通道：TLS、头序、cookie 与登录会话完全自洽，cf_clearance 之类的指纹封锁对页内 fetch 天然不存在）。接口的 queryId 随 X 部署轮换，本源以快照常量起步、**失效自动收割自愈**：报错时重开页签收集当前部署的 JS chunk、grep 出新 queryId 原参重试——X 改版从「跑脚本手抄常量」变成「当天自动恢复」。行为要点：

- 元信息列「名字 @handle」；外文推自动附 Grok 译文块，媒体换 `[图片]` / `[视频]` 记号可点击直达原图 / 视频直链
- 详情：焦点推全文 + 引用链展开，回复沿 ShowMore 游标翻页；转发（RT）自动跳到原推的会话
- 搜索不做：SearchTimeline 的 transaction-id 门禁只有 X 自己的 JS 会算头，页内 fetch 同样进不去

### Reddit

需要登录，数据走**网站自己的 HTML 通道**——`.json` 遗留端点 2026 年起对第三方全线 403，本源改吃新版前端自己用的路：列表页 SSR HTML 起步、翻页跟 `faceplate-partial` 的 load-after 链。请求由常驻浏览器页签的真 Chrome 发出（与知乎同款通道：TLS、头序、cookie 与登录会话完全自洽，不吃非浏览器画像的封锁）；`x-reddit-client-version` 从页面响应自动收割（403 时重割自愈）。行为要点：

- 三层结构与 HN 同形：帖子（自述帖 HTML 转 markdown、链接帖原文一行、媒体帖 `[图片]` / `[视频]` 记号直达原图）→ 顶层评论当楼层（`Enter` 进单条评论的回复树）
- 登录判据：token_v2 的 JWT 本地解码（登录态 `sub="user"`、匿名态 `sub="loid"`）——reddit_session 和 token_v2 的「存在」匿名流量也能造出来，解码才是铁证；登录轮询与判登门共用这一条，零网络零伪造
- 元信息列「N 点 · N 评 · r/版块」；楼层带赞数、版务置顶评论带 `[置顶]` 前缀；深层讨论拍平两层、`@父作者` 标出指向（同 HN）
- 「首页」是账号在 Reddit 站内关注版块的聚合（新账号没关注过版块时为空，属正常）；想看的版块用订阅：`F2` → Reddit 行按 `→`，输入 subreddit 名或地址（`rust`、`r/rust`、站点链接都认）即成单独的流，不必去 Reddit 网站关注
- 评论树单请求拉全（超大帖截断），截断处显示「另有 N 条未展开」；楼层 / 评论翻页全内存切片零请求

### Telegram 频道

免登录，数据走频道公开 Web 预览页（`t.me/s/<频道>`，服务端渲染 HTML，站长开启预览即可读）。行为要点：

- 与 RSS 同形的纯订阅源：`F2` → Telegram 行按 `→` 进订阅管理，输入频道名或链接（`durov`、`@durov`、`t.me/durov`、帖子直链都认），显示名缺省拉频道名
- 「全部」流 = 全部频道按发布时间倒序合并，单频道失败只损失自己的条目；每频道也各自成流（可 `hide` 单藏）
- 元信息列「N 观看」是频道帖的热度雷达；「全部」流里每行带源名列标频道名（与收藏列表同款，出处成列不挤元信息）；正文行内 HTML 转 markdown、图片/视频换 `[图片]` / `[视频]` 记号（视频无直链，记号指向帖子页）
- 详情读列表自带正文（零请求）；单频道流 `Ctrl+N` 走 `?before=` 游标往回翻历史帖；无评论层（频道讨论组在预览页拿不到）

## 社区

- **Bug 与功能**——[issues](https://github.com/howardli/FeedScope/issues)：bug 模板会请你贴 `feedscope doctor` 输出，几行带上版本与环境，省一个来回
- **提问与交流**——[discussions](https://github.com/howardli/FeedScope/discussions)
- **参与开发**——[CONTRIBUTING](CONTRIBUTING.md)：新增一个源被设计成只动一个包的活，新增一种界面语言只需一个 TOML 文件
- **安全**——[SECURITY.md](SECURITY.md)：登录 cookie 存在哪里、漏洞如何私密上报
- **更新日志**——[CHANGELOG.md](CHANGELOG.md)

状态：**0.x，活跃开发中**。配置格式在小版本间只增不破，破坏性变更等 1.0；站点改版随时可能弄断对应源——`feedscope probe` 定位，补丁版本跟进新解析器。

## 免责声明

本项目通过本机浏览器会话访问知乎、X 与 Reddit 的网页接口，仅供个人学习与研究：

- 请遵守所在地区法律法规及各站用户协议，账号风控风险自担
- 不带任何内置凭据，登录态只存于本机浏览器 profile，不与第三方共享
- 站点改版导致失效属正常损耗（`feedscope probe` 可巡检定位）；请勿用于批量抓取或商业用途

## 许可证

[MIT](LICENSE)
