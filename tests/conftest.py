"""Shared fixtures for Arbor School integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arbor.const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_SCHOOL_NAME,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)

SCHOOL_DOMAIN = "example-school.uk.arbor.sc"
STUDENTS = [{"student_id": "101", "name": "Alex Example"}]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load custom_components/arbor in every test."""


def auth_result(domain: str = SCHOOL_DOMAIN) -> dict[str, Any]:
    """Return a successful ArborApiClient.authenticate() payload."""
    return {
        "school_domain": domain,
        "school_name": "Example School",
        "refresh_token": "new-refresh",
        "access_token": "new-access",
        "token_expiry": 9_999_999_999.0,
    }


def mock_client(**overrides: Any) -> MagicMock:
    """Return an ArborApiClient double whose calls all succeed with empty data."""
    client = MagicMock()
    client.school_domain = SCHOOL_DOMAIN
    client.refresh_token = "old-refresh"
    client.access_token = "old-access"
    client.token_expiry = 9_999_999_999.0
    client.authenticate = AsyncMock(return_value=auth_result())
    client.ensure_valid_token = AsyncMock()
    client.get_dashboard = AsyncMock(return_value={})
    client.parse_students = MagicMock(return_value=STUDENTS)
    client.discover_academic_year_id = AsyncMock(return_value="24")
    client.get_kpis = AsyncMock(return_value={})
    client.get_assignment_counts = AsyncMock(return_value={})
    client.get_assignments_due = AsyncMock(return_value=[])
    client.get_assignments_overdue = AsyncMock(return_value=[])
    client.get_assignments_submitted = AsyncMock(return_value=[])
    client.get_calendar = AsyncMock(return_value=[])
    client.get_clubs = AsyncMock(return_value={"registered": [], "available": []})
    client.get_school_messages = AsyncMock(return_value=[])
    client.get_school_message = AsyncMock(return_value={})
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry as created by the user step."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Example School",
        unique_id=f"arbor_{SCHOOL_DOMAIN}",
        data={
            CONF_USERNAME: "parent@example.com",
            CONF_PASSWORD: "old-password",
            CONF_SCHOOL_DOMAIN: SCHOOL_DOMAIN,
            CONF_SCHOOL_NAME: "Example School",
            CONF_REFRESH_TOKEN: "old-refresh",
            CONF_ACCESS_TOKEN: "old-access",
            CONF_TOKEN_EXPIRY: 9_999_999_999.0,
            CONF_STUDENTS: STUDENTS,
            CONF_ACADEMIC_YEAR_ID: "24",
        },
    )
