"""克制的编辑器风深色主题：低饱和、灰阶层级，长时间阅读不刺眼。

色板对齐 VS Code Dark+ 的克制取向：
- 背景近黑灰（#1e1e1e）、前景浅灰（#d4d4d4），层级用 #252526/#2d2d30 的灰阶
- 全主题唯一强调色是低饱和编辑器蓝 #569cd6（选中/焦点/链接），选中态由
  Textual 从 primary 派生暗化色，贴近 VS Code 列表选中背景 #264f78
- 语义色全部用暗色版：错误 #f48771、警告 #dcdcaa、成功 #6a9955
- 不引入任何高饱和/荧光色

Textual 8 的 Theme 是数据类：这些色直接喂给 ColorSystem，
Header/Footer/Markdown/ListView 自动取色，无需逐组件写 CSS。两处派生
不合意、经 variables/CSS 覆写（后者见 ui/screens/reading.py 的 READING_CSS）：
- Markdown 标题取 primary 会整行变蓝（H5/H6 甚至 ansi_cyan）——抹平成
  段落样式，所有源的正文同一种格式；
- 链接缺省取前景灰，灰底灰字认不出可点——编辑器蓝只留给链接。
"""

from __future__ import annotations

from textual.theme import Theme

FEEDSCOPE_THEME = Theme(
    name="feedscope-editor",
    primary="#569cd6",  # 编辑器蓝：选中/高亮
    secondary="#5c6b7a",  # 次级钢蓝灰
    accent="#569cd6",  # 焦点环
    warning="#dcdcaa",  # 暗黄（VS Code 警告色）
    error="#f48771",  # 暗珊瑚红（VS Code 错误色）
    success="#6a9955",  # 暗绿（VS Code 注释绿）
    foreground="#d4d4d4",  # 编辑器前景
    background="#1e1e1e",  # 编辑器背景
    surface="#252526",  # 侧栏/面板
    panel="#2d2d30",  # 边框/分隔
    boost="#37373d",  # 悬浮
    dark=True,
    variables={
        "footer-key-foreground": "#9aa0a6",
        # 链接：缺省取前景灰（灰底灰字认不出可点），编辑器蓝只留给这里
        # （Markdown 标题的统一格式见 reading.py 的 READING_CSS，不占强调色）
        "link-color": "#569cd6",
        # 侧栏未聚焦选中带（VS Code 未聚焦选中同款灰）：boost 字段在 CSS 变量
        # $boost 里是 4% 半透明悬浮层（铺了看不见），实底只能走 variables
        "nav-sel-background": "#37373d",
    },
)
