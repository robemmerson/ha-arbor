"""Tests for academic-year rollover and polling interval in the coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.arbor.api import ArborApiError
from custom_components.arbor.const import (
    CONF_ACADEMIC_YEAR_ID,
    DATA_ACCOUNT,
    DATA_ASSIGNMENTS_DUE,
    DATA_CALENDAR,
    DATA_KPIS,
    DATA_SCHOOL_MESSAGES,
    EVENT_SCHOOL_MESSAGE,
    MESSAGE_DETAIL_LIMIT,
)
from custom_components.arbor.coordinator import ArborDataUpdateCoordinator

from .conftest import mock_client


def _mock_client(year_id: str | None = "25", **overrides: object) -> MagicMock:
    return mock_client(
        discover_academic_year_id=AsyncMock(return_value=year_id), **overrides
    )


def _message(message_id: str, subject: str = "Subject") -> dict[str, str]:
    return {
        "id": message_id,
        "subject": subject,
        "preview": "Hi Parents...",
        "received": "2026-09-14T10:34:00",
    }


async def _poll(coordinator: ArborDataUpdateCoordinator) -> dict:
    """Run one poll and store the result like DataUpdateCoordinator does."""
    coordinator.data = await coordinator._async_update_data()
    return coordinator.data


async def test_new_academic_year_is_adopted_and_persisted(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(year_id="25")
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await coordinator._async_update_data()

    assert config_entry.data[CONF_ACADEMIC_YEAR_ID] == "25"
    client.get_assignments_due.assert_awaited_with("101", "25")
    assert data["101"][DATA_ASSIGNMENTS_DUE] == []


async def test_failed_year_discovery_keeps_stored_year(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client()
    client.discover_academic_year_id = AsyncMock(side_effect=ArborApiError("boom"))
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    await coordinator._async_update_data()

    assert config_entry.data[CONF_ACADEMIC_YEAR_ID] == "24"
    client.get_assignments_due.assert_awaited_with("101", "24")


async def test_undiscoverable_year_keeps_stored_year(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(year_id=None)
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    await coordinator._async_update_data()

    assert config_entry.data[CONF_ACADEMIC_YEAR_ID] == "24"


async def test_year_is_checked_once_per_day(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(year_id="24")
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    await coordinator._async_update_data()
    freezer.tick(timedelta(minutes=15))
    await coordinator._async_update_data()
    assert client.discover_academic_year_id.await_count == 1

    freezer.tick(timedelta(days=1))
    await coordinator._async_update_data()
    assert client.discover_academic_year_id.await_count == 2


async def test_failed_year_discovery_is_retried_next_poll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client()
    client.discover_academic_year_id = AsyncMock(
        side_effect=[ArborApiError("boom"), "25"]
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert config_entry.data[CONF_ACADEMIC_YEAR_ID] == "25"


async def test_scan_interval_comes_from_options(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SCAN_INTERVAL: 45}
    )

    coordinator = ArborDataUpdateCoordinator(hass, _mock_client(), config_entry)

    assert coordinator.update_interval == timedelta(minutes=45)


async def test_scan_interval_defaults_to_15_minutes(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)

    coordinator = ArborDataUpdateCoordinator(hass, _mock_client(), config_entry)

    assert coordinator.update_interval == timedelta(minutes=15)


async def test_network_error_during_year_check_does_not_fail_poll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client()
    client.discover_academic_year_id = AsyncMock(side_effect=TimeoutError())
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await coordinator._async_update_data()

    assert data["101"][DATA_ASSIGNMENTS_DUE] == []
    assert config_entry.data[CONF_ACADEMIC_YEAR_ID] == "24"


async def test_network_error_for_one_child_keeps_previous_values(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(
        get_kpis=AsyncMock(return_value={"attendance_year": 97}),
        get_assignments_due=AsyncMock(return_value=[{"title": "Essay"}]),
        get_calendar=AsyncMock(return_value=[{"subject": "Maths"}]),
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    await _poll(coordinator)

    client.get_kpis = AsyncMock(side_effect=aiohttp.ClientError("reset"))
    client.get_assignments_due = AsyncMock(side_effect=TimeoutError())
    client.get_calendar = AsyncMock(side_effect=aiohttp.ClientError("reset"))
    data = await _poll(coordinator)

    assert data["101"][DATA_KPIS] == {"attendance_year": 97}
    assert data["101"][DATA_ASSIGNMENTS_DUE] == [{"title": "Essay"}]
    # A stale timetable could be yesterday's, so it is cleared instead.
    assert data["101"][DATA_CALENDAR] == []


async def test_failure_before_any_data_uses_empty_values(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(get_kpis=AsyncMock(side_effect=aiohttp.ClientError()))
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await _poll(coordinator)

    assert data["101"][DATA_KPIS] == {}


async def test_every_request_failing_raises_update_failed(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    offline = AsyncMock(side_effect=aiohttp.ClientError("offline"))
    client = _mock_client(
        **{
            name: offline
            for name in (
                "get_kpis",
                "get_assignment_counts",
                "get_assignments_due",
                "get_assignments_overdue",
                "get_assignments_submitted",
                "get_calendar",
                "get_clubs",
                "get_school_messages",
            )
        }
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_existing_messages_are_not_announced_on_first_poll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client(get_school_messages=AsyncMock(return_value=[_message("1")]))
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await _poll(coordinator)
    await hass.async_block_till_done()

    assert events == []
    assert data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES] == [_message("1")]


async def test_new_message_fires_event_with_detail(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client(get_school_messages=AsyncMock(return_value=[_message("1")]))
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    await _poll(coordinator)

    client.get_school_messages = AsyncMock(
        return_value=[_message("3", "Newest"), _message("2", "Older"), _message("1")]
    )
    client.get_school_message = AsyncMock(
        side_effect=lambda mid: {"id": mid, "sent_by": "Office", "body": f"Body {mid}"}
    )
    await _poll(coordinator)
    await hass.async_block_till_done()

    assert [e.data["id"] for e in events] == ["2", "3"]
    assert events[1].data["subject"] == "Newest"
    assert events[1].data["body"] == "Body 3"
    assert events[1].data["sent_by"] == "Office"
    assert events[1].data["config_entry_id"] == config_entry.entry_id


async def test_failed_message_detail_still_announces_preview(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client()
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    await _poll(coordinator)

    client.get_school_messages = AsyncMock(return_value=[_message("7")])
    client.get_school_message = AsyncMock(side_effect=ArborApiError("500"))
    await _poll(coordinator)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["preview"] == "Hi Parents..."


async def test_failed_first_message_fetch_does_not_flood_events(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client(get_school_messages=AsyncMock(side_effect=TimeoutError()))
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    data = await _poll(coordinator)
    assert data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES] == []

    client.get_school_messages = AsyncMock(
        return_value=[_message(str(i)) for i in range(20)]
    )
    await _poll(coordinator)
    await hass.async_block_till_done()

    assert events == []


async def test_messages_over_the_cap_are_announced_on_later_polls(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client()
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    await _poll(coordinator)

    # Seven new messages, newest first.
    client.get_school_messages = AsyncMock(
        return_value=[_message(str(i)) for i in range(7, 0, -1)]
    )
    await _poll(coordinator)
    await hass.async_block_till_done()
    assert [e.data["id"] for e in events] == ["1", "2", "3", "4", "5"]

    await _poll(coordinator)
    await hass.async_block_till_done()
    assert [e.data["id"] for e in events] == ["1", "2", "3", "4", "5", "6", "7"]

    await _poll(coordinator)
    await hass.async_block_till_done()
    assert len(events) == 7


def _detail(message_id: str) -> dict[str, str]:
    return {"id": message_id, "sent_by": "Office", "body": f"Full body {message_id}"}


async def test_latest_messages_carry_full_body_from_first_poll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(
        get_school_messages=AsyncMock(
            return_value=[_message(str(i)) for i in range(6, 0, -1)]
        ),
        get_school_message=AsyncMock(side_effect=_detail),
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await _poll(coordinator)
    messages = data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES]

    assert [m.get("body") for m in messages[:MESSAGE_DETAIL_LIMIT]] == [
        f"Full body {i}" for i in range(6, 6 - MESSAGE_DETAIL_LIMIT, -1)
    ]
    assert messages[0]["sent_by"] == "Office"
    assert messages[0]["preview"] == "Hi Parents..."
    assert all("body" not in m for m in messages[MESSAGE_DETAIL_LIMIT:])
    assert client.get_school_message.await_count == MESSAGE_DETAIL_LIMIT


async def test_message_details_are_cached_between_polls(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(
        get_school_messages=AsyncMock(return_value=[_message("2"), _message("1")]),
        get_school_message=AsyncMock(side_effect=_detail),
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    await _poll(coordinator)
    data = await _poll(coordinator)

    assert client.get_school_message.await_count == 2
    assert data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES][0]["body"] == "Full body 2"


async def test_failed_message_detail_is_retried_next_poll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    client = _mock_client(
        get_school_messages=AsyncMock(return_value=[_message("1")]),
        get_school_message=AsyncMock(side_effect=ArborApiError("500")),
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)

    data = await _poll(coordinator)
    assert "body" not in data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES][0]

    client.get_school_message = AsyncMock(side_effect=_detail)
    data = await _poll(coordinator)
    assert data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES][0]["body"] == "Full body 1"


async def test_new_message_detail_is_fetched_once_for_event_and_sensor(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_SCHOOL_MESSAGE)
    client = _mock_client(
        get_school_messages=AsyncMock(return_value=[_message("1")]),
        get_school_message=AsyncMock(side_effect=_detail),
    )
    coordinator = ArborDataUpdateCoordinator(hass, client, config_entry)
    await _poll(coordinator)

    client.get_school_messages = AsyncMock(return_value=[_message("2"), _message("1")])
    data = await _poll(coordinator)
    await hass.async_block_till_done()

    assert [e.data["body"] for e in events] == ["Full body 2"]
    assert data[DATA_ACCOUNT][DATA_SCHOOL_MESSAGES][0]["body"] == "Full body 2"
    # message 1 on the first poll, message 2 once on the second
    assert client.get_school_message.await_count == 2
