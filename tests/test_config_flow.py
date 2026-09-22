"""Tests for the Arbor config, reauth and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arbor.api import ArborAuthError
from custom_components.arbor.const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_STUDENTS,
    DEFAULT_ACADEMIC_YEAR_ID,
    DOMAIN,
)

from .conftest import STUDENTS, auth_result, mock_client


def _mock_client(**overrides: object) -> MagicMock:
    return mock_client(
        **{"discover_academic_year_id": AsyncMock(return_value="25"), **overrides}
    )


def _patch_client(client: MagicMock):
    return patch(
        "custom_components.arbor.config_flow.ArborApiClient", return_value=client
    )


def _patch_setup():
    return patch("custom_components.arbor.async_setup_entry", return_value=True)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    with _patch_client(_mock_client()), _patch_setup():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "pw"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STUDENTS] == STUDENTS
    assert result["data"][CONF_ACADEMIC_YEAR_ID] == "25"


async def test_user_flow_falls_back_to_default_year(hass: HomeAssistant) -> None:
    client = _mock_client(discover_academic_year_id=AsyncMock(return_value=None))
    with _patch_client(client), _patch_setup():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "pw"},
        )

    assert result["data"][CONF_ACADEMIC_YEAR_ID] == DEFAULT_ACADEMIC_YEAR_ID


async def test_reauth_updates_existing_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with _patch_client(_mock_client()), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "new-password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert config_entry.data[CONF_ACCESS_TOKEN] == "new-access"
    # Untouched keys survive the update.
    assert config_entry.data[CONF_STUDENTS] == STUDENTS


async def test_reauth_invalid_credentials_shows_error(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    client = _mock_client(authenticate=AsyncMock(side_effect=ArborAuthError("nope")))
    with _patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == "old-password"


async def test_reauth_with_different_school_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    client = _mock_client(
        authenticate=AsyncMock(return_value=auth_result("other.uk.arbor.sc"))
    )
    with _patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone@else.com", CONF_PASSWORD: "pw"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_PASSWORD] == "old-password"


async def test_options_flow_saves_scan_interval(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)

    with _patch_setup():
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 30.0}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 30}


async def test_reconfigure_updates_credentials(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The Reconfigure menu item updates credentials on a working entry."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with _patch_client(_mock_client()), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "rotated-password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PASSWORD] == "rotated-password"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert config_entry.data[CONF_STUDENTS] == STUDENTS


async def test_reconfigure_rejects_a_different_school(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Credentials for another school must not overwrite this entry."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)

    other = _mock_client(
        authenticate=AsyncMock(return_value=auth_result("other-school.uk.arbor.sc"))
    )
    with _patch_client(other), _patch_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "someone@example.com", CONF_PASSWORD: "pw"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_PASSWORD] != "pw"


async def test_reconfigure_invalid_credentials_shows_error(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)

    bad = _mock_client(authenticate=AsyncMock(side_effect=ArborAuthError("nope")))
    with _patch_client(bad):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_adding_the_same_school_again_repairs_the_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Re-adding a broken entry refreshes its credentials instead of dead-ending.

    Without this, a stale password leaves the user with an unusable entry that
    can only be fixed by deleting the integration and setting it up again.
    """
    config_entry.add_to_hass(hass)

    with _patch_client(_mock_client()), _patch_setup():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "recovered-password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_PASSWORD] == "recovered-password"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert config_entry.data[CONF_ACCESS_TOKEN] == "new-access"
