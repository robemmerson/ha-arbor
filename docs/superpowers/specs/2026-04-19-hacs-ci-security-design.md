# HACS CI and Security Scanning — Design

Date: 2026-04-19
Author: Claude (for @robemmerson)

## Problem

`ha-arbor` is a public HACS integration but has no CI, no HACS or `hassfest`
validation, no dependency or secret scanning, and no release automation. All
merges and releases are manual. Since the owner works on this asynchronously
and will often be away, the repo needs guard-rails that run without human
attention and a release path that requires minimal touch.

## Goals

1. Validate the repo against the official HACS + Home Assistant rules on every
   change and on a weekly schedule (catches ecosystem drift).
2. Apply standard security scanning (SAST, secret scanning, dependency CVEs,
   Python security linter).
3. Automate patch/minor dependency updates with auto-merge so the repo doesn't
   rot while the owner is away.
4. Automate releases via Conventional Commits (`release-please`) so shipping a
   new version is "merge a PR."
5. Apply HACS best-practice repo hygiene (info.md, badge, pinned version).
6. Enforce the above via branch protection on `main`.

## Non-goals

- Adding a pytest suite. No tests exist today; scaffolding empty tests adds
  noise without value. Deferred to a separate effort.
- Publishing to the default HACS store (would require the
  `home-assistant/brands` PR — deferred).
- Changing runtime behaviour of the integration.

## Decisions (from brainstorm Q&A, 2026-04-19)

| # | Decision |
|---|----------|
| Q1 | Standard security: CodeQL + Dependabot + Gitleaks + Bandit + pip-audit |
| Q2 | No pytest scaffolding |
| Q3 | `release-please` for automated Conventional-Commit releases |
| Q4 | Dependabot auto-merge enabled for safe updates |
| Q5 | Add `.pre-commit-config.yaml` |
| Q6 | Configure branch protection via `gh` after CI is green once |
| Q7 | Bump `manifest.json` version to `1.0.1` |

## File inventory

```
.github/
  dependabot.yml
  release-please-config.json
  .release-please-manifest.json
  workflows/
    validate.yml              # hassfest + hacs/action
    lint.yml                  # Ruff check + format
    codeql.yml                # CodeQL (python)
    security.yml              # Bandit + pip-audit + Gitleaks
    release-please.yml        # automated release PRs, tag + zip asset on merge
    dependabot-automerge.yml  # auto-merge safe Dependabot PRs
.pre-commit-config.yaml
pyproject.toml                # Ruff config
info.md                       # HACS store content
README.md                     # + HACS badge
custom_components/arbor/manifest.json  # version -> 1.0.1
```

## Workflow design

All workflows share these security properties:

- **Least privilege:** explicit `permissions:` block per workflow, defaulting
  to `contents: read`. Write permissions only where strictly needed.
- **SHA pinning:** every third-party action is pinned to a commit SHA with
  the version as a trailing comment (Dependabot `github-actions` updates the
  SHAs on a schedule).
- **Concurrency:** each workflow cancels in-progress runs on the same ref so
  we don't waste minutes on rapid pushes.
- **Triggers:** `push` to main, `pull_request`, and a weekly `schedule` cron
  for drift detection on validation and security jobs.

### validate.yml (HACS best practices)
Runs `home-assistant/actions/hassfest` and `hacs/action` (category:
`integration`). These validate `manifest.json`, `hacs.json`, translations,
iot_class, manifest keys, and more. This is the same pair the official
`home-assistant-core` and HACS docs recommend for custom integrations.

### lint.yml (code quality)
Runs `ruff check` and `ruff format --check` against `custom_components/` and
`scripts/`. Ruff config lives in `pyproject.toml`. Target Python 3.13 (HA's
current minimum for 2026.x).

### codeql.yml (SAST)
Uses `github/codeql-action` with language `python`. Uploads results to the
GitHub Security tab.

### security.yml (defense-in-depth)
Three parallel jobs:
- **bandit** — runs `bandit -r custom_components/` with a minimal config
- **pip-audit** — installs `manifest.json` requirements and audits for CVEs
- **gitleaks** — runs `gitleaks/gitleaks-action` across the full history on
  push and against the diff on PRs

### release-please.yml
On push to `main`, `googleapis/release-please-action` either opens/updates a
release PR or, when a release PR is merged, creates a git tag and GitHub
Release. A follow-up job zips `custom_components/arbor` as
`arbor.zip` and attaches it to the release so HACS can consume it directly
(this is the HACS-recommended artefact shape).

### dependabot-automerge.yml
For Dependabot PRs that are patch updates (and minor updates for
`github-actions` only), enable auto-merge once all required checks pass.
Major updates always require manual review.

## Dependabot config

Two ecosystems, weekly schedule:

- `pip` — on `custom_components/arbor/manifest.json` via the `pip` ecosystem
  targeted at the manifest directory. (Dependabot understands HA manifests
  via the `pip` ecosystem as long as we point it at a `requirements.txt`-
  shaped file. For a custom_component the idiomatic pattern is to also add
  a `custom_components/arbor/requirements.txt` mirror; we'll skip that and
  rely on GitHub's native HA manifest support when present, otherwise fall
  back to updating `aiohttp` pin in the manifest by hand.)
- `github-actions` — on `/` and `.github/workflows/`.

Grouped updates so auto-merge doesn't spam 10 PRs for one Actions release.

## Ruff configuration

Pragmatic middle ground — stricter than defaults, looser than
`homeassistant/core` so the first PR is green without refactoring the
integration. Starting rule set:

```
E, F, W       # pycodestyle / pyflakes
I             # isort
UP            # pyupgrade (we target py313)
B             # bugbear
SIM           # simplify
RUF           # ruff-specific
```

Line length 88, target `py313`. Can be tightened in a follow-up.

## Pre-commit

Mirrors CI: Ruff check + format, plus `check-yaml`, `end-of-file-fixer`,
`trailing-whitespace`, `check-merge-conflict`, and a pinned Gitleaks hook.
Developers run `pre-commit install` once.

## HACS best practices checklist

- [x] `manifest.json` has `domain`, `name`, `codeowners`, `version`,
      `documentation`, `issue_tracker`, `iot_class`, `requirements`
- [x] `hacs.json` present and valid (name, render_readme, iot_class,
      homeassistant minimum, domains)
- [ ] `info.md` — added by this work; short store-front description
- [ ] HACS badge in README — added by this work
- [ ] `hassfest` + `hacs/action` in CI — added by this work
- [ ] `manifest.json.version` bumped each release — automated by
      release-please (this PR bumps to `1.0.1`)

## Branch protection plan

Once the PR lands and CI has run green once on `main`, configure via
`gh api`:

- Require these status checks: `hassfest`, `hacs`, `ruff`,
  `codeql-analyze`, `bandit`, `pip-audit`, `gitleaks`.
- Require PR before merging, 1 review (owner can self-approve via admin
  bypass).
- Linear history enabled; force pushes disabled.
- Admin bypass enabled so the owner can always recover.

## Rollout sequence

1. Land everything on `ci/add-hacs-and-security-workflows` in one PR.
2. Run CI. Fix findings inline before merge.
3. Merge to `main`.
4. Apply branch protection.
5. First release PR from `release-please` will land automatically after
   any subsequent Conventional Commit. Owner merges it to release.

## Risks / trade-offs

- **Gitleaks full-history scan on large repos is slow.** This repo is small,
  so acceptable. If it ever slows CI noticeably, scope it to the diff.
- **Auto-merge on patch updates can silently break the integration.**
  Mitigated by: CI must pass first, and `aiohttp` is the only pip
  dependency. Worst case: a patch release of `aiohttp` breaks something, the
  Gitleaks/hassfest jobs stay green, and no test catches it. Acceptable for
  a no-test repo — the blast radius is bounded to HA install failures which
  are immediately visible to the owner (who uses the integration daily).
- **Release-please requires Conventional Commits from this point forward.**
  Past commits don't need to be re-written. First release PR will cover all
  changes since the last tag.
