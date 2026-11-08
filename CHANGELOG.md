# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Unread management: per-feed unread badges in the sidebar (recounted on refresh, decremented live as you read — reads from the favorites list decrement the home feed too), `F4` unread-only view (session toggle; read rows vanish instantly, newsboat-style), and `Ctrl+A` mark-all-read for the current feed with a light confirmation
- UI themes: `Ctrl+T` switches instantly (pure presentation, no restart) between the built-in `editor-dark` (the existing VS Code Dark+ palette) and a new `editor-light`; a new theme is one TOML file in `ui/themes/`, auto-discovered, with completeness pinned by tests
- `Ctrl+Y` copies the current item's link from the list, favorites, details or reply layers (reply layers copy the floor-direct link), and read items get a dimmed title so read/unread separates at a glance

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

[0.2.0]: https://github.com/howardli/FeedScope/releases/tag/v0.2.0
[0.1.0]: https://github.com/howardli/FeedScope/releases/tag/v0.1.0
