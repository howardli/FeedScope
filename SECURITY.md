# Security Policy

## Supported versions

Only the latest release line receives security fixes.

## What FeedScope stores on your machine

FeedScope handles your login sessions for Zhihu, X and Reddit. Here is exactly
where the sensitive bits live, so you can audit that claim:

| Data | Location | Contains credentials? |
| --- | --- | --- |
| User config | `sources.toml` in your platform config dir (`~/.config/feedscope/` on Linux, `~/Library/Application Support/feedscope/` on macOS) | No — language, enabled sources, subscriptions only |
| Browser profile | `chromium-profile/` in your platform cache dir (`~/.cache/feedscope/` on Linux, `~/Library/Caches/feedscope/` on macOS) | **Yes** — Chrome profile with your Zhihu / X / Reddit session cookies |
| Read state | `state.json` in your platform state dir (`~/.local/state/feedscope/` on Linux) | No — item ids, timestamps and the last reading position (source / feed / selected item id) |
| Favorites | `favorites.json` next to `state.json` | No — snapshots (title/link/body, plus the detail saved when starring from a reading screen) of items you starred |
| List cache | `feeds/` next to the browser profile in your platform cache dir | No — titles, links and bodies of the public feeds you subscribe to |
| Instance lock | `feedscope.lock` next to `state.json` | No — an empty file held open to warn a second instance on the same state |

Guarantees:

- No credentials ship with the project and none are ever transmitted anywhere
  except the sites you logged into (by their own pages, in your own browser)
- No telemetry, analytics or crash reporting — the app makes no network calls
  that are not fetching the feeds you asked for
- The browser profile is a standard Chrome profile; deleting the directory
  wipes all login state
- `feedscope probe` saves raw samples from your feeds to disk when inspecting
  a source — review those files before sharing them in an issue
- The optional `browser` / `clipboard` config keys run a command of your own
  choosing when you open a link or copy one; nothing is executed that you did
  not configure
- A damaged `state.json` / `favorites.json` is never overwritten silently: it
  is renamed to `<name>.corrupt-<timestamp>` and left for you to inspect

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting**: Security tab →
"Report a vulnerability". That reaches the maintainer privately and lets us
coordinate a fix and disclosure.

Please do **not** open a public issue for anything security-sensitive,
including: credential storage weaknesses, injection into the browser channel,
or ways site content could execute something unexpected in the terminal.

Expect an initial response within 7 days. Once a fix is released the reporter
is credited in the [changelog](CHANGELOG.md) unless they prefer otherwise.
