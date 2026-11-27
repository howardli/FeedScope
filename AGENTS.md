# AGENTS.md — FeedScope 代理工作手册

给在本仓库里干活的工程代理（人或 LLM）看的：读完这一页就知道**东西在哪、边界在哪、
怎么验证**。给人类贡献者的英文说明在 `CONTRIBUTING.md`；两者与代码冲突时，以代码和
`tests/test_architecture.py` 为准（本页末尾的模块地图有守卫，不会与代码漂移）。

## 0. 先跑门禁

```bash
uv sync
uv run ruff check src/ tests/ scripts/   # 风格 + 导入纪律
uv run mypy                              # src 全量 strict，零 type: ignore
uv run pytest tests/                     # 全离线，约 2 分钟
```

三条都绿才算完。改动再小也跑全量：TUI 的时序回归常常只在别的模块的用例里现形
（列表改宽的重排曾把详情屏刚写的状态行盖掉，只有账号动线的用例抓得到）。
覆盖率门槛 88%（`uv run pytest tests/ --cov`）。

提交前自查：新增行为有测试、用户可见的改动进了 `CHANGELOG.md` 的 `[Unreleased]`、
动了界面就重跑 `scripts/screenshot.py` 刷新 `docs/*.png`。

## 1. 分层与依赖方向

```
cli ──► commands ──► services ──► sources ──► core
          │tui 命令      │            (插件)      (地基)
          ▼              ▼
          ui ────────────┘          （ui 只经 services 用源；不 import 具体源）
```

| 层 | 目录 | 可以 import | 一句话 |
|---|---|---|---|
| 入口 | `cli.py` | 任意 | 装配 parser、按命令名分发；不实现任何命令 |
| 命令 | `commands/` | commands / services / sources / core | 一个子命令一个模块：`NAME` + `register(sub)` + `run(args)`；只有 `tui.py` 碰 ui |
| 服务 | `services/` | services / sources / core | CLI 与 TUI 共用的跨源业务：登录、订阅管理、OPML。不认识具体源的内部实现 |
| 界面 | `ui/` | ui / services / core | Textual 屏与支撑件；`screens/` 依赖支撑件，支撑件不依赖 `screens/`（要屏做事就 `post_message`） |
| 源 | `sources/<id>/` | core + 本包 | 一个站点一个包：`__init__.py`（编排）→ `fetch.py`（取数）→ `parse.py`（纯函数叶子） |
| 地基 | `core/` | core | 模型、契约、配置与落盘、取数通道、文本工具；谁都不许往上 import |

例外只有两处，都写在守卫里：`ui` 可以整包 `import feedscope.sources`（触发注册）；
`commands/tui.py` 是 TUI 的组装点。

## 2. 不变量（每条都有守卫：改规则先改 `tests/test_architecture.py`）

| 规则 | 守卫 |
|---|---|
| 依赖只指向下方；包根只有 `cli.py` 与五个分层包 | `test_dependencies_only_point_downward` / `test_every_module_belongs_to_a_declared_layer` |
| 模块间 import 无环（函数内的惰性导入也算依赖） | `test_no_import_cycles` |
| 依赖写在模块顶部；函数内 import 只限白名单（playwright / fcntl / 可导入性探测 / tui 的 Textual） | `test_function_level_imports_are_allowlisted` |
| 不 import 别的模块的下划线私有名（`scripts/` 对 `src/` 同理） | `test_no_cross_module_private_imports` |
| `parse.py` 是纯函数叶子：只依赖 core、不碰网络、不 import 同包 fetch | `test_parse_is_pure_leaf` |
| `parse.py` 的公开函数一律 `parse_` 前缀，其余加 `_` 留作私有 | `test_parse_public_api_is_parse_prefixed` |
| 源的 `__init__.py` 只有一个 `@register` 的 Source 子类；Loader 住 `fetch.py` | `test_source_init_declares_only_the_source_class` |
| UI 不读 `FeedItem.extra`（源私有上下文），不 import 具体源，支撑件不 import 屏幕 | `test_ui_never_reads_item_extra` / `test_ui_does_not_bypass_business_layer` / `test_ui_support_modules_do_not_import_screens` |
| 配置落盘只走 `core.config._write_config`；注册表只经 `@register` / `install_sources` / 查询函数 | `test_config_write_path_is_single` / `test_registry_is_touched_via_public_api_only` |
| 命令模块与 `commands.ALL` 一一对应，`NAME` = 文件名，顺序 = `--help` 顺序 | `test_commands_follow_the_module_contract` |
| 测试树镜像 src 分层；测试模块之间不互相 import | `test_test_tree_mirrors_source_tree` / `test_tests_do_not_import_other_test_modules` |
| 模块 ≤ 750 行、函数 ≤ 90 行（`src/` 与 `scripts/` 同限；豁免要在守卫里登记理由） | `test_complexity_budgets` |
| 本页的模块地图与磁盘双向一致 | `test_agents_module_map_matches_tree` |
| 用户/第三方文本不得被当成 Rich 标记解析 | `tests/ui/test_markup_sinks.py` |
| UI 文案全部经 i18n；各语言键树同形、占位符一致 | `tests/test_i18n_no_hardcoded.py` / `tests/core/test_i18n.py` |
| 源的能力声明基线（登录 / 评论 / 订阅 / 注册顺序） | `tests/sources/test_source_caps.py` |

守卫拦住你时，先读它的 docstring——例外与理由都写在那里。**不要为了让代码过而放宽
守卫**；确实是结构演进，改守卫常量与注释，并在提交说明里写明。

## 3. 约定

**命名与结构**

- 一个模块一件事，模块 docstring 第一行就是它的职责（模块地图直接取这一行）
- 公开名 = 给别的模块用；带 `_` = 模块私有。跨模块要用的东西就给它公开名字
- 常量与函数回到它们所属的模块，不设「杂物袋」模块（`core/const.py` 已溶解）
- 同一件事只有一份实现：原子写 `core/jsonio`、去重 `core/loaders.dedup_by`、
  拍平 `core/tree`、判登 `Source.logged_in`、列表步进 `ui/list_render.step_to`

**源插件**（`sources/<id>/`）

- `parse.py`：raw dict/HTML → 模型，纯函数；公开入口 `parse_*`，直接用夹具测
- `fetch.py`：httpx 或常驻通道页签取数；持资源的 Loader 住这里
- `__init__.py`：`@register` 的 Source 子类，只做能力声明与 `open_*` 分派；
  首屏出口过 `require_feed_items`（或 `static_feed` / `sliced_feed` / `first_batch`）
- 需要登录：声明 `login_url` / `login_cookie` / `logout_cookies`，取数入口先
  `await self.require_login(...)`；cookie 在不在不可靠的源覆写 `logged_in()`
- 可订阅：`subscribable = True` + 实现三个钩子（`subscription_input` /
  `check_subscription` / `probe_subscription`）+ 声明四个文案键
  （`sub_placeholder` / `sub_hint` / `sub_empty` / `sub_added`）——`store_for` 自动有
- 在 `sources/__init__.py` 按 `source_id` 字母序挂一行 import（= Tab 顺序）

**错误与日志**

- 源侧失败抛 `SourceError`（消息是面向用户的当前语言文案）；要判语义用
  `status` 字段，绝不匹配消息文本。未登录抛 `LoginRequired`
- 订阅业务失败抛 `SubscriptionError`；文件用法错误抛 `BadSubscriptionFile`（CLI 退 2）
- 每个模块 `logger = logging.getLogger(__name__)`；宽捕获处写明为什么吞

**界面**

- 文案一律 `t("a.b.c", **kwargs)`；无点的字符串直通（品牌名 / 用户数据）
- 键位全用非打印键（字母/数字会被中文输入法拦进候选框）；`Binding` 的 description
  存文案键 `bindings.<action>`，挂载时 `retranslate_bindings(self)`；F1 帮助页自动收集
- 用户/第三方文本进 Textual：纯文本出口 `markup=False`，或用
  `feedscope.ui.markup.escape` 转义（别用 `rich.markup.escape`）
- 控件不认识屏：要屏做事就 `post_message`（见 `ui/widgets.py`）
- 主屏家族：状态各层自己初始化自己声明的，跨层的动线抽成协作者
  （`ui/screens/main_feed.py` 的 `FeedFlow`、`main_resume.py` 的 `ResumeTracker`）；
  归属表在 `ui/screens/__init__.py` 的 docstring，新状态先想清楚归谁

**注释与测试**

- 注释用中文，解释**为什么**（约束、踩过的坑、被否掉的方案），不复述代码
- 测试全离线：HTTP 走 `tests/support/http.py` 的 MockTransport，通道页签走
  `tests/support/tabs.py`，TUI 用 `tests/support/stubs.py` 的桩源 + Textual Pilot
- 测试放在镜像目录（见下）；跨文件夹具进 `tests/sources/<id>/fixtures.py`，
  跨模块工具进 `tests/support/`，子树 fixture 进该子树的 `conftest.py`

## 4. 常见任务怎么改

| 要做的事 | 步骤 |
|---|---|
| **站点改版，修解析器** | `uv run feedscope probe <id>`（默认把原始响应落盘到 `FEEDSCOPE_SAMPLE_DIR`）→ 存成夹具 → 只改 `sources/<id>/parse.py` → `tests/sources/<id>/test_parse.py` 先红后绿 |
| **加一个源** | 建 `sources/<id>/{__init__,fetch,parse}.py`（模板见 `sources/__init__.py` 的 docstring）→ `sources/__init__.py` 挂一行 import → `core/i18n/*.toml` 加 `sources.<id>.*` 文案 → `tests/sources/<id>/test_parse.py`（+ `test_source.py`）→ 需要新能力组合时在 `tests/sources/test_source_caps.py` 加一条。core / services / ui 零改动 |
| **加一个 CLI 子命令** | `commands/<name>.py`（`NAME` / `register` / `run`，形状见 `commands/base.py`）→ `commands/__init__.py` 的 `ALL` 登记一行（顺序 = help 顺序）→ `core/i18n/*.toml` 加 `cli.help_<name>` → `tests/commands/` |
| **加配置键** | `core/config.py`：读侧 `get_x`（坏值容错 + 记日志）/ 写侧 `save_x`（经 `_write_config`，其余原样保留）→ 需要体检的进 `lint()` → 更新 `config.file_header` 文案 → 两份 README 的配置节 |
| **加屏或键位** | `ui/screens/` 里改；`core/i18n/*.toml` 加 `bindings.*` → `tests/ui/` 用 Pilot 测 → README 键位表 + `scripts/screenshot.py` 刷新截图。新家族在 `ui/screens/__init__.py` 的 docstring 里登记 |
| **加文案** | `core/i18n/en.toml` 与 `zh-CN.toml` 同时加（键树必须同形）→ 两边都放在所在节的末尾，减少并行冲突 |
| **加语言 / 主题** | 复制 `core/i18n/en.toml` / `ui/themes/editor-dark.toml` 改内容，零代码 |

## 5. 并行开发：按任务划分的文件归属

同时开多个代理时，按任务类型认领路径；重叠的只有下面点名的「热点文件」。

| 任务 | 主要触碰 | 必跑 |
|---|---|---|
| 修某源解析 | `sources/<id>/parse.py`、`tests/sources/<id>/test_parse.py`、`fixtures.py` | `uv run pytest tests/sources/<id>` |
| 修某源取数 | `sources/<id>/fetch.py`、`tests/sources/<id>/test_fetch.py` | 同上 |
| 新源 | `sources/<id>/`、`sources/__init__.py`（1 行）、i18n、`tests/sources/<id>/` | 全量 |
| CLI 命令 | `commands/<x>.py`、`commands/__init__.py`（1 行）、i18n、`tests/commands/` | `uv run pytest tests/commands tests/test_architecture.py` |
| 屏与键位 | `ui/screens/`、i18n 的 `bindings.*`、`tests/ui/` | `uv run pytest tests/ui` |
| 核心模型 / 契约 | `core/`、`tests/core/`、`tests/test_architecture.py`、本页地图 | 全量 |
| 业务服务 | `services/`、`tests/services/` | `uv run pytest tests/services` |

**热点文件**（谁都会碰，改动保持最小、只追加）：`core/i18n/en.toml`、
`core/i18n/zh-CN.toml`、`sources/__init__.py`、`commands/__init__.py`、
`tests/sources/test_source_caps.py`、`CHANGELOG.md`、两份 README。

## 6. 别做的事

- 别放宽守卫、别加 `type: ignore`、别把 import 藏进函数、别跨模块拿私有名
- 别让模块长过预算——先想拆不拆（按职责拆模块，或抽纯函数下沉到该在的层）
- 别在 `parse.py` 里做 IO，别在 `ui` 里读 `extra`，别在 `services` 里 import 具体源
- 别硬编码 UI 文案，别把用户文本当 markup，别绑字母/数字键
- 别在测试里碰网络、真浏览器、系统剪贴板、真实用户目录（conftest 已把配置 /
  状态 / 缓存 / 日志都指进临时目录，新增落盘路径要照同样的方式隔离）
- 别手写原子写 / JSON 落盘（走 `core/jsonio`）；别绕过 `config` 直接读写
  `sources.toml`，别绕过 `core/store` 碰 `state.json` / `favorites.json`
- 不引第三方依赖除非 stdlib 明确不够；不支持 Windows（`fcntl`、本机 Chrome）

## 7. 模块地图

每个模块一行，取自模块 docstring。新增模块时在这里加一行（守卫会提醒你）；
顺手把 docstring 第一行写成一句完整的职责说明。

**包根**

| 模块 | 职责 |
|---|---|
| `src/feedscope/__init__.py` | 包根：只有 `__version__`（版本号的唯一出处）。 |
| `src/feedscope/__main__.py` | `python -m feedscope` = `feedscope` 命令。 |
| `src/feedscope/cli.py` | 命令行入口：装配子命令并分发；只含全局旗标与默认命令，不实现任何命令。 |

**commands/ — CLI 子命令，一个命令一个模块**

| 模块 | 职责 |
|---|---|
| `src/feedscope/commands/base.py` | 子命令模块的形状约定（NAME / register / run）与类型别名。 |
| `src/feedscope/commands/cache.py` | `feedscope cache [clear]`：列表缓存运维——无子命令 = 体检（目录/文件数/字节），clear = 清空。 |
| `src/feedscope/commands/doctor.py` | `feedscope doctor`：环境自检。逐项检查、逐项打印，退出码 0 = 全部通过。 |
| `src/feedscope/commands/favorites.py` | `feedscope favorites [export [path]]`：收藏列表。 |
| `src/feedscope/commands/login.py` | `feedscope login [source]`：有头 Chrome 手动登录（流程本体在 services/login）。 |
| `src/feedscope/commands/probe.py` | `feedscope probe`：源巡检——每个源的每个信息流拉一次首批，改版当天一条命令定位。 |
| `src/feedscope/commands/report.py` | doctor / probe 共用的 `[OK]/[FAIL]/[SKIP]/[WARN]` 状态行（不是命令）。 |
| `src/feedscope/commands/rss.py` | `feedscope rss …`：RSS 订阅管理——list / add / remove / rename / import / export。 |
| `src/feedscope/commands/tui.py` | `feedscope`（无子命令）/ `feedscope tui`：拉起终端界面。 |

**core/ — 地基：模型、契约、配置与落盘、取数通道、文本工具**

| 模块 | 职责 |
|---|---|
| `src/feedscope/core/browser.py` | 本机 Chrome + 持久 profile 的唯一入口：BrowserSession、常驻通道页签 ChannelTab、profile cookie、UA 修正。 |
| `src/feedscope/core/config.py` | `sources.toml` 的读写：启用的源、隐藏的流、各源订阅表、语言/主题/时区/桌面命令（坏配置绝不挡启动）。 |
| `src/feedscope/core/desktop.py` | 桌面桥：把链接交给浏览器、把文本放进剪贴板（可配置命令 > 自动探测 > OSC 52）。 |
| `src/feedscope/core/display.py` | 展示词汇的唯一出处：作者署名、计数折「万/K/M」、赞/点/评的文本拼装。 |
| `src/feedscope/core/errors.py` | 业务异常词汇（最底层叶子）：SourceError / LoginRequired / SubscriptionError / BadSubscriptionFile。 |
| `src/feedscope/core/feed_cache.py` | 列表缓存：每流一个 JSON 文件，重启先画缓存秒开、抓取失败时兜底不清屏。 |
| `src/feedscope/core/http.py` | 共享 HTTP 客户端池：httpx 源的统一取数通道，异常在边界包成 SourceError。 |
| `src/feedscope/core/jsonio.py` | 落盘原语：原子写（临时文件 + os.replace）与紧凑 JSON 文本的唯一实现。 |
| `src/feedscope/core/loaders.py` | 加载器契约与通用实现：FeedLoader / DetailLoader / CommentLoader 三个 ABC + Static*/Slice* 现成实现 + 批次去重。 |
| `src/feedscope/core/logsetup.py` | 日志装配：`--verbose` / `--log-file` 与默认落盘（TUI 绝不镜像 stderr）。 |
| `src/feedscope/core/media.py` | HTML → markdown 的共用管线：图片/视频换成 `[图片](地址)` 记号，HTML2Text 实例线程本地。 |
| `src/feedscope/core/models.py` | 核心数据模型：FeedItem 是「源 → UI」的唯一契约；Answer / Comment / DetailDoc / Floor。 |
| `src/feedscope/core/registry.py` | 源注册器：`@register` + 导入 sources 包 = 插件机制；只经公开函数读写。 |
| `src/feedscope/core/samples.py` | 样本落盘：`feedscope probe` 把取到的原始响应写盘，站点改版后照着修 parse。 |
| `src/feedscope/core/search.py` | 本地搜索：跨「列表缓存 + 收藏快照」筛条目（标题 + RSS/Telegram 正文），零请求。 |
| `src/feedscope/core/source.py` | Source 插件契约：能力声明、判登谓词与判登门、订阅三钩子、SubscribedSource 骨架、首屏 0 条守卫、「全部」聚合编排。 |
| `src/feedscope/core/store.py` | 已读 / 已见 / 收藏状态：`state.json`（读态 + 续读位置）与 `favorites.json`（收藏时的全量快照）。 |
| `src/feedscope/core/text.py` | 终端文本工具：显示宽度、按宽截断、零宽字符清理、正文链接提取。 |
| `src/feedscope/core/timefmt.py` | 时间展示格式化：各源的原始时间形态 → 展示时区的 `YYYY-MM-DD HH:MM` 与相对时间。 |
| `src/feedscope/core/tree.py` | 讨论树拍平：深层嵌套 → Comment 模型的两层（顶层 + replies），`@父作者` 前缀规则的唯一实现。 |

**core/i18n/ — 文案目录（`en.toml` / `zh-CN.toml`，键树必须同形）**

| 模块 | 职责 |
|---|---|

**services/ — 业务服务（CLI 与 TUI 共用）**

| 模块 | 职责 |
|---|---|
| `src/feedscope/services/login.py` | 登录流程：有头 Chrome 手动登录、登录轮询、`is_logged_in` / `logout`（判据 = Source.logged_in）。 |
| `src/feedscope/services/opml.py` | OPML 进出：读/解析/导入（幂等合并）/导出，订阅表与外部阅读器互通。 |
| `src/feedscope/services/subscriptions.py` | 订阅管理（源无关）：增删改名的通用编排 + 订阅管理屏的适配器 SubsStore。 |

**sources/ — 源插件，一个站点一个包**

| 模块 | 职责 |
|---|---|
| `src/feedscope/sources/hn/fetch.py` | 取数层：HN 走 Algolia 公开索引 API（免 key 免登录）——search 列表 + items/:id 整树。 |
| `src/feedscope/sources/hn/parse.py` | 解析层：Algolia JSON -> FeedItem / 楼层与评论树，纯函数不碰网络。 |
| `src/feedscope/sources/reddit/fetch.py` | 取数层：Reddit 走通道页签（知乎同款）——常驻 reddit.com 页签的页面 fetch。 |
| `src/feedscope/sources/reddit/parse.py` | 解析层：Reddit 网页 HTML（shreddit SSR / faceplate partial）→ FeedItem / 帖子与评论树；token_v2 的 JWT 判登解码。 |
| `src/feedscope/sources/rss/fetch.py` | 取数层：订阅抓取（httpx 条件请求）+「全部」聚合流（共享编排）。 |
| `src/feedscope/sources/rss/parse.py` | 解析层：RSS / Atom / JSON Feed -> FeedItem / 详情原料，纯函数不碰网络。 |
| `src/feedscope/sources/telegram/fetch.py` | 取数层：t.me/s 频道预览页抓取 + 「全部」聚合流（共享编排）。 |
| `src/feedscope/sources/telegram/parse.py` | 解析层：t.me/s 频道预览 HTML -> FeedItem / 正文 markdown，纯函数不碰网络。 |
| `src/feedscope/sources/v2ex/fetch.py` | 取数层：V2EX 走官方 v1 公开 API（免 token 免登录）——列表 + 全量回复。 |
| `src/feedscope/sources/v2ex/parse.py` | 解析层：v1 JSON -> FeedItem / 主题详情，纯函数不碰网络。 |
| `src/feedscope/sources/x/fetch.py` | X 取数层：通道页签（知乎/Reddit 同款）——常驻 x.com 页签的页面 fetch 直调 GraphQL。 |
| `src/feedscope/sources/x/parse.py` | X（Twitter）解析层：GraphQL URT 响应 -> FeedItem / 详情，纯函数，不碰网络与浏览器。 |
| `src/feedscope/sources/zhihu/fetch.py` | 知乎取数层：免签 API（常驻通道页签的页面 fetch + x-zse-93 版本头）；热榜 / 推荐 / 关注与问题、评论的 Loader。 |
| `src/feedscope/sources/zhihu/parse.py` | 知乎解析层：DOM 提取结果 / API JSON / HTML -> 统一模型，纯函数，不碰网络与浏览器。 |

**ui/ — TUI 支撑件（控件、布局度量、行渲染、弹层、反馈）**

| 模块 | 职责 |
|---|---|
| `src/feedscope/ui/account.py` | 账号动线：撞墙自动询问与 Ctrl+U 手动登录/登出的编排（从 app.py 拆出）。 |
| `src/feedscope/ui/app.py` | TUI 应用壳：主题/生命周期/全局键位与账号动线的宿主，主屏见 screens/main.py。 |
| `src/feedscope/ui/coalesce.py` | 合并式调度：背靠背触发只跑一次、在途再触发排一次重跑的 worker 收口。 |
| `src/feedscope/ui/dialogs.py` | 弹层底座与轻弹层：居中卡片 + 按钮行的骨架只写一次。 |
| `src/feedscope/ui/feedback.py` | 用户反馈统一出口：对用户的提示措辞与级别都在这里定。 |
| `src/feedscope/ui/focus_nav.py` | 弹层的方向键焦点移动：与主屏同一套「方向键管焦点」的键位语言。 |
| `src/feedscope/ui/labels.py` | 键位文案的 i18n 解析：类级 BINDINGS 的 description 存文案键，挂载时翻译。 |
| `src/feedscope/ui/launch.py` | 打开链接 / 复制链接的屏级封装：主列表、收藏屏、阅读屏家族三处共用。 |
| `src/feedscope/ui/layout.py` | 布局度量：列表屏与详情屏共用的可用宽度计算、各列宽度与 resize 防抖间隔。 |
| `src/feedscope/ui/list_render.py` | 列表行的渲染件与光标导航件：行怎么画、光标怎么走。 |
| `src/feedscope/ui/markup.py` | markup 字面量化：把用户/第三方文本安全地拼进 Textual 的 markup 字符串。 |
| `src/feedscope/ui/themes.py` | 主题加载器：ui/themes/ 下一个 TOML 一个主题，自动发现（镜像 core/i18n）。 |
| `src/feedscope/ui/widgets.py` | 主屏的配套部件：站点 tab、信息流边栏项、列表视图；不认识任何屏（要屏做事就发消息）。 |
| `src/feedscope/ui/wrap.py` | 显示折行：UAX #14 中文场景的规范子集。 |

**ui/screens/ — TUI 屏幕，按家族分文件**

| 模块 | 职责 |
|---|---|
| `src/feedscope/ui/screens/answer.py` | 回答详情屏：多层递进的第三层（列表 -> 问题 -> 回答+评论）。 |
| `src/feedscope/ui/screens/detail.py` | 问题详情屏：题目 + 描述 + 回答流，纯终端阅读。 |
| `src/feedscope/ui/screens/favorites.py` | 收藏屏（F3）：跨源收藏列表，数据全部来自本地快照（core/store.favorites）。 |
| `src/feedscope/ui/screens/help.py` | 帮助弹层（F1）：全局 + 按任务分组的当前屏键位表。 |
| `src/feedscope/ui/screens/language.py` | 语言选择弹层：首启设置与日常修改（Ctrl+L）共用一屏。 |
| `src/feedscope/ui/screens/links.py` | 正文链接选择器：阅读屏家族里纯键盘跟进链接的出口。 |
| `src/feedscope/ui/screens/local_list.py` | 本地列表屏的共享骨架：数据全在本地内存的跨源条目列表（收藏屏 / 搜索屏）。 |
| `src/feedscope/ui/screens/main.py` | 主屏叶子：compose 与样式、两侧导航的渲染、键位接线（取数上屏与续读位置在协作者）。 |
| `src/feedscope/ui/screens/main_feed.py` | 主屏的取数上屏协作者 FeedFlow：刷新 / Ctrl+N / 整批上屏 / 重排，独占 loader、新计数与三列宽。 |
| `src/feedscope/ui/screens/main_resume.py` | 主屏的续读位置协作者 ResumeTracker：回到上次停下的源与流，抓取定稿时定位选中，退出时落盘。 |
| `src/feedscope/ui/screens/main_nav.py` | 主屏继承链的导航层：三面板方向键导航与切站切流。 |
| `src/feedscope/ui/screens/main_read.py` | 主屏继承链的行集层：行集映射、过滤、已读同步与未读导航。 |
| `src/feedscope/ui/screens/main_setup.py` | 主屏继承链的根：启用集与当前位置（含其派生视图）+ 首启 / F2 的生效流程。 |
| `src/feedscope/ui/screens/reading.py` | 全屏阅读屏的共享骨架，两层收口： |
| `src/feedscope/ui/screens/search.py` | 本地搜索屏（主屏 F6）：跨全部列表缓存与收藏找条目。 |
| `src/feedscope/ui/screens/source_setup.py` | 源选择弹层：首启引导 + 日常源管理（F2）。 |
| `src/feedscope/ui/screens/subscriptions.py` | 订阅管理屏：F2 里 → 进入，列表 + 增删改名，改动即时落盘（源无关）。 |
| `src/feedscope/ui/screens/theme.py` | 主题选择弹层（Ctrl+T）：镜像语言选择屏的结构，但语义更简单——主题是 |

**数据目录**（不是模块）：`src/feedscope/core/i18n/*.toml` 文案目录；`src/feedscope/ui/themes/*.toml` 主题。

**`scripts/`**（不是包，同目录按模块名互相 import；同样受复杂度预算约束）：

| 脚本 / 模块 | 职责 |
|---|---|
| `scripts/screenshot.py` | README 截图：`walk_screens` 走一遍界面的固定键序（GIF 复用）+ 落盘 |
| `scripts/demo_gif.py` | README 演示 GIF：同一条键序收集帧，Pillow 合成 |
| `scripts/demo_sources.py` | 五个离线演示源 + `demo_environment()`（隔离用户环境、换进注册表） |
| `scripts/rasterize.py` | SVG 修正中文格宽 → 无头 Chrome 栅格化 |
| `scripts/x_probe.py` | X 通道诊断（只用公开 API：`harvest` / `shape_report`） |

## 8. 测试树

`tests/` 镜像上面的分层：`tests/<层>/test_<模块>.py`。

| 目录 | 放什么 |
|---|---|
| `tests/`（根） | `conftest.py`（全树生效的环境隔离 fixture）与两个跨切面守卫：`test_architecture.py`、`test_i18n_no_hardcoded.py`。不再放别的测试文件 |
| `tests/support/` | 跨模块共用的支撑件（不是测试）：`stubs.py` 桩源、`tabs.py` 通道页签替身、`http.py` HTTP 替身、`tui.py` Pilot 轮询、`env.py` 配置覆写 |
| `tests/core/` | 一个 core 模块一个文件（`test_source.py` = `core/source.py` 的守卫与契约，`test_loaders.py` = 加载器与聚合编排） |
| `tests/sources/<id>/` | `test_parse.py` / `test_fetch.py` / `test_source.py` + `fixtures.py`（跨文件夹具） |
| `tests/services/`、`tests/commands/` | 业务服务与子命令 |
| `tests/ui/` | Pilot 驱动的屏与键位测试；子树共享 fixture 在 `tests/ui/conftest.py` |
| `tests/scripts/` | `scripts/` 的导入冒烟 |
