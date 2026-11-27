# Contributing to FeedScope

Thanks for your interest in making FeedScope better. Bug reports, new sources,
new languages and doc improvements are all welcome — the two most natural
contributions for this codebase are **adding a source** and **adding a
language**, both designed to need almost no code outside their own files.

A note on language: the README is bilingual (English / 中文); code comments in
this repo are written in Chinese. Either language is fine in issues and PRs.

If you are an AI agent (or a human who wants the map in one page): `AGENTS.md` is
the working handbook — layering, invariants and the test that guards each,
conventions, recipes for common tasks and a per-module map that a test keeps in
sync with the code. This file is the human-facing tour; the code and
`tests/test_architecture.py` are the source of truth.

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
uv run pytest tests/ --cov   # coverage floor: 88% (measured ~92%)
```

## Project layout

```
src/feedscope/
  cli.py       entry point: assembles the parser and dispatches — implements no command
  commands/    one module per subcommand (NAME / register / run): tui, login, probe,
               doctor, cache, rss, favorites
  services/    business shared by CLI and TUI: login, subscriptions, opml
  sources/     one package per site: hn / reddit / rss / telegram / v2ex / x / zhihu —
               each is __init__.py (orchestration) → fetch.py (IO) → parse.py (pure)
  ui/          Textual screens and their support pieces (widgets, layout, rendering)
  core/        models, the Source contract, loaders, config, state, i18n, browser and
               HTTP channels, text tools — the foundation everything else builds on
tests/         offline suite mirroring that layout (tests/<layer>/test_<module>.py),
               with shared doubles in tests/support/ and per-source fixtures
scripts/       screenshot.py (README images), demo_gif.py, x_probe.py; the demo sources
               and the rasteriser they share live in demo_sources.py / rasterize.py
AGENTS.md      the one-page handbook: layering, invariants, conventions, module map
```

Layering is enforced by `tests/test_architecture.py`, not just convention:

```
cli → commands → services → sources → core
ui  → services → sources → core        (ui never imports a concrete source)
```

The guards go further than direction: no import cycles, dependencies are imported
at module top (function-level imports are allowlisted), no module imports another
one's underscore-private names, `parse.py` public functions are all `parse_*`
pure leaves with no IO, UI never reads source-private keys, tests never import
other test modules, and modules/functions stay within size budgets. If a guard
blocks you, read its docstring — the exceptions and their reasons are documented
there. Change a rule by changing the guard first, never by weakening it to pass.

User-controlled text (titles, subscription names, filter words) must never be
parsed as Rich markup — Textual's `Label` / `Static` / `notify` do that by
default, and `[AI] Weekly` loses its bracketed word while a stray closing tag
raises `MarkupError`. `tests/ui/test_markup_sinks.py` enforces the two safe forms:

- a plain-text output passes `markup=False` (dialog prompts, toasts, hints), or
- a markup-enabled output escapes every dynamic value with
  `feedscope.ui.markup.escape` — never `rich.markup.escape`, which lets
  upper-case bracketed words through

Anything else has to be listed in that test's exemption table with a reason.

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
                require_feed_items; login gate via Source.require_login（判据是源差异：覆写 Source.logged_in）
                if the site needs one.
```

Step by step:

1. **Read an existing source closest to your target shape.** RSS / Telegram
   are subscription-style; HN / Reddit are three-layer with discussion trees;
   V2EX is two-layer. `sources/__init__.py`'s docstring is the authoritative
   template, and `AGENTS.md` has the checklist.
2. **Write `parse.py` first** and drive it with saved fixtures
   (`tests/sources/<id>/test_parse.py`, shared fixtures in `fixtures.py` next to
   it). This is where most of the logic should live, because it is trivially
   testable offline. Public functions are `parse_*`; helpers stay underscore-private.
3. **Write `fetch.py`**: use `core/http.py` for plain HTTP, or the resident
   browser-tab channel (`core/browser.py`) when the site blocks non-browser
   fingerprints — see the Reddit / X sources for the pattern.
4. **Write the `Source` subclass** in `__init__.py` with its capability flags
   (`supports_detail`, `supports_comments`, `subscribable`, `feeds`,
   `feed_labels`, `detail_kinds`). Inherit the shared skeletons instead of
   rewriting mechanics:
   - subscription-style → `core.source.SubscribedSource` (the "All" stream's
     error handling comes free; inject `_fetch_merged` / `_open_one`)
   - detail fetched as one full tree → `core.loaders.SliceDetailLoader.from_detail(...)`
     (floor / reply paging = in-memory slicing, zero code)
   - body carried by the list → `core.loaders.StaticDetailLoader` (opening a
     detail costs zero requests)
   - deep discussion flattening → `core.tree` (`@parent` prefix rule has a
     single implementation)
   - needs login → declare `login_url` / `login_cookie` / `logout_cookies` and
     `await self.require_login(...)` at the top of `open_feed` / `open_detail`;
     override `logged_in()` only when cookie presence is not proof (Reddit)
   - user-subscribable → `subscribable = True` plus the three hooks
     (`subscription_input` / `check_subscription` / `probe_subscription`) and the
     four copy keys; `services.subscriptions.store_for` builds the manager from
     the declaration, so there is no registry to edit
5. **Register it** by adding one import line to `sources/__init__.py`, in
   **alphabetical order by `source_id`** — import order = source-picker row
   order = default Tab order. `tests/sources/test_source_caps.py` (its
   `test_registry_order_is_tab_order` case included) goes red if you get this wrong.
6. **All UI copy through i18n** (`core/i18n.t()`) — never hardcode user-visible
   strings; `tests/test_i18n_no_hardcoded.py` blocks it in CI. Source
   vocabulary words (unit labels like "回复" / "comment") go through the same
   catalogs.
7. **Run the full gate** (see above) and add a caps test entry if the source
   introduces a new capability combination.

## Adding a language

Copy `src/feedscope/core/i18n/en.toml` to `<code>.toml` (e.g. `ja.toml`), set
the native name in `[meta]`, translate the messages. The language picker
discovers it automatically — zero code changes. `tests/core/test_i18n.py` enforces
an identical key tree across catalogs, so a missing translation fails CI
(`tests/core/test_i18n.py`).

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
- New behavior needs tests (offline; fixture-driven — put them in the mirrored
  directory, `tests/<layer>/test_<module>.py`, and look at the neighbours for
  the pattern; shared doubles live in `tests/support/`, never in another test file)
- User-facing changes get a `CHANGELOG.md` entry under `[Unreleased]`
- CI (ruff + mypy + pytest on Python 3.11–3.15, macOS & Linux) must be green
- Match the existing style: Chinese comments that explain *why*, ruff's
  line-length 100 in `src/`, no `type: ignore`, dependencies imported at module top
- New module? Add its one-line entry to the module map in `AGENTS.md` — a guard
  fails until the map matches the tree

Thank you! 🎉
