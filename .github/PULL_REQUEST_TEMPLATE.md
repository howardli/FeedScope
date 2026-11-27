## Summary / 摘要

<!-- What changed and why. One or two sentences, then the detail that matters. -->

## Type of change / 变更类型

- [ ] Bug fix
- [ ] New feature
- [ ] New source (registered alphabetically in `sources/__init__.py`; `tests/sources/test_source_caps.py` updated)
- [ ] Refactor / internals
- [ ] Docs / assets

## Checklist / 自查

- [ ] Tests added or updated for the behavior change (fixture-driven, offline, in the mirrored directory `tests/<layer>/`)
- [ ] `uv run ruff check src/ tests/ scripts/` clean
- [ ] `uv run mypy` clean
- [ ] `uv run pytest tests/` green
- [ ] Architecture guards green (`uv run pytest tests/test_architecture.py`); a new module has its line in the `AGENTS.md` module map
- [ ] User-facing change has a `CHANGELOG.md` entry under `[Unreleased]`
- [ ] UI-visible change → screenshots / demo GIF regenerated (`scripts/screenshot.py`, `scripts/demo_gif.py`)
- [ ] New user-visible strings go through i18n catalogs (no hardcoded copy)
