"""Tests for config entry setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arbor import async_remove_config_entry_device
from custom_components.arbor.api import ArborApiError, ArborAuthError
from custom_components.arbor.const import CONF_STUDENTS, DOMAIN

from .conftest import SCHOOL_DOMAIN, STUDENTS, mock_client


def _patch_client(client: MagicMock):
    return patch("custom_components.arbor.ArborApiClient", return_value=client)


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = mock_client()
    client.ensure_valid_token = AsyncMock(side_effect=ArborAuthError("bad"))

    with _patch_client(client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_setup_network_failure_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = mock_client()
    client.ensure_valid_token = AsyncMock(side_effect=aiohttp.ClientError("offline"))

    with _patch_client(client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_picks_up_new_child(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    both = [*STUDENTS, {"student_id": "202", "name": "Sam Example"}]
    client = mock_client()
    client.parse_students = MagicMock(return_value=both)

    with _patch_client(client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_STUDENTS] == both
    assert hass.states.get("todo.arbor_sam_example_sam_assignments") is not None


async def test_setup_keeps_students_when_discovery_fails(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = mock_client()
    client.get_dashboard = AsyncMock(side_effect=ArborApiError("500"))

    with _patch_client(client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_STUDENTS] == STUDENTS


async def test_only_departed_children_devices_are_removable(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    current = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "arbor_101")},
    )
    departed = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "arbor_999")},
    )

    school = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"arbor_school_{SCHOOL_DOMAIN}")},
    )

    assert not await async_remove_config_entry_device(hass, config_entry, current)
    assert not await async_remove_config_entry_device(hass, config_entry, school)
    assert await async_remove_config_entry_device(hass, config_entry, departed)


async def test_clubs_and_messages_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = mock_client(
        get_clubs=AsyncMock(
            return_value={
                "registered": [{"name": "Chess", "details": "", "url": ""}],
                "available": [],
            }
        ),
        get_school_messages=AsyncMock(
            return_value=[
                {
                    "id": "9",
                    "subject": "Sports day",
                    "preview": "Hi Parents...",
                    "received": "2026-09-14T10:34:00",
                }
            ]
        ),
    )

    with _patch_client(client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    clubs = hass.states.get("sensor.arbor_alex_example_alex_clubs")
    assert clubs.state == "1"
    assert clubs.attributes["clubs"][0]["name"] == "Chess"
    assert (
        hass.states.get("sensor.arbor_alex_example_alex_clubs_available").state == "0"
    )

    latest = hass.states.get("sensor.arbor_example_school_latest_school_message")
    assert latest.attributes["subject"] == "Sports day"
    assert latest.attributes["message_id"] == "9"
    assert latest.state.startswith("2026-09-14T")
