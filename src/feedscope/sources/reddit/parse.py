"""解析层：Reddit 网页 HTML（shreddit SSR / faceplate partial）-> FeedItem /
帖子与评论树，纯函数不碰网络。

2026-11 起 www 的 .json 遗留端点对第三方全线 403，本源改吃网站自己的
HTML 通道（请求由常驻 reddit.com 通道页签的真 Chrome 发出，见 fetch.py）：
- 列表：流页面 SSR（首页 / 热门 r/popular / 订阅 r/{sub}）+ 翻页跟随
  faceplate-partial slot="load-after" 的 src 链（feeds/*-feed 与
  community-more-posts 两族，src 里带全参数不用自己拼）
- 详情：帖子 permalink 页 SSR 自带整棵评论树（1.5MB 级，深度大的树被
  服务端截断，用「另有 N 条未展开」诚实提示，不续拉）

结构映射是 HN 式三层（词汇重映射，UI 零特判），语义与旧 JSON 版一致：
- <shreddit-post> 开标签属性即帖子全部元数据（id/post-title/score/
  comment-count/subreddit-name/permalink/post-type/content-href）
- 顶层评论 -> 楼层（Answer，unit「评论」）；楼层内回复树按 depth 属性
  重建 -> 评论层（Comment，unit「回复」）。Comment 模型只有两层而
  Reddit 常嵌四五层：照 HN 拍平——直接子回复做顶层，更深的拍平进
  replies；隔层回复加「@父作者」前缀
- 正文是结构化 HTML（rtjson 渲染产物），统一走 html_to_md（同 Telegram）

Reddit 特有语义：
- score：楼层与评论都有值
- [deleted]/[removed]：作者/正文的删除占位（作者栏 [已删除]、正文
  「（已删除）」；有回复的删除评论保留树形）
- 广告：promoted 帖不带 t3_ id（或 class 带 promoted），静默跳过
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from feedscope.core.const import safe_int
from feedscope.core.display import fmt_count, votes_text
from feedscope.core.media import attrs as tag_attrs
from feedscope.core.media import html_to_md, mark
from feedscope.core.models import Answer, Comment, FeedItem, Floor
from feedscope.core.timefmt import fmt_iso
from feedscope.core.tree import DELETED_AUTHOR, DELETED_BODY, TreeAccessors, build_floors

SOURCE_ID = "reddit"

_SITE = "https://www.reddit.com"

_DELETED_BODIES = {"[deleted]", "[removed]"}  # Reddit 原生删除态（作者/正文两栏的判定源）

# subreddit 名域（字母数字下划线）：下限放宽到 2——校验过严拒掉真订阅的
# 代价（加不上），比过松收进一个假名（页面校验会拦住）更高
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")

# 站内地址里的 /r/<名>/ 段（起点或前面是 / . = 才认，避免误吃普通词）
_R_PATH_RE = re.compile(r"(?:^|[/=.])r/([A-Za-z0-9_]+)/?")

# shreddit 开标签（自闭合形态不出现；\s 排除 shreddit-post-xxx 同前缀标签）
_POST_TAG_RE = re.compile(r"<shreddit-post\s[^>]*>")
_COMMENT_TAG_RE = re.compile(r"<shreddit-comment\s[^>]*>")
_PARTIAL_TAG_RE = re.compile(r"<faceplate-partial\s[^>]*>")

_SELF_TYPES = {"text", "self"}  # post-type -> kind 的两类映射（其余一律 link）
_MEDIA_TYPES = {"image", "video", "gallery"}


# ---- 列表 ----


def _post_meta(a: dict[str, str], *, with_author: bool = False) -> str:
    """帖子元信息列「N 点 · N 评 · r/版块（· u/作者）」：列表与详情共用。"""
    parts = [
        f"{fmt_count(safe_int(a.get('score')))} 点",
        f"{fmt_count(safe_int(a.get('comment-count')))} 评",
    ]
    if subreddit := str(a.get("subreddit-name") or ""):
        parts.append(f"r/{subreddit}")
    if with_author and (author := str(a.get("author") or "")):
        parts.append(f"u/{author}")
    return " · ".join(parts)


def parse_posts_html(html: str) -> list[FeedItem]:
    """页面/片段 HTML -> FeedItem 列表（保持文档序，广告与残条逐条防御跳过）。"""
    items: list[FeedItem] = []
    for m in _POST_TAG_RE.finditer(html):
        a = tag_attrs(m.group(0))
        pid = str(a.get("id") or "")
        title = str(a.get("post-title") or "").strip()
        if not pid.startswith("t3_") or not title:
            continue  # 广告（无 t3 id）或残条：跳条不跳流
        if "promoted" in str(a.get("class") or ""):
            continue
        subreddit = str(a.get("subreddit-name") or "")
        permalink = str(a.get("permalink") or "")
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=pid[3:],
                title=title,
                url=urljoin(_SITE, permalink) if permalink else f"{_SITE}/comments/{pid[3:]}",
                kind="self" if str(a.get("post-type") or "") in _SELF_TYPES else "link",
                meta=_post_meta(a),
                extra={"subreddit": subreddit},
            )
        )
    return items


def parse_next_html(html: str) -> str | None:
    """HTML -> 下一页 partial 的绝对 URL（faceplate-partial slot="load-after" 的 src）。

    没有该元素 = 服务端说到底了。src 自带全参数（after/distance/sort/...），
    直接整串跟走，不重新拼。
    """
    for m in _PARTIAL_TAG_RE.finditer(html):
        a = tag_attrs(m.group(0))
        if a.get("slot") == "load-after" and a.get("src"):
            return urljoin(_SITE, a["src"])
    return None


# ---- 帖子与评论树 ----


@dataclass
class ThreadDetail:
    """帖子 permalink 页的解析结果：帖子本体 + 全部楼层（含各自的回复树）。"""

    title: str
    meta: str
    time: str
    description_md: str
    floors: list[Floor] = field(default_factory=list)


@dataclass
class _CNode:
    """评论树的工作节点：属性 + 正文 + 子节点（depth 属性重建嵌套）。"""

    attrs: dict[str, str]
    body: str
    children: list[_CNode] = field(default_factory=list)

    @property
    def author(self) -> str:
        a = str(self.attrs.get("author") or "")
        return a if a and a not in _DELETED_BODIES else DELETED_AUTHOR

    @property
    def votes(self) -> str:
        return votes_text(safe_int(self.attrs.get("score")))


_DIV_TOKEN_RE = re.compile(r"<div\b|</div>")


def _slot_inner(segment: str, slot: str) -> str:
    """segment 里第一个 slot=<slot> 的 div 的内层 HTML（按 div 配平找闭合）。

    shreddit 的正文容器是 slot="comment" / slot="text-body" 的 div，内层
    是结构化 HTML（rtjson 产物）；嵌套 div 靠配平而不是非贪婪匹配。
    """
    m = re.search(rf'<div[^>]*slot="{slot}"[^>]*>', segment)
    if not m:
        return ""
    depth = 1
    for t in _DIV_TOKEN_RE.finditer(segment, m.end()):
        depth += 1 if t.group(0) == "<div" else -1
        if depth == 0:
            return segment[m.end() : t.start()]
    return ""


def _content(node: _CNode) -> str:
    """一条评论的正文：删除占位的兜底（@ 前缀由 core.tree 统一拼装）。"""
    return node.body or DELETED_BODY


def _comment_of(node: _CNode) -> Comment:
    return Comment(
        author=node.author,
        votes=node.votes,
        content_md=_content(node),
        time=fmt_iso(str(node.attrs.get("created") or "") or None),
        raw_id=str(node.attrs.get("thingid") or "").removeprefix("t1_"),
    )


def _keep(node: _CNode) -> bool:
    """空壳节点（被删且无回复的评论）整支跳过（规则见 core.tree）。"""
    return bool(node.body or node.children)


def _kids_of(node: _CNode) -> list[_CNode]:
    return node.children


def _author_of(node: _CNode) -> str:
    return node.author


# 差异注入：Reddit 的树是 HTML 按 depth 重建的 _CNode（拍平与 @ 规则在 core.tree）
_ACC = TreeAccessors(keep=_keep, kids_of=_kids_of, author_of=_author_of, comment_of=_comment_of)


def _answer_of(node: _CNode, n: int) -> Answer:
    """楼层本体（core.tree.build_floors 注入）：字段来源是站差异，n 为回复数。"""
    cp = str(node.attrs.get("permalink") or "")
    return Answer(
        author=node.author,
        votes=node.votes,
        content_md=_content(node),
        time=fmt_iso(str(node.attrs.get("created") or "") or None),
        url=urljoin(_SITE, cp) if cp.startswith("/") else "",
        comment_count=f"{n} 条回复" if n else "",  # 0 条不引导 Enter
    )


def _post_body(attrs: dict[str, str], html: str) -> str:
    """帖子正文：自述帖 HTML 转 markdown；媒体帖记号直达原物；链接帖一行原文。"""
    ptype = str(attrs.get("post-type") or "")
    if ptype in _SELF_TYPES:
        md = html_to_md(_slot_inner(html, "text-body"))
        return md or "（无正文）"
    href = str(attrs.get("content-href") or "")
    if ptype in _MEDIA_TYPES:
        label = {"image": "[图片]", "video": "[视频]", "gallery": "[相册]"}.get(ptype, "[媒体]")
        target = (
            urljoin(_SITE, href)
            if href
            else urljoin(_SITE, str(attrs.get("permalink") or ""))
        )
        return mark(label, target) if target else ""
    return mark("[原文]", urljoin(_SITE, href)) if href else ""


def parse_thread_html(html: str, fallback_title: str = "") -> ThreadDetail:
    """帖子 permalink 页 HTML -> ThreadDetail（野数据防御；评论缺失给空楼层）。"""
    post: dict[str, str] = {}
    m = _POST_TAG_RE.search(html)
    if m:
        post = tag_attrs(m.group(0))

    # 评论树：开标签按文档序拿属性与正文，depth 属性重建嵌套
    roots: list[_CNode] = []
    stack: list[tuple[int, list[_CNode]]] = []
    tags = list(_COMMENT_TAG_RE.finditer(html))
    for i, t in enumerate(tags):
        # 末条评论的正文区上界：200KB 足够容纳一条评论的 rtjson（再大只会
        # 白扫），配平逻辑保证越界内容也只是被丢弃而不是误吞
        end = tags[i + 1].start() if i + 1 < len(tags) else min(t.end() + 200_000, len(html))
        node = _CNode(
            attrs=tag_attrs(t.group(0)),
            body=html_to_md(_slot_inner(html[t.end() : end], "comment")),
        )
        depth = max(0, safe_int(node.attrs.get("depth")))
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1] if stack else roots).append(node)
        stack.append((depth, node.children))

    # 顶层节点 -> 楼层（keep 过滤 / 计数 / 拍平的骨架在 core.tree.build_floors）
    floors = build_floors(roots, _ACC, answer_of=_answer_of)
    # 截断口：服务端只渲染了前一部分楼层（大帖常见），按 comment-count 诚实提示
    total = safe_int(post.get("comment-count"))
    loaded = sum(1 + len(f.comments) + sum(len(c.replies) for c in f.comments) for f in floors)
    if floors and total > loaded:
        last = floors[-1].answer
        last.comment_count = " · ".join(
            x for x in (last.comment_count, f"另有 {fmt_count(total - loaded)} 条未展开") if x
        )
    return ThreadDetail(
        title=str(post.get("post-title") or "").strip() or fallback_title,
        meta=_post_meta(post, with_author=True),
        time=fmt_iso(str(post.get("created-timestamp") or "") or None),
        description_md=_post_body(post, html),
        floors=floors,
    )


# ---- 订阅校验 ----


def parse_subreddit_page(html: str) -> str | None:
    """版块页 HTML -> subreddit 名（校验存在性用）；不存在（壳页）回 None。

    subreddit-name 首次出现可能在 400KB+ 深处（页首是大量 head/CSS），
    全量搜索——600KB 级页面的单次正则扫描代价可忽略。
    """
    m = re.search(r'subreddit-name="([A-Za-z0-9_]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'subreddit-prefixed-name="r/([A-Za-z0-9_]+)"', html)
    return m.group(1) if m else None


# ---- 订阅输入归一 ----


def parse_subreddit_input(text: str) -> str:
    """四形态归一成裸 subreddit 名（小写）：rust / r/rust / /r/rust/ / 站点地址。

    认不出回空串（调用方给「没认出」反馈，不在这里猜）。
    """
    s = (text or "").strip()
    if not s:
        return ""
    if m := _R_PATH_RE.search(s):
        return m.group(1).lower()
    s = s.rstrip("/")
    return s.lower() if _NAME_RE.match(s) else ""
