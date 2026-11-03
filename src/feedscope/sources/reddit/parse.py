"""解析层：Reddit 网页 JSON -> FeedItem / 帖子与评论树，纯函数不碰网络。

结构映射是 HN 式三层（词汇重映射，UI 零特判）：
- 列表 t3 -> FeedItem（id36 即条目 id；is_self 分 self/link 两类 kind）
- comments/:id 的帖子 -> 帖子本体：自述帖 markdown 直传——Reddit 正文原生
  就是 markdown，全项目第一个不经 HTML 转换的源；媒体帖换 [图片]/[视频]
  记号；链接帖 = 原文一行（同 HN）
- 顶层评论 -> 楼层（Answer，unit「评论」）；楼层内回复树 -> 评论层
  （Comment，unit「回复」）。Comment 模型只有两层而 Reddit 常嵌四五层：
  照 HN 拍平——直接子回复做顶层，更深的拍平进 replies；隔层回复加
  「@父作者」前缀。整棵树一次已在内存，翻页是切片不是请求。

Reddit 特有语义：
- score：楼层与评论的 votes 都有值（HN 树节点无分值留空）
- stickied：版务置顶评论加「[置顶] 」前缀（版务帖删不掉，标出来保可读）
- [deleted]/[removed]：作者/正文的删除占位（作者栏 [已删除]、正文
  「（已删除）」；有回复的删除评论保留树形，与 HN 删除节点同语义）
- more 续页对象：树被 limit/depth 截断的口子（见 fetch 的取值），count
  折进相邻 Comment.more_replies（知乎「另有 N 条回复」同款提示）；顶层
  more 折进末楼层 comment_count 文案。不做 morechildren 续拉——截断提示
  诚实够用，省一整条分页链路
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field

from feedscope.core.display import fmt_count, votes_text
from feedscope.core.media import mark
from feedscope.core.models import Answer, Comment, FeedItem
from feedscope.core.text import strip_zwsp
from feedscope.core.timefmt import fmt_unix

SOURCE_ID = "reddit"

# permalink 前缀（列表条目 url 与楼层直达链接共用；与 fetch 层的 host 同值各自维护）
_SITE = "https://www.reddit.com"

_DELETED = "[已删除]"  # 作者栏占位（Reddit 原生是 [deleted]/[removed]）
_DELETED_BODIES = {"[deleted]", "[removed]"}

# subreddit 名域（字母数字下划线）：下限放宽到 2——校验过严拒掉真订阅的
# 代价（加不上），比过松收进一个假名（about.json 会 404 拦住）更高
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")

# 站内地址里的 /r/<名>/ 段（起点或前面是 / . = 才认，避免误吃普通词）
_R_PATH_RE = re.compile(r"(?:^|[/=.])r/([A-Za-z0-9_]+)/?")


def _int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _md(text: str) -> str:
    """Reddit 正文原生 markdown 直传，仅清洗零宽空格（其余源走 HTML 转换）。"""
    return strip_zwsp(text).strip()


def _data(child: dict) -> dict:
    d = child.get("data")
    return d if isinstance(d, dict) else {}


def _listing_children(listing: object) -> list[dict]:
    """Listing 对象 -> children 列表（非 dict 成员剔除）；形不符回空。"""
    data = listing.get("data") if isinstance(listing, dict) else None
    children = data.get("children") if isinstance(data, dict) else None
    return [c for c in children if isinstance(c, dict)] if isinstance(children, list) else []


# ---- 列表 ----


def parse_listing(children: list[dict]) -> list[FeedItem]:
    """listing children（t3）-> FeedItem 列表（保持返回序，野数据逐条防御）。"""
    items: list[FeedItem] = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        d = _data(child)
        cid = str(d.get("id") or "")
        if not cid:
            continue  # 无 id 无法寻址，跳条不跳流
        permalink = str(d.get("permalink") or "")
        subreddit = str(d.get("subreddit") or "")
        parts = [
            f"{fmt_count(_int(d.get('score')))} 点",
            f"{fmt_count(_int(d.get('num_comments')))} 评",
        ]
        if subreddit:
            parts.append(f"r/{subreddit}")
        items.append(
            FeedItem(
                source=SOURCE_ID,
                id=cid,
                title=str(d.get("title") or "").strip() or "（无标题）",
                url=f"{_SITE}{permalink}" if permalink.startswith("/") else _SITE,
                kind="self" if d.get("is_self") else "link",
                meta=" · ".join(parts),
                extra={"subreddit": subreddit},
            )
        )
    return items


# ---- 帖子与评论树 ----


@dataclass
class Floor:
    """一个楼层（顶层评论）：本体 + 它的回复树（已是 Comment 列表）。"""

    answer: Answer
    comments: list[Comment] = field(default_factory=list)


@dataclass
class ThreadDetail:
    """comments/:id 的解析结果：帖子本体 + 全部楼层（含各自的回复树）。"""

    title: str
    meta: str
    time: str
    description_md: str
    floors: list[Floor] = field(default_factory=list)


def _author(d: dict) -> str:
    a = str(d.get("author") or "")
    return a if a and a not in _DELETED_BODIES else _DELETED


def _body(d: dict) -> str:
    b = str(d.get("body") or "")
    return "" if not b or b in _DELETED_BODIES else _md(b)


def _keep(d: dict) -> bool:
    """空壳节点（被删且无回复的评论）整支跳过，与 HN _keep 同语义。"""
    return bool(_body(d) or _t1_children(d))


def _content(d: dict, *, at: str = "") -> str:
    """一条评论的正文：删除占位、@ 回复指向前缀、置顶前缀的拼装次序唯一出处。"""
    md = _body(d) or "（已删除）"
    if at:
        md = f"{at}：{md}"
    if d.get("stickied"):
        md = f"[置顶] {md}"
    return md


def _comment(d: dict, *, at: str = "") -> Comment:
    return Comment(
        author=_author(d),
        votes=votes_text(_int(d.get("score"))),
        content_md=_content(d, at=at),
        time=fmt_unix(d.get("created_utc")),
        raw_id=str(d.get("id") or ""),
    )


def _t1_children(node: dict) -> list[dict]:
    """t1 的 replies -> children 列表；无回复时 Reddit 给的是空串而非 dict。"""
    replies = node.get("replies")
    data = replies.get("data") if isinstance(replies, dict) else None
    children = data.get("children") if isinstance(data, dict) else None
    return [c for c in children if isinstance(c, dict)] if isinstance(children, list) else []


def _flatten(node: dict, parent_author: str, *, direct: bool = False) -> tuple[list[Comment], int]:
    """node 的全部后代拍平成一串 Comment（@ 前缀标出回复对象）+ 上交的 more 数。

    direct=True 是「紧贴上一条」的第一层（回复的就是楼上那条，@ 冗余）；
    更深层 @ 直接父——被拍平后父不一定视觉相邻，@ 是唯一指向。父已删
    不给前缀（@ 不到人）；本尊已删有内容照常保留。more 的 count 记到
    同层前一条 Comment.more_replies（截断口在那支子树末尾）；前一条不
    存在（该层整个被截掉）只能上交，由调用方折进所属分支的根。
    """
    flat: list[Comment] = []
    overflow = 0
    for child in _t1_children(node):
        d = _data(child)
        if child.get("kind") == "more":
            if flat:
                flat[-1].more_replies += _int(d.get("count"))
            else:
                overflow += _int(d.get("count"))
            continue
        if child.get("kind") != "t1" or not _keep(d):
            continue
        author = _author(d)
        at = "" if direct or not parent_author or parent_author == _DELETED else f"@{parent_author}"
        c = _comment(d, at=at)
        sub, more = _flatten(d, author)
        if more:
            c.more_replies = more  # 该支开头整层截断：折到这支的根上
        flat.append(c)
        flat.extend(sub)
    return flat, overflow


def _floor_comments(node: dict) -> list[Comment]:
    """楼层的直接子回复 -> 顶层 Comment；各自更深的后代拍平进 replies（同 HN）。

    直接子回复不加 @：楼层作者就在屏首（与知乎「回答下的评论」同观感）；
    replies 里第一层（紧贴楼上那条）也不加，@ 从隔层起才有指向价值。
    """
    comments: list[Comment] = []
    for child in _t1_children(node):
        d = _data(child)
        if child.get("kind") == "more":
            if comments:
                comments[-1].more_replies += _int(d.get("count"))
            # 楼层只有 more 没有任何 t1：无处挂提示，丢弃（about 极罕见）
            continue
        if child.get("kind") != "t1" or not _keep(d):
            continue
        author = _author(d)
        c = _comment(d)
        c.replies, more = _flatten(d, author, direct=True)
        c.more_replies += more  # 回复树开头的整层截断折到本条
        comments.append(c)
    return comments


def _count_replies(node: dict) -> int:
    """node 子树内保留下来的评论总数（楼层头「N 条回复」用；more 截断部分不计）。"""
    return sum(
        1 + _count_replies(_data(c))
        for c in _t1_children(node)
        if c.get("kind") == "t1" and _keep(_data(c))
    )


def _media_marks(d: dict) -> list[str]:
    """preview 图（含相册）+ 原生视频直链 -> 媒体记号列表；上限 4 张防相册刷屏。"""
    marks: list[str] = []
    if d.get("is_video"):
        media = d.get("secure_media") or d.get("media")
        rv = media.get("reddit_video") if isinstance(media, dict) else None
        if isinstance(rv, dict) and (u := str(rv.get("fallback_url") or "")):
            marks.append(mark("[视频]", u))
            return marks  # 视频直链在手，preview 只是海报帧，冗余不带
    preview = d.get("preview")
    images = preview.get("images") if isinstance(preview, dict) else None
    if isinstance(images, list):
        for img in images[:4]:
            src = img.get("source") if isinstance(img, dict) else None
            if isinstance(src, dict) and (u := _html.unescape(str(src.get("url") or ""))):
                marks.append(mark("[图片]", u))  # preview 地址服务端存的是 &amp; 转义态
    return marks


def _post_body(d: dict) -> str:
    """帖子正文：自述帖 markdown 直传；媒体帖记号直达原图；链接帖一行原文。"""
    if d.get("is_self"):
        raw = str(d.get("selftext") or "")
        if raw in _DELETED_BODIES:
            return "（已删除）"
        return _md(raw)
    if d.get("is_reddit_media_host") or d.get("is_video"):
        marks = _media_marks(d)
        url = _html.unescape(str(d.get("url") or ""))
        # 记号拿不到再兜底直链（v.redd.it 视频帖的 url 有时就是直链本身）
        if not marks and url and not url.startswith(_SITE):
            marks.append(mark("[视频]" if "/v.redd.it/" in url else "[图片]", url))
        return "\n\n".join(marks)
    url = _html.unescape(str(d.get("url") or ""))
    return f"[原文]({url})" if url else ""


def parse_thread(data: object, fallback_title: str = "") -> ThreadDetail:
    """comments/:id 的 [帖子 listing, 评论 listing] -> ThreadDetail（野数据防御）。"""
    post: dict = {}
    top: list[dict] = []
    if isinstance(data, list) and len(data) >= 2:
        t3s = [c for c in _listing_children(data[0]) if c.get("kind") == "t3"]
        if t3s:
            post = _data(t3s[0])
        top = _listing_children(data[1])
    subreddit = str(post.get("subreddit") or "")
    parts = [
        f"{fmt_count(_int(post.get('score')))} 点",
        f"{fmt_count(_int(post.get('num_comments')))} 评",
    ]
    if subreddit:
        parts.append(f"r/{subreddit}")
    if author := str(post.get("author") or ""):
        parts.append(f"u/{author}")
    floors: list[Floor] = []
    top_more = 0
    for child in top:
        d = _data(child)
        if child.get("kind") == "more":
            top_more += _int(d.get("count"))  # 顶层还有整段没载（>limit 的大帖）
            continue
        if child.get("kind") != "t1" or not _keep(d):
            continue
        n = _count_replies(d)
        cp = str(d.get("permalink") or "")
        floors.append(
            Floor(
                answer=Answer(
                    author=_author(d),
                    votes=votes_text(_int(d.get("score"))),
                    content_md=_content(d),
                    time=fmt_unix(d.get("created_utc")),
                    url=f"{_SITE}{cp}" if cp.startswith("/") else "",
                    comment_count=f"{n} 条回复" if n else "",  # 0 条不引导 Enter
                ),
                comments=_floor_comments(d),
            )
        )
    if top_more and floors:
        last = floors[-1].answer
        last.comment_count = " · ".join(
            x for x in (last.comment_count, f"另有 {fmt_count(top_more)} 条未展开") if x
        )
    return ThreadDetail(
        title=str(post.get("title") or "").strip() or fallback_title,
        meta=" · ".join(parts),
        time=fmt_unix(post.get("created_utc")),
        description_md=_post_body(post),
        floors=floors,
    )


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
