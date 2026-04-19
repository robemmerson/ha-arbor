"""Config flow for Arbor School integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArborApiClient, ArborAuthError
from .const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_SCHOOL_NAME,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ArborConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arbor School."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = ArborApiClient(session)

            try:
                auth_result = await client.authenticate(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except ArborAuthError as err:
                _LOGGER.error("Authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.error("Connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during authentication")
                errors["base"] = "unknown"
            else:
                # Discover students from dashboard
                try:
                    dashboard = await client.get_dashboard()
                    students = client.parse_students(dashboard)
                except Exception:
                    _LOGGER.exception("Failed to discover students")
                    errors["base"] = "unknown"
                    students = []

                if not students:
                    errors["base"] = "no_students"
                else:
                    # Discover academic year ID
                    try:
                        academic_year_id = await client.discover_academic_year_id(
                            students[0]["student_id"]
                        )
                    except Exception:
                        _LOGGER.warning(
                            "Failed to discover academic year, using default"
                        )
                        academic_year_id = "24"

                    # Set unique ID to prevent duplicate entries
                    await self.async_set_unique_id(
                        f"arbor_{auth_result['school_domain']}"
                    )
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=auth_result.get("school_name", "Arbor School"),
                        data={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_SCHOOL_DOMAIN: auth_result["school_domain"],
                            CONF_SCHOOL_NAME: auth_result.get("school_name", ""),
                            CONF_REFRESH_TOKEN: auth_result["refresh_token"],
                            CONF_ACCESS_TOKEN: auth_result["access_token"],
                            CONF_TOKEN_EXPIRY: auth_result["token_expiry"],
                            CONF_STUDENTS: students,
                            CONF_ACADEMIC_YEAR_ID: academic_year_id,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication when tokens expire permanently."""
        return await self.async_step_user()
