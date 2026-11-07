"""i18n 基线：语言文件奇偶（键树 + 占位符）、发现 / 回退 / 缺省与 format 行为。

奇偶校验是「文案做全、不漏」的第一道防线：任何语言缺键、多键或占位符
不一致都在这里红——不靠人眼对表。第二道防线（src 里不允许再出现硬编码
中文串）在 test_i18n_no_hardcoded.py。
"""

from __future__ import annotations

import pytest

from feedscope.core import i18n


@pytest.fixture(autouse=True)
def _reset_language() -> None:
    """语言是进程级状态：用例改过就复位，不泄漏给同进程的其他用例。"""
    yield
    i18n.set_language(i18n.BASE_LANGUAGE)


def test_available_discovers_catalogs() -> None:
    """包内 TOML 即语言清单：基准语言排最前，自称来自各文件 [meta] name。"""
    langs = i18n.available()
    codes = [lang.code for lang in langs]
    assert codes[0] == i18n.BASE_LANGUAGE
    assert "en" in codes
    names = {lang.code: lang.name for lang in langs}
    assert names["zh-CN"] == "简体中文"
    assert names["en"] == "English"


def test_every_language_has_full_key_tree() -> None:
    base = i18n.catalog_keys(i18n.BASE_LANGUAGE)
    assert base, "基准语言目录为空：文案还没开始进目录？"
    for lang in i18n.available():
        assert i18n.catalog_keys(lang.code) == base, (
            f"{lang.code} 与基准键树不一致："
            f"缺 {sorted(base - i18n.catalog_keys(lang.code))[:5]} / "
            f"多 {sorted(i18n.catalog_keys(lang.code) - base)[:5]}"
        )


def test_placeholder_sets_match_across_languages() -> None:
    """同一键在所有语言里占位符集合一致：英文改语序可以，换占位符不行。"""
    base = i18n.messages(i18n.BASE_LANGUAGE)
    for lang in i18n.available():
        for key, text in i18n.messages(lang.code).items():
            assert i18n.placeholders(text) == i18n.placeholders(base[key]), (
                f"{lang.code}:{key} 占位符与基准不一致（{text!r}）"
            )


def test_every_entry_is_format_safe() -> None:
    """无占位符的文案无参 format 不抛：字面花括号必须双写，裸单括号会在运行时炸。"""
    for lang in i18n.available():
        for key, text in i18n.messages(lang.code).items():
            if i18n.placeholders(text):
                continue  # 有占位符的条目无参调用本就该 KeyError，不在此查
            try:
                text.format()
            except (KeyError, ValueError) as exc:
                raise AssertionError(f"{lang.code}:{key} 文案含未转义花括号（{text!r}）：{exc}")


def test_t_formats_placeholders_and_escapes_braces() -> None:
    assert i18n.t("config.describe_enabled", n=3) == "启用 3 源"
    header = i18n.t("config.file_header")
    assert "{ label, url }" in header and "{{" not in header  # 双写转义生效


def test_t_missing_key_falls_back_to_key(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="feedscope.core.i18n"):
        assert i18n.t("no.such.key") == "no.such.key"
    assert "no.such.key" in caplog.text


def test_t_falls_back_to_base_language(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """当前语言缺键回退基准语言文案（奇偶测试保证投产后不发生，这里钉行为）。"""
    base_text = i18n.messages(i18n.BASE_LANGUAGE)["config.describe_enabled"]
    en = {k: v for k, v in i18n.messages("en").items() if k != "config.describe_enabled"}
    monkeypatch.setitem(i18n._catalogs, "en", en)
    i18n.set_language("en")
    with caplog.at_level("WARNING", logger="feedscope.core.i18n"):
        assert i18n.t("config.describe_enabled", n=2) == base_text.format(n=2)
    assert "config.describe_enabled" not in caplog.text  # 基准兜住的不算缺键


def test_set_language_unknown_code_falls_back_to_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="feedscope.core.i18n"):
        i18n.set_language("klingon")
    assert i18n.language() == i18n.DEFAULT_LANGUAGE
    assert "klingon" in caplog.text


def test_default_language_guesses_from_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "getlocale", lambda: ("zh_CN.UTF-8", "UTF-8"))
    assert i18n.default_language() == "zh-CN"
    monkeypatch.setattr(i18n, "getlocale", lambda: ("en_US", "UTF-8"))
    assert i18n.default_language() == "en"
    monkeypatch.setattr(i18n, "getlocale", lambda: ("fr_FR", "UTF-8"))
    assert i18n.default_language() == "en"  # 只有中英两种：非中文给英文
    monkeypatch.setattr(i18n, "getlocale", lambda: (None, None))
    assert i18n.default_language() == i18n.DEFAULT_LANGUAGE  # 取不到用缺省（英文）

    def _broken():
        raise ValueError("bad LANG")

    monkeypatch.setattr(i18n, "getlocale", _broken)
    assert i18n.default_language() == i18n.DEFAULT_LANGUAGE  # 坏 LANG 也不挡启动


def test_registry_vocabulary_resolves_in_every_language() -> None:
    """全部注册源的词汇键（label/流名/单位词/订阅名词）在每种语言都解析得开。

    动态拼键（sources.<id>.feeds.<name>）无法静态查，靠遍历注册表兜住：
    某源漏配流名 = 这里红，不是上屏露键名。无点的裸文案（stub 源 / 用户
    标签）直通，不算键。
    """
    import feedscope.sources  # noqa: F401  import 即注册

    from feedscope.core.registry import all_sources
    from feedscope.core.source import SubscribedSource

    def dotted(value: str) -> bool:
        return "." in value

    langs = i18n.available()
    for source in all_sources():  # all_sources 给实例（词汇表读实例视图）
        keys = [source.label, source.detail_unit_label, source.comment_unit_label]
        if isinstance(source, SubscribedSource):
            keys.append(source.sub_noun)
        keys += [v for v in source.feed_labels.values() if dotted(v)]  # 含订阅型的实例合成视图
        for key in keys:
            if not dotted(key):
                continue  # 无点 = 裸文案（品牌名 / 用户标签），不是键
            assert i18n.has(key), f"{source.source_id} 词汇键 {key} 不在目录"
            for lang in langs:
                assert key in i18n.catalog_keys(lang.code), (
                    f"{lang.code} 缺 {source.source_id} 的词汇键 {key}"
                )
