"""Standalone local test for the Arbor API client.

Run from the repo root:

    python -m scripts.test_api

Credentials can be supplied via environment variables or interactively:

    ARBOR_USERNAME   email / username for the Arbor guardian account
    ARBOR_PASSWORD   password (falls back to getpass prompt)

Optionally limit which students / sections run:

    ARBOR_STUDENT_INDEX=0          only the first student
    ARBOR_SKIP_CALENDAR=1          skip the calendar fetch
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date
from getpass import getpass

import aiohttp

# Allow running as `python scripts/test_api.py` from the repo root too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.arbor.api import ArborApiClient, ArborApiError


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


async def _run() -> int:
    username = os.environ.get("ARBOR_USERNAME") or input("Arbor username: ")
    password = os.environ.get("ARBOR_PASSWORD") or getpass("Arbor password: ")

    skip_calendar = os.environ.get("ARBOR_SKIP_CALENDAR") == "1"
    student_index_env = os.environ.get("ARBOR_STUDENT_INDEX")
    only_index = int(student_index_env) if student_index_env else None

    async with aiohttp.ClientSession() as session:
        client = ArborApiClient(session)

        _section("Authenticate")
        auth = await client.authenticate(username, password)
        print(f"School:  {auth['school_name']}")
        print(f"Domain:  {auth['school_domain']}")
        print(f"Token expires in ~{int(auth['token_expiry'] - __import__('time').time())}s")

        _section("Dashboard → students")
        dashboard = await client.get_dashboard()
        students = client.parse_students(dashboard)
        if not students:
            print("No students found on the dashboard.")
            return 2
        for i, s in enumerate(students):
            print(f"  [{i}] {s['name']}  (id={s['student_id']})")

        targets = (
            [students[only_index]] if only_index is not None else students
        )

        for s in targets:
            sid = s["student_id"]
            name = s["name"]

            _section(f"{name} ({sid}) → academic year")
            year_id = await client.discover_academic_year_id(sid)
            print(f"  academic_year_id = {year_id}")

            _section(f"{name} → KPIs")
            kpis = await client.get_kpis(sid)
            for k, v in kpis.items():
                print(f"  {k:>22} : {v}")

            _section(f"{name} → assignment counts")
            counts = await client.get_assignment_counts(sid, year_id)
            for k, v in counts.items():
                print(f"  {k:>10} : {v}")

            _section(f"{name} → assignments due (first 5)")
            for a in (await client.get_assignments_due(sid, year_id))[:5]:
                print(f"  • {a['title']} — {a['due_date']} [{a['status']}]")

            _section(f"{name} → assignments overdue (first 5)")
            for a in (await client.get_assignments_overdue(sid, year_id))[:5]:
                print(f"  • {a['title']} — {a['due_date']} [{a['status']}]")

            if not skip_calendar:
                _section(f"{name} → calendar (today)")
                lessons = await client.get_calendar(sid, for_date=date.today())
                if not lessons:
                    print("  (no lessons)")
                for l in lessons:
                    print(
                        f"  {l['start']} – {l['end']}  "
                        f"{l['subject']}  @ {l['location']}"
                    )

        _section("Token refresh round-trip")
        client.token_expiry = 0  # force refresh on the next call
        await client.ensure_valid_token()
        print("  refresh OK, new token acquired")

    print("\nAll calls completed successfully.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("ARBOR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run())
    except ArborApiError as exc:
        print(f"\nAPI error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
