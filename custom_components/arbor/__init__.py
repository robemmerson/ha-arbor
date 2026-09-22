"""The Arbor School integration."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArborApiClient, ArborApiError, ArborAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)
from .coordinator import ArborDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR, Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Arbor School from a config entry."""
    session = async_get_clientsession(hass)

    client = ArborApiClient(
        session=session,
        school_domain=entry.data[CONF_SCHOOL_DOMAIN],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_expiry=entry.data.get(CONF_TOKEN_EXPIRY, 0),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    # ensure_valid_token() falls back to full re-authentication using the
    # stashed credentials if the refresh token has been invalidated. If even
    # that fails, the credentials themselves are bad and HA starts the
    # reauth flow so the user can re-enter them.
    try:
        await client.ensure_valid_token()
    except ArborAuthError as err:
        raise ConfigEntryAuthFailed(f"Re-authentication failed: {err}") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Unable to reach Arbor: {err}") from err

    await _async_refresh_students(hass, entry, client)

    coordinator = ArborDataUpdateCoordinator(hass, client, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_refresh_students(
    hass: HomeAssistant, entry: ConfigEntry, client: ArborApiClient
) -> None:
    """Re-discover children so a newly linked child appears after a reload.

    Discovery failures are non-fatal: the students stored in the entry are
    used instead.
    """
    try:
        students = client.parse_students(await client.get_dashboard())
    except ArborAuthError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except (ArborApiError, aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Could not refresh students, using stored list: %s", err)
        return

    if students and students != entry.data.get(CONF_STUDENTS):
        _LOGGER.info("Student list changed, now tracking %d student(s)", len(students))
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_STUDENTS: students}
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Arbor School config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting the device of a child no longer on the account."""
    current = {f"arbor_{s['student_id']}" for s in entry.data.get(CONF_STUDENTS, [])}
    current.add(f"arbor_school_{entry.data[CONF_SCHOOL_DOMAIN]}")
    return not any(
        domain == DOMAIN and identifier in current
        for domain, identifier in device_entry.identifiers
    )
