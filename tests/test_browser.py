"""core/browser.py 离线单测：headless UA 修正的替换语义（不启动浏览器）。"""

from __future__ import annotations

from feedscope.core.browser import _HEADLESS_UA_RE

_PREFIX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
_FULL = "152.0.7977.65"


def _fix(ua: str) -> str:
    """_chrome_ua 的替换步骤（版本号由探针浏览器给，这里固定值）。"""
    return _HEADLESS_UA_RE.sub(lambda _: f"Chrome/{_FULL}", ua)


def test_frozen_headless_token_is_replaced() -> None:
    ua = _PREFIX + "HeadlessChrome/152.0.0.0 Safari/537.36"
    assert _fix(ua) == _PREFIX + f"Chrome/{_FULL} Safari/537.36"


def test_full_version_headless_token_is_replaced() -> None:
    """冻结形态若变成完整小版本（如 HeadlessChrome/152.0.7977.0），同样要换掉，
    不能静默漏替换——漏了 CF jsd 挑战会挂死不自解。"""
    ua = _PREFIX + "HeadlessChrome/152.0.7977.0 Safari/537.36"
    assert "HeadlessChrome" not in _fix(ua)


def test_normal_chrome_ua_untouched() -> None:
    ua = _PREFIX + f"Chrome/{_FULL} Safari/537.36"
    assert _fix(ua) == ua
    assert _HEADLESS_UA_RE.search(ua) is None  # 无标记 = 调用方走「无须修」分支


# ---- UA 探针落盘缓存（7 天 TTL；探不到不缓存）----


def test_ua_cache_roundtrip_and_ttl(tmp_path, monkeypatch) -> None:
    from feedscope.core import browser

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "profile")
    assert browser._read_ua_cache() is None  # 无缓存
    browser._write_ua_cache("Mozilla/5.0 Chrome/156.0.8078.74")
    assert browser._read_ua_cache() == "Mozilla/5.0 Chrome/156.0.8078.74"
    # 时间老化：ts 回拨 8 天 -> 过期
    import json
    import time
    cache = tmp_path / "chrome-ua.json"
    data = json.loads(cache.read_text())
    data["ts"] = time.time() - 8 * 24 * 3600
    cache.write_text(json.dumps(data))
    assert browser._read_ua_cache() is None


def test_ua_cache_corrupt_file_is_miss(tmp_path, monkeypatch) -> None:
    from feedscope.core import browser

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "profile")
    (tmp_path / "chrome-ua.json").write_text("not-json{")
    assert browser._read_ua_cache() is None
