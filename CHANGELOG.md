# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Restart resumes where you left off: the source, feed and selected item are saved on quit (and whenever an item is opened) in `state.json` under `last`, and the next start lands back on them. The item is located by id, so items that arrived above it in the meantime don't matter. A source that is no longer enabled or a feed that is gone falls back to the first source / feed; an item that has scrolled off leaves the list at the top
- Telegram channel posts carry the content the preview page shows but the parser dropped: forwarded posts start with a "Forwarded from …" line, posts that reply to another quote the replied message, link-preview cards (site / title / summary) render after the text, files and voice notes become `[file]` / `[audio]` markers with the file title, and polls render as the question plus its options (with percentages when the preview shows results) and end with a `[poll]` link to the post, where you can vote

### Fixed

- Telegram albums lost all but their first item — a post with nine photos or videos showed a single marker. Every photo now gets its own `[image]` marker, and a post's videos collapse into one `[video N]` marker
- Telegram posts that reply to another post showed the *replied* message as their own title and body: the quote inside the reply header shares the body's class name, and the parser took the first match
- Telegram list titles showed raw HTML entities (`&#036;100,400`); they are decoded now

## [0.5.0] - 2026-11-24

### Added

- Local search (`F6`): searches every cached list and your favorites as you type — titles for all sources, bodies for RSS / Telegram — with zero requests. Title hits come first, each group newest first; ↑↓ picks, Enter opens the detail through the item's own source, and ←/→ plus `]`/`[` step through the results. This is the roadmap's "full-text search across locally cached items"
- Keyboard link following (`Ctrl+G`): the links in what you are reading (`[image]`, `[video]`, `@mentions`, the original article) are listed in order — ↑↓ pick, Enter opens in the browser. They used to be mouse-only (Textual renders them as click actions)
- The unread keys work where you read: `]` / `[` inside a detail load the next / previous unread article (newsboat's next-unread) and `Ctrl+R` marks the one being read as unread; `]` / `[` / `Ctrl+R` are also available on the favorites list
- Starring from a reading screen archives the detail you had loaded (body, answers and reply trees). When the original can later no longer be opened — deleted, offline, the source is gone — Enter on that favorite shows the saved copy with a notice instead of a dead end; the login-expired flow is unchanged (sign in to see the latest). Favorites saved before this keep working
- `feedscope doctor` now really checks the config. An unparsable `sources.toml` is a `FAIL` (it printed `[OK] … unreadable` and exited 0, while saves refused to write and pointed at doctor), and settings that would silently not take effect are listed one per line: unknown `language` / `timezone` values, unknown source ids in `enabled`, `hide` names matching no feed (subscription keys count as feed names), malformed `enabled` / `hide` / `feeds`, subscription entries without a url or with a non-text label, non-string `browser` / `clipboard`. Data files kept aside by the fix below are reported as a `[WARN]` reminder (a new non-failing state)
- New config keys `browser` and `clipboard`: the command used to open links (`%u` is replaced by the URL; default `webbrowser`) and the command used to copy text (reads stdin; default auto-detects `pbcopy` / `wl-copy` / `xclip` / `xsel` when a graphical session exists, then falls back to the terminal's OSC 52)
- JSON Feed subscriptions are now parsed: auto-discovery and the Accept header already accepted `feed+json`, but the parser only understood XML. `content_text` is rendered as paragraphs and `attachments` appear as links
- RSS / Atom enclosures (podcasts, video feeds) appear after the body as `[audio]` / `[video]` / `[file]` links instead of being dropped
- Relative URLs inside RSS bodies (images and links) are resolved against the entry's own page

### Changed

- CI runs on Python 3.11–3.15 (the classifiers now list 3.14 and 3.15; the suite passes on both ends) with least-privilege permissions, per-job timeouts and per-branch concurrency cancellation; the build job installs the wheel in a clean environment and smoke-tests it, and the release pipeline gained a test gate before anything reaches PyPI
- Coverage floor raised from 80% to 88% (measured ~92%): the browser session start-up path — the lowest-covered infrastructure at 60% — is now exercised through a fake Playwright module (94%)
- README no longer claims that Reddit moderator stickies are flagged `[置顶]`. That prefix was lost when the Reddit source moved to the HTML channel (8d3a87b), and the README screenshot was generated from a stub that hand-wrote it; the stub is now honest and the screenshot regenerated. Restoring the flag needs a real thread sample to confirm the markup
- During an in-app sign-in only the browser-backed sources (Zhihu / X / Reddit) are blocked: HN / V2EX / RSS / Telegram keep refreshing, and switching to a browser-backed source is refused with a hint instead of leaving the tab and sidebar highlight out of step with the list

### Fixed

- Subscription names were interpreted as Rich markup in the sidebar and in the list's source-tag column (`[AI] Weekly` showed as ` Weekly`; a name containing a closing tag raised `MarkupError`), and the same happened in the remove-subscription confirm dialog — which made such a subscription impossible to remove from the TUI — and in the add / remove / rename toasts. Plain-text outputs (dialog prompts, toasts, empty-state hints) are now `markup=False` by construction, and a new AST guard (`tests/test_markup_sinks.py`) requires every markup-enabled sink to be safe by construction or explicitly exempt with a reason
- `rich.markup.escape` does not escape upper-case bracketed words (`[AI]`, `[PDF]`, `[Discussion]`) although Textual's parser treats them as tags, so titles in the main list lost them. The UI now uses its own exhaustive `escape` (`feedscope.ui.markup`)
- The Reddit feed loader could issue requests without bound: the top-up loop only checked whether 20 *new* posts had been collected, so a load-after chain that looped or kept re-delivering seen posts never ended. Each load is now capped and a revisited URL ends the chain. Other non-2xx statuses (429 / 5xx) are translated into an error instead of being parsed as HTML and reported as "the site may have changed"
- A damaged `state.json` / `favorites.json` was overwritten in place by the next ordinary write (a single new star turned a library of saved items into one entry). A file that is readable but unparsable is now renamed to `*.corrupt-<timestamp>` first, never overwriting an earlier kept-aside copy
- Hand-written comments and custom keys *inside* a `[….feeds]` table were dropped by any save from the subscription manager, `rss rename` or `rss add`; the table is now merged in place, so the "preserved verbatim" promise holds inside the table too
- In-page fetches through the resident browser tab had no timeout (neither `fetch` nor `page.evaluate` times out): a stalled request waited forever while holding the browser session lock. They now abort after 30 seconds and report a timeout
- `Ctrl+O` ignored the result of opening the browser — on SSH or headless hosts the item was marked read and nothing happened; it now warns and points at `Ctrl+Y` and the `browser` setting. `Ctrl+Y` relied on OSC 52 only (not supported by macOS Terminal) while always toasting "copied"
- A second `feedscope` instance deleted the live Chrome profile lock of the running one (`drop_stale_locks` removed any existing lock), which led to two Chromes on one profile. The lock is now inspected: a lock owned by a live process on this machine refuses to start and names the pid. A second TUI instance on the same state is warned that the two will overwrite each other
- Switching sources while a sign-in was in progress left the tab and sidebar highlight on the new source while the list still showed the previous one, so Enter dispatched the old item through the new source
- Lazy-loaded images (`data-src` / `data-original`) linked to the placeholder instead of the real image
- The favorites status line escaped the title filter for a header that does not parse markup, showing stray backslashes
- Stepping through favorites across sources (←/→ in a detail opened from the favorites list) opened the next item through the *previous* item's source; the detail screen now resolves the source per item (search results get the same)

## [0.4.2] - 2026-11-23

### Changed

- README (both languages) restructured for scannability: a Quick start block and a "Why FeedScope?" section now lead, the feature list is grouped into Sources / Reading / Terminal-native, the keyboard-conventions paragraph became a list, a Roadmap section was added, and images switched to absolute URLs so the PyPI page renders them; previously undocumented keys (`Home`/`End`/`PgUp`/`PgDn`), env vars (`FEEDSCOPE_NO_PREWARM`, `FEEDSCOPE_SAMPLE_DIR`), exit-code conventions, and the browser-profile path holding login sessions are now listed, and `rss`/`favorites export`-to-stdout is spelled out

### Fixed

- Docs-vs-code drift from a full audit: the nonexistent "double Ctrl+C quits" fallback claim removed (Textual 8's Ctrl+C only hints at Ctrl+Q), the stray-key hint is correctly scoped to CJK input, the Linux log path written out explicitly, the favorites list ordering stated as by-star-time, the new-language copy base unified to `en.toml` across both READMEs and CONTRIBUTING, the language-following subcommand list completed, `wrap` re-homed to `ui/` in CONTRIBUTING's layout, and stale version strings dropped from the bug template and release doc

## [0.4.1] - 2026-11-22

### Fixed

- Twelve latent bugs from a full-project audit, each with a reproducing regression test: X detail paging could mount duplicate floors when the API re-delivered first-batch replies (their ids were never registered for dedup); X queryId self-heal was blind to mid-session rotations (harvest only fills gaps — the stale entry is now dropped before re-harvesting); X timeline dropped visibility-wrapped tweets (`TweetWithVisibilityResults`) as broken cards instead of unwrapping them like the detail path; the title filter broke row↔item alignment on `Ctrl+N` (unfiltered rows were appended raw while selection addressed the filtered set); `Ctrl+A` on a mixed list over-decremented the aggregate feed's unread badge for already-read items; a title filter containing `[` could crash or silently restyle toasts / empty hints / the status line (user input is now markup-escaped); a corrupt-but-salvageable `sources.toml` was silently rewritten down to a single managed key by any save (saves now refuse to write and point at `doctor`); a subscription label containing `{` crashed the sidebar (`t()` no longer format-strings passthrough user data); a concurrent `feedscope cache clear` could crash an in-flight refresh (LRU stat failures are tolerated); two concurrent fetches through a rebuilding channel tab leaked an orphan browser page (double-check inside the session lock); a subscription-manager exit via the shared cancel path hit a `NotImplementedError` landmine; a filter-toggle rebuild cancelled an in-flight refresh, leaving the status line stuck on "loading"
- Zhihu login-state detection no longer string-matches localized error messages: `SourceError` carries a structured HTTP `status` and the 401/403/404 translations key off it — a wording change in either catalog can no longer silently break re-login guidance
- `update_feeds` aborts atomically on a top-level scalar key conflict (previously it still ran the enabled-linkage and wrote, leaving "enabled lists the source but its feeds table was never written")
- `test_probe` no longer probes real sources when run in isolation (missing registration import let `probe.py`'s module-level import repopulate the cleared stub registry)

### Changed

- Dependency floors tightened to the tested baseline: `textual>=8.0` (the theme layer rides Textual 8's dataclass `Theme` semantics; `>=1.0` could resolve to versions that fail at startup), `hatchling>=1.27` (PEP 639 SPDX expressions)
- Internal convergence with zero behavior change (554 tests green throughout): HN/Reddit "N pts · N comments" meta assembly lives once in `core.display`; the coalesced busy/dirty scheduling, `/` filter dialog flow, row-dot update, and login-wall prompting each have a single implementation shared by the main and favorites screens; `F1` help, `Ctrl+N`/`Ctrl+O` bindings and the `_tui` cast are hoisted to their family bases — the favorites help page now lists `Enter`
- Docs drift cleaned: SECURITY.md no longer hardcodes a version line, CHANGELOG release links for 0.4.0/0.3.1 defined, the "every binding is non-printing" README claim now names the five ASCII keys, `cli.py` docstring and CONTRIBUTING list all seven subcommands, `.gitignore` covers `.mypy_cache/`

## [0.4.0] - 2026-11-20

### Added

- Time awareness in list rows: feeds that carry timestamps (RSS / Telegram / HN / V2EX / X) show a right-aligned relative-time column (`25m` / `4h` / `2d`, then `MM-DD` / `YY-MM`); sources without list timestamps (Zhihu hot) keep the old layout. A new `timezone` config key (IANA name; default stays Beijing UTC+8) renders every timestamp in the chosen zone
- `/` filters the current list by title (case-insensitive substring; empty input clears the filter, Esc leaves it untouched) — on the main list and the favorites list, composing with `F4` unread-only (both filters show a dedicated empty-state hint)
- `]` / `[` jump to the next / previous unread item, wrapping around the current feed (same keys as floor-stepping in reply layers); all-read gives a toast
- Self-diagnosis logging: every run writes a rotating log file (INFO by default, `--verbose` for DEBUG, `--log-file <path>` to relocate, subcommands mirror to stderr under `--verbose`) — fetch failures with tracebacks, self-heal fallbacks (X queryId re-harvest etc.) and login-detector errors all land there; `feedscope doctor` prints the location
- `F2` source manager: `,` / `.` nudges the highlighted source's position (numbers re-sequence instantly; untick-then-tick still moves to the end)
- Favorites list paginates (100 rows per page, `Ctrl+N` loads the next page; the status line shows how many are displayed) — thousands of favorites no longer freeze the screen on open
- CLI: `--version`, `feedscope cache [clear]` (report / wipe the derived list cache, lossless), `feedscope rss rename <key> <name>`

### Changed

- Saving from the TUI (first-run / `F2` / `Ctrl+L` / `Ctrl+T` / subscription changes) now round-trips `sources.toml` with tomlkit: hand-written comments, unknown keys and formatting survive verbatim and only managed keys change (previously the file was re-rendered from scratch, silently dropping them); the standard header comment is added only to newly created files
- Toolchain baseline: type checking runs mypy `strict` (src keeps zero `type: ignore`); lint rules extended with bugbear / pyupgrade / isort / simplify / ruff-natives

### Fixed

- The in-place "comments failed · Ctrl+N retry" hint never actually appeared: the status swap queried the widget id as a CSS selector (`cm-loading` instead of `#cm-loading`), killing the worker with `InvalidQueryFormat` — the failure state only showed up after a resize repaint
- The favorites screen could mount duplicate rows when a filter dialog dismissed into a resume rebuild (two `clear`/`extend` cycles interleaving); rebuilds now coalesce through a single scheduler

## [0.3.1] - 2026-11-10

### Fixed

- Zhihu details failed with a bare `HTTP 403` while lists kept working: once the stored login session expires server-side, the lenient v3 feed endpoints still serve the list but the v4 detail endpoints (question meta / answers feeds / comments) reject the stale session. Those endpoints now translate 401/403 into the login-expired flow, and the detail screen offers the same sign-in dialog as the list screen (persistent hint + `Ctrl+U` after declining); comment paging no longer swallows the session-expired signal either
- After any session-expired signal this run, `Ctrl+U` offers sign-in directly instead of logout: a stale-but-present login cookie keeps fooling the cookie-presence check, which previously turned the account dialog into a logout-first detour (sign out, then sign in again); the stale-session memory clears on successful sign-in or logout
- Closing the sign-in Chrome window (the red button / `Cmd+W` on macOS) left the app showing "login in progress" and blocking every refresh for the rest of the 3-minute wait: on macOS the Chrome process outlives its last window, so the polling never errors out; the login page's `close` event now ends the wait immediately with a clear "window closed" outcome
- A fully logged-out Zhihu showed a generic "fetch failed" at the list layer with no sign-in guidance: the channel tab's login-wall check raised the right signal, but the shared fetch primitive's generic exception wrapper demoted it to a plain error before it could reach the UI (the detail layer took a different path and worked); the wall's exception now passes through untouched, so the list refresh pops the same login dialog

## [0.3.0] - 2026-11-09

### Added

- Unread management: per-feed unread badges in the sidebar (recounted on refresh, decremented live as you read — reads from the favorites list decrement the home feed too), `F4` unread-only view (session toggle; read rows vanish instantly, newsboat-style), and `Ctrl+A` mark-all-read for the current feed with a light confirmation
- UI themes: `Ctrl+T` switches instantly (pure presentation, no restart) between the built-in `editor-dark` (the existing VS Code Dark+ palette) and a new `editor-light`; a new theme is one TOML file in `ui/themes/`, auto-discovered, with completeness pinned by tests
- `Ctrl+Y` copies the current item's link from the list, favorites, details or reply layers (reply layers copy the floor-direct link), and read items get a dimmed title so read/unread separates at a glance
- `F1` opens a keybinding help overlay — global section plus the current screen's keys grouped by task (navigation / content / read state / favorites / settings), auto-derived from the binding declarations so it never goes stale, with Textual's inherited dead bindings filtered out; with it in place the footer slimmed to just `F1` (plus `Esc` on reading/favorites screens), freeing a line of content on every screen

### Fixed

- Rapid back-to-back sidebar refreshes could crash with `DuplicateIds` (two rebuilds interleaving on Textual's async child removal); sidebar refreshes now coalesce — at most one rebuild runs, with at most one queued rerun
- All modal dialogs (language / theme / help / confirm / subscriptions) hugged the left edge: `ModalScreen` ships no default centering; fixed once in the shared modal base class
- Unread counts failed to decrement for entries recorded before the badge feature (no feed routing key), and for items living in two feeds at once (RSS/Telegram "All" plus the member feed — decrement only hit the first-seen feed): entries now carry a membership set and reads decrement every member feed

## [0.2.0] - 2026-11-08

### Added

- Read/unread state: the row-leading `●` marks an item unread and clears when you open it (Enter into detail, `Ctrl+O` to the browser, `←`/`→` article stepping in a detail); `Ctrl+R` toggles the current item; unread persists across refreshes and restarts in a plain-text `state.json` (item ids and timestamps only, no credentials, 10k-entry FIFO cap), while the "N new" count of first-seen items shows in the status line
- Favorites / read-later: `Ctrl+F` stars the current item from the list or while reading, `F3` opens the cross-source favorites list (newest first, Enter fetches latest comments from the original source); a full snapshot is stored in `favorites.json` so items stay readable after they leave the feeds, favorited entries are exempt from the read-state cap, and `feedscope favorites [export [file.md]]` lists / exports them as Markdown
- Source-tag column for mixed lists: the RSS / Telegram "All" aggregate streams label each row's origin (feed / channel name) in a dedicated left-aligned column instead of prefixing it into the right-aligned meta column, which made column edges ragged; the favorites list shows the item's source the same way (a favorited aggregate item shows its feed/channel name)
- Offline list cache: each feed is cached to its own JSON file (200 items per feed, 60 most recent feeds kept); startup and refresh paint the cache first (instant restarts), and a failed refresh keeps the old list on screen instead of clearing it — RSS / Telegram detail bodies travel with the cached list and read offline

### Fixed

- The unread dot was invisible on the selected row: the highlight background and the dot both use the editor blue (blue-on-blue). On a highlighted row the dot now follows the selection text color; in the sidebar-focused state (highlight suppressed, no blue band) it keeps the editor blue

## [0.1.0] - 2026-11-07

Initial release.

### Added

- Multi-source aggregation in one terminal UI (`Tab` to switch): Zhihu Hot / Recommend / Following, X For You / Following, Reddit Home / Popular / custom subreddits, Hacker News Front / New / Ask / Show, V2EX Hot / Latest, Telegram channel subscriptions
- RSS subscriptions: any RSS / Atom feed; site-homepage feed auto-discovery; OPML import (idempotent merge) / export; "All" stream merged by publish time with cross-feed dedup; conditional requests (ETag / Last-Modified) keep refreshes polite to sources
- Three-layer in-terminal reading (feed → detail → discussion) loaded per layer on demand; HN-style deep threads flattened to two levels with `@parent` prefixes preserving who-replies-to-whom
- Login loop: in-app Chrome QR / password login with auto-refresh on completion, `Ctrl+U` to switch accounts; session-revocation detection that never leaves you guessing (X 401 semantics, Reddit `token_v2` JWT local decode — zero network, zero forgery)
- Resident browser-tab data channels for Zhihu / X / Reddit: TLS, header order and cookies fully consistent with the login session; X GraphQL queryIds self-heal by harvesting the current deployment's JS chunks; Reddit rides the website's own HTML channel with self-healing client version
- Auto Grok translation for foreign-language X posts; media rendered as clickable `[图片]` / `[视频]` markers linking to originals
- Bilingual UI (中文 / English), per-language TOML catalogs; a new language is one new TOML file, auto-discovered, enforced by key-parity and no-hardcoded-copy tests
- Source manager (`F2`): enable sources on demand, per-source feed `hide` config, subscription managers for RSS / Reddit / Telegram
- Editor-inspired VS Code Dark+ theme; every keybinding is a non-printing key (works while an IME is active); top tab bar and sidebar are clickable
- Layout-change self-check: 0 items parsed → immediate "site may have changed" error; `feedscope probe` inspects all sources with raw samples saved to disk; `feedscope doctor` environment self-check; `feedscope rss` CLI for subscription management

[0.5.0]: https://github.com/howardli/FeedScope/releases/tag/v0.5.0
[0.4.2]: https://github.com/howardli/FeedScope/releases/tag/v0.4.2
[0.4.1]: https://github.com/howardli/FeedScope/releases/tag/v0.4.1
[0.4.0]: https://github.com/howardli/FeedScope/releases/tag/v0.4.0
[0.3.1]: https://github.com/howardli/FeedScope/releases/tag/v0.3.1
[0.3.0]: https://github.com/howardli/FeedScope/releases/tag/v0.3.0
[0.2.0]: https://github.com/howardli/FeedScope/releases/tag/v0.2.0
[0.1.0]: https://github.com/howardli/FeedScope/releases/tag/v0.1.0
