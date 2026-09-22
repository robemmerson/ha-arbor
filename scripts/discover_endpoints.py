"""Map the Arbor guardian pages reachable from the dashboard.

Used to find the endpoints and widget shapes for features the integration
doesn't cover yet (clubs, messages, ...). GET requests only.

Run from the repo root:

    python3 -m scripts.discover_endpoints

Environment:

    ARBOR_USERNAME / ARBOR_PASSWORD   credentials (prompted if unset)
    ARBOR_DISCOVER_KEYWORDS           comma-separated, default "club,message,communication,inbox,notice"
    ARBOR_DISCOVER_MAX_PAGES          crawl limit, default 80

Output (in scripts/arbor-discovery/, gitignored):

    urls.txt                   every guardian URL found, IDs templated as {id}
    skeleton/<page>.json       keyword pages with free text redacted — labels may still
                               contain names, so review before sharing
    raw/<page>.json            keyword pages verbatim — contains personal data, keep local
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import deque
from getpass import getpass
from pathlib import Path
from typing import Any

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.arbor.api import ArborApiClient, ArborApiError

OUT_DIR = Path(__file__).parent / "arbor-discovery"
SEED_PATH = "/guardians/home-ui/dashboard"
# Never follow links that look like they change state, even though we only GET.
UNSAFE = re.compile(
    r"delete|remove|save|submit|send|logout|pay|update|create|cancel|book", re.I
)
URL_IN_TEXT = re.compile(r"""(?:href|url)=["']?(/guardians/[^"'\s>]+)""")
# Attribute keys whose values describe page structure rather than personal content.
STRUCTURAL_KEYS = {"name", "label", "url", "type", "icon", "selected", "variant"}


def _collect_urls(node: Any, found: set[str]) -> None:
    """Gather /guardians/ paths from url attributes and embedded HTML."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                if key == "url" and value.startswith("/guardians/"):
                    found.add(value.split("#")[0])
                found.update(m.split("#")[0] for m in URL_IN_TEXT.findall(value))
            else:
                _collect_urls(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_urls(item, found)


def _template(path: str) -> str:
    return re.sub(r"/\d+(?=/|$)", "/{id}", path)


def _redact(node: Any, key: str = "") -> Any:
    """Keep keys and structural values; replace free text with its length."""
    if isinstance(node, dict):
        return {k: _redact(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact(v) for v in node[:5]] + (
            [f"<{len(node) - 5} more>"] if len(node) > 5 else []
        )
    if isinstance(node, str):
        if key in STRUCTURAL_KEYS:
            return _template(node)[:80]
        return f"<str {len(node)}>"
    return node


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _template(path).lower()).strip("_")[:120]


async def _fetch(
    session: aiohttp.ClientSession, client: ArborApiClient, path: str
) -> tuple[int, Any]:
    await client.ensure_valid_token()
    url = f"https://{client.school_domain}{path}"
    async with session.get(url, headers=client._auth_headers) as resp:
        if resp.status != 200:
            return resp.status, None
        try:
            return resp.status, await resp.json(content_type=None)
        except ValueError:
            return resp.status, None


async def _run() -> int:
    username = os.environ.get("ARBOR_USERNAME") or input("Arbor username: ")
    password = os.environ.get("ARBOR_PASSWORD") or getpass("Arbor password: ")
    keywords = [
        k.strip().lower()
        for k in os.environ.get(
            "ARBOR_DISCOVER_KEYWORDS", "club,message,communication,inbox,notice"
        ).split(",")
        if k.strip()
    ]
    max_pages = int(os.environ.get("ARBOR_DISCOVER_MAX_PAGES", "80"))

    (OUT_DIR / "raw").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "skeleton").mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        client = ArborApiClient(session)
        auth = await client.authenticate(username, password)
        print(f"Logged in to {auth['school_name']} ({auth['school_domain']})")

        queue: deque[str] = deque([SEED_PATH])
        seen: set[str] = {SEED_PATH}
        all_urls: set[str] = set()
        matches: list[tuple[str, int]] = []
        fetched = 0

        while queue and fetched < max_pages:
            path = queue.popleft()
            status, data = await _fetch(session, client, path)
            fetched += 1
            if data is None:
                print(f"  [{status}] {_template(path)}")
                continue

            found: set[str] = set()
            _collect_urls(data, found)
            all_urls.update(found)
            for link in sorted(found):
                if link not in seen and not UNSAFE.search(link):
                    seen.add(link)
                    queue.append(link)

            text = json.dumps(data).lower()
            hit = [k for k in keywords if k in path.lower() or k in text]
            marker = f"  <-- {', '.join(hit)}" if hit else ""
            print(f"  [{status}] {_template(path)}{marker}")
            if hit:
                slug = _slug(path)
                (OUT_DIR / "raw" / f"{slug}.json").write_text(
                    json.dumps(data, indent=2)
                )
                (OUT_DIR / "skeleton" / f"{slug}.json").write_text(
                    json.dumps(_redact(data), indent=2)
                )
                matches.append((path, status))

    templated = sorted({_template(u) for u in all_urls})
    (OUT_DIR / "urls.txt").write_text("\n".join(templated) + "\n")

    print(f"\nFetched {fetched} page(s); {len(templated)} distinct URL pattern(s).")
    print(f"Wrote {OUT_DIR / 'urls.txt'}")
    if matches:
        print("Pages mentioning keywords (review skeletons before sharing):")
        for path, _status in matches:
            print(f"  {_template(path)}  ->  skeleton/{_slug(path)}.json")
    else:
        print("No pages matched the keywords; check urls.txt for likely candidates.")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except ArborApiError as exc:
        print(f"\nAPI error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
