"""Tests for ArborApiClient parsing helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.arbor.api import ArborApiClient


def _year_toggle(selected_year: str) -> dict:
    return {
        "name": "page",
        "children": [
            {
                "name": "page-toggle",
                "attributes": {"label": "Academic year"},
                "children": [
                    {
                        "attributes": {
                            "selected": "1" if year == selected_year else "0",
                            "url": f"/guardians/x/academic-year-id/{year}",
                        }
                    }
                    for year in ("24", "25")
                ],
            }
        ],
    }


def test_parse_academic_year_id_returns_selected_year() -> None:
    client = ArborApiClient(MagicMock())
    assert client.parse_academic_year_id(_year_toggle("25")) == "25"


async def test_discover_academic_year_id_returns_none_when_missing() -> None:
    client = ArborApiClient(MagicMock())
    client._get = AsyncMock(return_value={"name": "page", "children": []})

    assert await client.discover_academic_year_id("101") is None


NBSP = "\xa0"


def _message_row(message_id: str, content: str, received: str) -> dict:
    return {
        "name": "property-row",
        "attributes": {"description": received, "label": "Message from school"},
        "children": [
            {"name": "property-row-content", "attributes": [], "content": content},
            {
                "name": "event-listener",
                "attributes": {
                    "action": "load-page",
                    "url": "/guardians/outbound-in-app-message-ui/"
                    f"view-outbound-in-app-message/id/{message_id}",
                },
            },
        ],
    }


def test_parse_school_messages() -> None:
    page = {
        "name": "page",
        "attributes": [],
        "children": [
            {
                "name": "section",
                "attributes": {"title": "School Messages"},
                "children": [
                    _message_row(
                        "412",
                        "      Book Fair &amp; Bake Sale      Hi Parents/Carer, We...   ",
                        f"14{NBSP}September{NBSP}2026,{NBSP}10:34",
                    ),
                    _message_row("7", "   Term dates   ", "not a date"),
                ],
            }
        ],
    }

    messages = ArborApiClient._parse_school_messages(page)

    assert messages == [
        {
            "id": "412",
            "subject": "Book Fair & Bake Sale",
            "preview": "Hi Parents/Carer, We...",
            "received": "2026-09-14T10:34:00",
        },
        {"id": "7", "subject": "Term dates", "preview": "", "received": ""},
    ]


def test_parse_school_message_detail() -> None:
    page = {
        "name": "slide-over",
        "attributes": {"backButton": "true", "title": "Book Fair"},
        "children": [
            {
                "name": "section",
                "attributes": [],
                "children": [
                    {
                        "name": "property-row",
                        "attributes": {"label": "Subject", "value": "Book Fair"},
                    },
                    {
                        "name": "property-row",
                        "attributes": {
                            "label": "Received",
                            "value": "22 June 2026, 07:51",
                        },
                    },
                    {
                        "name": "property-row",
                        "attributes": {"label": "Sent by", "value": "School Office"},
                    },
                ],
            },
            {
                "name": "section",
                "attributes": {"title": "Message"},
                "children": [
                    {
                        "name": "simple-text",
                        "attributes": {},
                        "content": "Dear Parents,&#10;See you there.",
                    }
                ],
            },
        ],
    }

    assert ArborApiClient._parse_school_message("412", page) == {
        "id": "412",
        "subject": "Book Fair",
        "received": "2026-06-22T07:51:00",
        "sent_by": "School Office",
        "body": "Dear Parents,\nSee you there.",
    }


def _club_section(title: str, rows: list[dict]) -> dict:
    return {
        "name": "section",
        "attributes": {"emptyText": "None", "title": title},
        "children": rows,
        "content": None,
    }


def test_parse_clubs_with_no_clubs() -> None:
    # Shape of a real club dashboard for a student with no clubs.
    page = {
        "name": "page",
        "attributes": [],
        "children": [
            {"name": "sub-nav-column", "attributes": {"label": "Alex's page"}},
            {
                "name": "column",
                "attributes": [],
                "children": [
                    {
                        "name": "column-title",
                        "attributes": [],
                        "content": "School Clubs",
                    },
                    _club_section("Alex is registered for these clubs 2026/2027", []),
                    _club_section(
                        "Alex Can be Registered For These Clubs (2026/2027)", []
                    ),
                ],
            },
        ],
    }

    assert ArborApiClient._parse_clubs(page) == {"registered": [], "available": []}


def test_parse_clubs_rows_go_to_matching_bucket() -> None:
    row = {
        "name": "property-row",
        "attributes": {"description": "Tuesdays 15:30"},
        "children": [
            {"name": "property-row-content", "content": "<b>Chess&nbsp;Club</b>"},
            {"name": "event-listener", "attributes": {"url": "/guardians/club-ui/x"}},
        ],
    }
    page = {
        "name": "column",
        "children": [
            _club_section("Alex is registered for these clubs 2026/2027", [row]),
            _club_section(
                "Alex Can be Registered For These Clubs (2026/2027)",
                [{"name": "property-row", "attributes": {"label": "Choir"}}],
            ),
        ],
    }

    assert ArborApiClient._parse_clubs(page) == {
        "registered": [
            {
                "name": "Chess Club",
                "details": "Tuesdays 15:30",
                "url": "/guardians/club-ui/x",
            }
        ],
        "available": [{"name": "Choir", "details": "", "url": ""}],
    }
