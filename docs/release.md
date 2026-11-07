# Release runbook

How a FeedScope release gets out. Once the one-time setup below is done, a
release is three commands.

## One-time setup

### 1. PyPI trusted publishing (OIDC — no tokens anywhere)

On [pypi.org](https://pypi.org) (account that owns / will own the
`feedscope` project):

1. If the project doesn't exist yet: **Account settings → Publishing → Add a
   new pending publisher**
   - PyPI project name: `feedscope`
   - Owner: `howardli`, Repository: `FeedScope`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. If it already exists: **Project → Settings → Publishing** and add the same
   publisher there.

### 2. The `pypi` GitHub environment

Repo **Settings → Environments → New environment** → name it `pypi`. No
reviewers required; it exists so the trusted publisher has a stable
environment name and you can add protection rules later if more maintainers
join.

### 3. GitHub Release permissions

Nothing to do — `GITHUB_TOKEN` with `contents: write` (declared in
`release.yml`) creates the release.

## Cutting a release

```bash
# 1. Changelog: move the [Unreleased] entries into a new version section
#    (Keep a Changelog style) and set its date.

# 2. Bump the version — single source of truth:
#    src/feedscope/__init__.py  (__version__ = "0.2.0")

# 3. Tag and push:
git commit -am "release: v0.2.0"
git tag v0.2.0
git push origin main --tags
```

Pushing the tag triggers `.github/workflows/release.yml`: build (sdist +
wheel) → publish to PyPI → GitHub Release with generated notes and the
artifacts attached.

Verify after the run:

```bash
pipx install feedscope   # in a clean shell, then:
feedscope doctor
```

## Versioning policy

Semantic Versioning. While `0.x`: new sources and features land in minor
bumps, and **the config file format (`sources.toml`) may gain fields but
existing fields keep their meaning**; breaking config changes wait for `1.0`.
Site-layout breakage caused by upstream redesigns is wear, not a semver event
— patch releases may ship new parsers for a changed site.
