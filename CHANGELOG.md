# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/howardli/FeedScope/releases/tag/v0.1.0
