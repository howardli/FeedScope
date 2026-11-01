# FeedScope（观流）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/howardli/FeedScope/actions/workflows/ci.yml/badge.svg)](https://github.com/howardli/FeedScope/actions/workflows/ci.yml)

> 终端里的信息流聚合阅读器——知乎、X（Twitter）一个终端全看。

**English** — FeedScope (观流, "watching the feed") is a terminal-first feed reader that aggregates Zhihu and X (Twitter) into one Textual TUI: full in-terminal reading (feed → question → answers → comments, loaded per level), in-app Chrome login/logout, automatic translations for foreign posts, and media direct links. UI is in Chinese; see below for details.

## 特性

- **双源聚合**：知乎热榜/推荐/关注、X（Twitter）推荐/正在关注时间线，一个界面切换。
- **源按需启用**：首次启动先选要用的源（勾选了才启用），之后随时 `F2` 增删改——停用的源不占站点栏，新装版本带来的新源也要勾选后才出现。
- **终端内完整阅读**：信息流 → 问题 → 回答 → 评论，层层递进、逐层按需加载（进评论层才请求，首载只抓回答）；非终端可读的类型（想法/文章/视频等）在抓取层即过滤，列表里只有问题/推文。知乎评论走官方分页接口（根评论 + 子回复一并拉全，`Ctrl+N` 一路翻到底），时间是绝对时间、表情贴纸转文本。X 外文推自动附 Grok 译文（与网页版自动翻译同源，译文随详情响应内嵌，零额外请求）。
- **编辑器风配色**：克制的 VS Code Dark+ 取向深色主题，低饱和、灰阶层级，长时间阅读不刺眼。
- **登录/登出闭环**：知乎/X 未登录撞墙时直接在 TUI 里弹登录框，选登录即弹 Chrome 扫码/输密码；`Ctrl+U` 一键登出换账号，无需退出。
- **改版自检**：信息流解析出 0 条立即报「站点可能已改版」而非静默空列表；`feedscope probe` 一条命令巡检全部源并留原始样本，修复解析器对着样本改。
- **全键盘操作**：Tab 切站、`←`/`→` 切流、方向键移动滚动，键位全部非打印键，输入法中文状态照常可用；鼠标点击与键盘走同一条路径。

## 界面一览

**知乎热榜**（左侧信息流边栏，元信息列右对齐自适应）：

![知乎热榜列表](docs/screenshot-zhihu.png)

**X 推荐时间线**（`x` 一键直达，署名「名字 @handle」完整可见）：

![X 时间线列表](docs/screenshot-x.png)

**推文详情**（正文带媒体直链、外文推自动附 Grok 译文块、被引推文引用块、外文回复附译文）：

![X 推文详情](docs/screenshot-detail.png)

## 安装

### 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python | 3.11+ |
| Google Chrome | 本机已装即可，程序通过 `channel="chrome"` 调用，**无需** `playwright install` 下载浏览器 |
| [uv](https://docs.astral.sh/uv/) | 推荐，用于装依赖与运行；也可用 `pip` |

### 安装

```bash
git clone https://github.com/howardli/FeedScope.git
cd FeedScope
uv sync                       # 开发环境（含 pytest/ruff 等开发依赖）
uv tool install --editable .  # 全局 feedscope 命令：任意目录可启动，改代码即生效
```

`uv sync` 装项目虚拟环境供开发/测试（`uv run feedscope`、`uv run pytest`）；`uv tool install --editable .` 建独立工具环境，把 `feedscope` 命令链到 `~/.local/bin`。editable 模式下日常改代码无需重装，**仅当 `pyproject.toml` 新增依赖**后需重跑 `uv tool install --editable . --force` 刷新工具环境。

国内网络访问 PyPI 官方 CDN 龟速时，可给自己的 uv 配置镜像（属个人环境配置，不进仓库）：

```bash
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple   # 或写进 ~/.config/uv/uv.toml
```

没有 uv 也可 `pip install .`（非 editable，改代码需重装）。

安装后可用 `feedscope doctor` 自检环境（Python 版本、Chrome、依赖、用户配置）。

## 使用

### 启动

```bash
feedscope               # 任意目录直接启动
uv run feedscope        # 等价（项目目录内，开发调试用）
```

### 登录 / 登出

- 知乎与 X 都需要登录（未登录打开即弹登录框，无匿名可用流）。
- 未登录时打开需要登录的源/信息流会**自动弹出登录框**，选「登录」即弹 Chrome 窗口扫码 / 输账号密码，完成后自动刷新，不用退出 TUI。选「取消」后该源本次运行内不再自动追问（常驻提示保留 `Ctrl+U` 登录入口）；登录成功或登出换账号后恢复自动询问。
- 已登录时按 `Ctrl+U` 弹登出框，一键登出换账号。
- 纯命令行场景仍可用子命令：`feedscope login`（默认知乎）、`feedscope login x`（X）。

### 键盘快捷键

| 按键 | 功能 |
| --- | --- |
| `Tab` / `Shift+Tab` | 切换站点 |
| `z` `x` | 一键直达知乎 / X（唯二字母键，需输入法在英文状态） |
| `→` / `←` | 列表屏内切换信息流；详情屏内换下一篇 / 上一篇；评论层内换下一条 / 上一条回答 |
| `↑` / `↓` | 移动选中项 / 滚动正文 |
| `Enter` | 进入终端详情（问题/推文）；详情屏内再按 Enter 进「视口正在阅读」的那个回答的评论层（知乎，滚到哪进哪，评论此刻才请求） |
| `Ctrl+N` | 加载更多（列表条目 / 详情回答 / 评论，逐层各自生效） |
| `Ctrl+O` | 浏览器打开当前条目 |
| `F5` | 刷新 |
| `Ctrl+U` | 账号：登录 / 登出当前源 |
| `F2` | 源管理：勾选启用 / 停用源，保存即生效 |
| `Esc` | 逐层返回（评论层 → 问题 → 列表） |
| `Ctrl+Q` | 任意界面一键退出（`Ctrl+C` 连按两次为兜底） |

**输入法在中文状态时**：快捷键全部为非打印键（方向键、`Enter`、`Esc`、`Tab`、F 键、`Ctrl` 组合），不会被输入法拦进候选框，无需切换输入法。唯二例外是 `z` / `x` 站点直达键（字母键；`Tab` 循环切站不受影响）；误把中文打进来时，应用会弹提示指路。

顶部站点 tab 条与左侧信息流边栏可点击，和键盘走同一条切站/切流路径；各站会记住上次浏览的信息流。

### 站点一览

| 站点 | 直达键 | 信息流 | 需登录 |
| --- | --- | --- | --- |
| 知乎 | `z` | 热榜 / 推荐 / 关注 | ✅ |
| X（Twitter） | `x` | 推荐 / 正在关注 | ✅ |

### 子命令

```bash
feedscope [tui]           # 启动 TUI（默认）
feedscope login [源 id]   # 弹浏览器窗口登录某源（默认 zhihu，如 x）
feedscope probe [源 id…]  # 巡检全部源：每流拉一次首批，改版当天定位；原始样本落盘
feedscope doctor          # 环境自检（Chrome / profile / 用户配置）
```

### 源与信息流配置

**首次启动**会先弹「选择要启用的源」：空格勾选（默认全选）、确认后进入主界面；取消则退出，下次启动再次引导。之后随时按 `F2` 打开源管理：勾选 = 启用（增加）、取消勾选 = 停用（删除）、保存即生效——将来版本新增的源也在这里勾选后才会出现。

**站点排列顺序 = 勾选顺序**：勾选的源实时标 `1、2、3…` 序号（即站点排列位置，取消勾选后序号自动重排）；想调整顺序，在 `F2` 管理里「取消勾选再重新勾选」即把它移到末尾。

选择会落盘到用户配置文件（也可手动编辑，改完重启生效）：

- Linux：`~/.config/feedscope/sources.toml`（macOS/Windows 由 platformdirs 决定；`FEEDSCOPE_CONFIG` 环境变量可换路径）
- 文件不存在就新建一个

```toml
# FeedScope 源配置：enabled = 启用的源；各源 hide = 隐藏的信息流
enabled = ["zhihu", "x"]  # 书写顺序即站点排列顺序；去掉某项 = 停用该源

[zhihu]
hide = ["follow"]    # 隐藏不想看的内置流
```

规则：`enabled` 缺省 = 全部启用（老配置文件只有 `hide` 的不受影响）；管理界面保存会保留各源 `hide` 段、丢弃无法识别的键；停用源的直达键按下会提示「未启用」。`feedscope doctor` 可查配置状态。

## 免责声明

本项目通过本机浏览器会话访问知乎与 X（Twitter）的网页接口，仅供个人学习与研究使用：

- 使用前请确保遵守所在地区法律法规及两站用户协议；账号因风控受限的风险由使用者自行承担。
- 本项目不带任何内置凭据，登录态只保存在你自己机器的浏览器 profile 里，不与任何第三方共享。
- 站点改版导致功能失效属正常损耗（`feedscope probe` 可巡检定位）；请勿用于批量抓取或商业用途。

## 许可证

[MIT](LICENSE)
