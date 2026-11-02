"""core/media.py 媒体标记共用件的基线测试（离线，无网络）。"""

from feedscope.core.media import Tokens, attrs, mark, pick_url, video_url


def test_attrs_unescapes_and_lowercases() -> None:
    assert attrs('<img SRC="a&amp;b" data-x="1">') == {"src": "a&b", "data-x": "1"}


def test_pick_url_priority_and_data_skip() -> None:
    # data-original 优先；data: 懒加载占位被跳过
    tag = '<img src="data:image/svg+xml,x" data-original="https://a/1.jpg">'
    assert pick_url(tag, ("data-original", "src")) == "https://a/1.jpg"


def test_pick_url_protocol_relative() -> None:
    assert pick_url('<img src="//cdn.example.com/x.png">', ("src",)) == "https://cdn.example.com/x.png"


def test_pick_url_all_empty() -> None:
    assert pick_url("<img>", ("src",)) == ""


def test_video_url_nested_source_first() -> None:
    block = '<video controls><source src="https://v/1.mp4" type="video/mp4"></video>'
    assert video_url(block) == "https://v/1.mp4"


def test_video_url_own_src_fallback() -> None:
    assert video_url('<video src="https://v/2.mp4"></video>') == "https://v/2.mp4"


def test_mark_is_clickable_markdown_link() -> None:
    # markdown 链接：Textual 渲染成可点击组件、URL 折叠；无地址退回纯标记
    assert mark("[图片]", "https://a") == "[图片](https://a)"
    assert mark("[图片]", "") == "[图片]"
    # URL 里的 ) 截断链接语法，转义 %29（维基类图片地址常见）
    assert mark("[图片]", "https://a/b(1).jpg") == "[图片](https://a/b(1%29.jpg)"


def test_tokens_roundtrip() -> None:
    tok = Tokens()
    key = tok.stash("https://a/1.png?x=1&y=2")
    assert tok.expand(f"前文{key}后文") == "前文https://a/1.png?x=1&y=2后文"
