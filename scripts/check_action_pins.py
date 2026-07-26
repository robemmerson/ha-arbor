#!/usr/bin/env python3
"""Verify every third-party action is SHA-pinned and correctly labelled.

The repo pins actions to immutable commit SHAs and records the human-readable
ref in a trailing comment:

    uses: actions/checkout@9c091bb...  # v7.0.0

The SHA is what actually runs; the comment is the only thing a reviewer reads.
When the two drift apart the comment stops being documentation and starts
being misinformation -- and Dependabot, which rewrites that comment by
substring-substituting the old version, mangles it (a stale `# v4.1.4` on a
`4.4.1` pin became `# v5.0.0.1.5.0.0` when bumped to 5.0.0).

Checks, in order:

1. every `uses:` on a third-party action pins a full 40-hex commit SHA
2. every pin carries a trailing `# <ref>` comment, and nothing but a ref
   (a `# v2 (2026-04-19)` style date is rejected -- nothing updates it, so it
   rots silently)
3. that ref resolves to that SHA: a tag must point at exactly this commit, a
   branch must contain it

Step 3 needs the GitHub API. `--offline` skips it, which is what the
pre-commit hook runs; CI runs the full check.

Usage:
    python scripts/check_action_pins.py [--offline] [--fix]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

WORKFLOW_DIR = Path(".github/workflows")
API = "https://api.github.com"

# `uses: owner/repo[/sub/path]@ref` with an optional trailing comment.
USES_RE = re.compile(
    r"^(?P<indent>\s*(?:-\s*)?)uses:\s*"
    r"(?P<action>[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+)@(?P<ref>\S+)"
    r"(?P<spacing>\s*)(?:#(?P<comment>.*))?$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A bare tag or branch name -- no trailing prose, no parenthetical date.
REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
# A version-shaped label, e.g. v7, v7.0, v7.0.0. Anything with more numeric
# components than that is Dependabot's substring rewrite having gone wrong
# (`v4.1.4` bumped to `5.0.0` becomes `v5.0.0.1.5.0.0`).
VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)*$")


class Pin(NamedTuple):
    """One `uses:` line pinning a third-party action."""

    path: Path
    lineno: int
    action: str  # e.g. github/codeql-action/init
    repo: str  # e.g. github/codeql-action
    sha: str
    label: str | None  # the trailing comment, stripped


def find_pins(paths: list[Path]) -> tuple[list[Pin], list[str]]:
    """Collect action pins, reporting lines that fail checks 1 and 2."""
    pins: list[Pin] = []
    errors: list[str] = []

    for path in paths:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = USES_RE.match(line)
            if not match:
                continue

            action = match["action"]
            if action.startswith("./"):
                continue  # local action, nothing to pin

            where = f"{path}:{lineno}"
            ref = match["ref"]
            comment = match["comment"]
            label = comment.strip() if comment else None

            if not SHA_RE.match(ref):
                errors.append(
                    f"{where}: {action} is pinned to {ref!r}, not a 40-character "
                    f"commit SHA. Mutable refs let an upstream force-push change "
                    f"what runs here."
                )
                continue

            if label is None:
                errors.append(
                    f"{where}: {action} has no trailing '# <ref>' comment, so "
                    f"nothing records which version this SHA is."
                )
            elif not REF_RE.match(label):
                errors.append(
                    f"{where}: {action} label {label!r} is not a bare tag or "
                    f"branch name. Use '# v1.2.3' or '# main' -- extra text "
                    f"(dates, notes) is never updated and goes stale."
                )
                label = None
            elif VERSION_RE.match(label) and label.count(".") > 2:
                errors.append(
                    f"{where}: {action} label {label!r} has too many version "
                    f"components to be a real tag -- this is what a Dependabot "
                    f"bump against an already-stale label produces."
                )
                label = None

            # `owner/repo` is the first two path segments; the rest is a
            # sub-action directory (github/codeql-action/init).
            repo = "/".join(action.split("/")[:2])
            pins.append(Pin(path, lineno, action, repo, ref, label))

    return pins, errors


class Resolver:
    """Resolves refs to commit SHAs against the GitHub API, with caching."""

    def __init__(self, token: str | None) -> None:
        self.token = token
        self._cache: dict[tuple[str, str], str | None] = {}

    def _get(self, url: str) -> dict | list | None:
        request = urllib.request.Request(url)
        request.add_header("Accept", "application/vnd.github+json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise

    def tag_sha(self, repo: str, tag: str) -> str | None:
        """Commit SHA a tag points at, dereferencing annotated tags."""
        key = (repo, tag)
        if key in self._cache:
            return self._cache[key]

        ref = self._get(f"{API}/repos/{repo}/git/ref/tags/{tag}")
        sha = None
        if ref:
            obj = ref["object"]
            if obj["type"] == "tag":
                # Annotated tag: one more hop to reach the commit.
                annotated = self._get(f"{API}/repos/{repo}/git/tags/{obj['sha']}")
                sha = annotated["object"]["sha"] if annotated else None
            else:
                sha = obj["sha"]

        self._cache[key] = sha
        return sha

    def branch_contains(self, repo: str, branch: str, sha: str) -> bool | None:
        """True if `sha` is an ancestor of (or equal to) the branch tip."""
        result = self._get(f"{API}/repos/{repo}/compare/{branch}...{sha}")
        if result is None:
            return None
        # "identical" -> the tip; "behind" -> an ancestor of the tip.
        return result["status"] in {"identical", "behind"}

    def tag_for_sha(self, repo: str, sha: str) -> str | None:
        """Highest-listed tag pointing at this commit, for --fix."""
        tags = self._get(f"{API}/repos/{repo}/tags?per_page=100")
        if not tags:
            return None
        exact = [tag["name"] for tag in tags if tag["commit"]["sha"] == sha]
        if not exact:
            return None
        # Prefer the most specific tag: v7.0.0 over v7.
        return max(exact, key=len)

    def default_branch(self, repo: str) -> str | None:
        info = self._get(f"{API}/repos/{repo}")
        return info["default_branch"] if info else None

    def correct_label(self, repo: str, sha: str) -> str | None:
        """The label this SHA should carry: its tag, else the branch it's on.

        Some actions publish no tag Dependabot can resolve, so it tracks the
        default branch and bumps the SHA in place. Those pins are branch-
        labelled by design -- a version label on one is always a lie.
        """
        tag = self.tag_for_sha(repo, sha)
        if tag:
            return tag
        branch = self.default_branch(repo)
        if branch and self.branch_contains(repo, branch, sha):
            return branch
        return None


def verify(pins: list[Pin], resolver: Resolver) -> tuple[list[str], dict[Pin, str]]:
    """Check each label against the API. Returns errors and suggested fixes."""
    errors: list[str] = []
    fixes: dict[Pin, str] = {}

    for pin in pins:
        where = f"{pin.path}:{pin.lineno}"

        if pin.label is None:
            # Already reported by find_pins; still offer the correct label.
            actual = resolver.correct_label(pin.repo, pin.sha)
            if actual:
                fixes[pin] = actual
            continue

        tag_sha = resolver.tag_sha(pin.repo, pin.label)
        if tag_sha is not None:
            if tag_sha == pin.sha:
                continue
            actual = resolver.correct_label(pin.repo, pin.sha)
            hint = f" (this SHA is {actual})" if actual else ""
            errors.append(
                f"{where}: {pin.action} is labelled '# {pin.label}', but that "
                f"tag points at {tag_sha[:12]}, not the pinned {pin.sha[:12]}"
                f"{hint}."
            )
            if actual:
                fixes[pin] = actual
            continue

        # Not a tag -- try it as a branch.
        contains = resolver.branch_contains(pin.repo, pin.label, pin.sha)
        if contains is True:
            continue
        if contains is False:
            errors.append(
                f"{where}: {pin.action} is labelled '# {pin.label}', but branch "
                f"{pin.label} does not contain the pinned {pin.sha[:12]}."
            )
        else:
            errors.append(
                f"{where}: {pin.action} label '# {pin.label}' matches no tag or "
                f"branch in {pin.repo}."
            )
        actual = resolver.correct_label(pin.repo, pin.sha)
        if actual:
            fixes[pin] = actual
            errors[-1] += f" This SHA is {actual}."

    return errors, fixes


def apply_fixes(fixes: dict[Pin, str]) -> None:
    """Rewrite trailing comments in place."""
    by_file: dict[Path, list[tuple[Pin, str]]] = {}
    for pin, label in fixes.items():
        by_file.setdefault(pin.path, []).append((pin, label))

    for path, items in by_file.items():
        lines = path.read_text().splitlines(keepends=True)
        for pin, label in items:
            index = pin.lineno - 1
            match = USES_RE.match(lines[index].rstrip("\n"))
            if not match:
                continue
            spacing = match["spacing"] or "  "
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = (
                f"{match['indent']}uses: {match['action']}@{match['ref']}"
                f"{spacing}# {label}{newline}"
            )
            print(f"fixed {path}:{pin.lineno} -> # {label}")
        path.write_text("".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="only check SHA pinning and label shape; skip API verification",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite incorrect labels using the version the SHA really is",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="workflow files to check (default: all of .github/workflows)",
    )
    args = parser.parse_args()

    paths = args.files or sorted(
        [*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")]
    )
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("no workflow files found", file=sys.stderr)
        return 1

    pins, errors = find_pins(paths)

    if not args.offline:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print(
                "warning: GITHUB_TOKEN unset; unauthenticated API calls are "
                "rate-limited to 60/hour",
                file=sys.stderr,
            )
        resolver = Resolver(token)
        try:
            api_errors, fixes = verify(pins, resolver)
        except urllib.error.HTTPError as err:
            print(f"error: GitHub API request failed: {err}", file=sys.stderr)
            return 2
        errors += api_errors

        if args.fix and fixes:
            apply_fixes(fixes)
            print(f"\napplied {len(fixes)} fix(es); re-run to verify")
            return 1

    if errors:
        print(f"{len(errors)} action pin problem(s):\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "\nEvery action must be pinned to a full commit SHA with a trailing "
            "'# <tag-or-branch>' comment naming what that SHA is.\n"
            "Run 'GITHUB_TOKEN=... python scripts/check_action_pins.py --fix' "
            "to correct the labels.",
            file=sys.stderr,
        )
        return 1

    scope = "SHA-pinned and labelled" if args.offline else "verified against upstream"
    print(f"{len(pins)} action pin(s) {scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
