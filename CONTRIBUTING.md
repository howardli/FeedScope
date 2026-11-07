# Contributing to FeedScope

Thanks for your interest in making FeedScope better. Bug reports, new sources,
new languages and doc improvements are all welcome — the two most natural
contributions for this codebase are **adding a source** and **adding a
language**, both designed to need almost no code outside their own files.

A note on language: the README is bilingual (English / 中文); code comments in
this repo are written in Chinese. Either language is fine in issues and PRs.

## Getting started

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a local
Google Chrome (the app drives it; no `playwright install` needed).

```bash
git clone https://github.com/howardli/FeedScope.git
cd FeedScope
uv sync                 # create the venv and install all dependencies
uv run feedscope        # run the app from source
uv run pytest           # run the full test suite — all offline, ~1 min
```

The full quality gate (same as CI):

```bash
uv run ruff check src/ tests/ scripts/
uv run mypy             # src/ is typed and held at zero errors
uv run pytest tests/ --cov   # coverage floor: 80%
```

## Project layout

```
src/feedscope/
  core/        models, config, registry, i18n, text/wrap/timefmt, tree,
               browser channel — the foundation everything else builds on
  sources/     one package per site: zhihu / x / reddit / hn / v2ex / rss / telegram
  ui/          Textual screens and widgets
  cli.py       argparse entry point (tui / login / probe / doctor / rss)
tests/         offline suite: fixture-driven parsers, TUI smoke via Textual Pilot,
               plus architecture / i18n / registry guard tests
scripts/       screenshot.py (README images), demo_gif.py, x_probe.py
```

Layering is enforced by `tests/test_architecture.py`, not just convention:

```
ui → top-level business (cli / login / subscriptions / probe / doctor / report)
   → sources → core
```

Dependencies only point downward; `parse.py` modules are pure leaves (no IO);
UI never reads source-private keys. If the guard blocks you, read its
docstring — the exceptions are documented there.

## Adding a new source

The highest-value contribution. The architecture is plugin-shaped: a source is
one package under `src/feedscope/sources/`, registered by a single import —
UI and core need zero changes.

```
sources/<id>/
  fetch.py      data layer: get raw data (httpx / resident browser tab /
                DOM extraction); incremental loaders that hold resources live
                here. No UI.
  parse.py      parsing layer: raw dict/HTML → FeedItem / Answer / DetailDoc /
                markdown. Pure functions, directly testable against fixtures.
                No network, no IO — it is the bottommost leaf.
  __init__.py   orchestration: the @register'd Source subclass — capability
                declaration and open_* dispatch. First-screen guard via
                require_feed_items; login gate via core.source.login_gate
                if the site needs one.
```

Step by step:

1. **Read an existing source closest to your target shape.** RSS / Telegram
   are subscription-style; HN / Reddit are three-layer with discussion trees;
   V2EX is two-layer. `sources/__init__.py`'s docstring is the authoritative
   template.
2. **Write `parse.py` first** and drive it with saved fixtures
   (`tests/test_<id>_parse.py`). This is where most of the logic should live,
   because it is trivially testable offline.
3. **Write `fetch.py`**: use `core/http.py` for plain HTTP, or the resident
   browser-tab channel (`core/browser.py`) when the site blocks non-browser
   fingerprints — see the Reddit / X sources for the pattern.
4. **Write the `Source` subclass** in `__init__.py` with its capability flags
   (`supports_detail`, `supports_comments`, `subscribable`, `feeds`,
   `feed_labels`, `detail_kinds`). Inherit the shared skeletons instead of
   rewriting mechanics:
   - subscription-style → `core.source.SubscribedSource` (the "All" stream's
     error handling comes free; inject `_fetch_merged` / `_open_one`)
   - detail fetched as one full tree → `SliceDetailLoader` (floor / reply
     paging = in-memory slicing, zero code)
   - body carried by the list → `StaticDetailLoader` (opening a detail costs
     zero requests)
   - deep discussion flattening → `core.tree` (`@parent` prefix rule has a
     single implementation)
5. **Register it** by adding one import line to `sources/__init__.py`, in
   **alphabetical order by `source_id`** — import order = source-picker row
   order = default Tab order. `tests/test_source_caps.py` and
   `tests/test_registry_order_is_tab_order` go red if you get this wrong.
6. **All UI copy through i18n** (`core/i18n.t()`) — never hardcode user-visible
   strings; `tests/test_i18n_no_hardcoded.py` blocks it in CI. Source
   vocabulary words (unit labels like "回复" / "comment") go through the same
   catalogs.
7. **Run the full gate** (see above) and add a caps test entry if the source
   introduces a new capability combination.

## Adding a language

Copy `src/feedscope/core/i18n/en.toml` to `<code>.toml` (e.g. `ja.toml`), set
the native name in `[meta]`, translate the messages. The language picker
discovers it automatically — zero code changes. `tests/test_i18n.py` enforces
an identical key tree across catalogs, so a missing translation fails CI.

## UI changes → refresh the images

README screenshots and the demo GIF are generated from the real app driven by
offline stub sources, so they can never drift from the code:

```bash
uv run python scripts/screenshot.py                  # README stills → docs/*.png
uv run --with pillow python scripts/demo_gif.py      # demo GIF → docs/demo.gif
```

If your change affects what's on screen, re-run the relevant script and commit
the images together with the code.

## Pull requests

- Keep PRs focused; one logical change each
- New behavior needs tests (offline; fixture-driven — look at existing
  `tests/test_*_parse.py` for the pattern)
- User-facing changes get a `CHANGELOG.md` entry under `[Unreleased]`
- CI (ruff + mypy + pytest on Python 3.11–3.13, macOS & Linux) must be green
- Match the existing style: Chinese comments that explain *why*, ruff's
  line-length 100 in `src/`

Thank you! 🎉
