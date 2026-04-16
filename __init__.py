"""The Arbor School integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArborApiClient, ArborAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)
from .coordinator import ArborDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Arbor School from a config entry."""
    session = async_get_clientsession(hass)

    client = ArborApiClient(
        session=session,
        school_domain=entry.data[CONF_SCHOOL_DOMAIN],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_expiry=entry.data.get(CONF_TOKEN_EXPIRY, 0),
    )

    # Ensure we have a valid token — if the stored one is stale,
    # try refreshing; if that fails, re-authenticate from scratch.
    try:
        await client.ensure_valid_token()
    except ArborAuthError:
        _LOGGER.info(
            "Stored tokens expired, attempting full re-authentication"
        )
        try:
            await client.authenticate(
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
            )
        except ArborAuthError as err:
            _LOGGER.error("Re-authentication failed: %s", err)
            # Trigger re-auth flow in the UI
            entry.async_start_reauth(hass)
            return False

    coordinator = ArborDataUpdateCoordinator(hass, client, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload an Arbor School config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
