"""Config flow for Arbor School integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import ArborApiClient, ArborAuthError
from .const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_SCHOOL_NAME,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DEFAULT_ACADEMIC_YEAR_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ArborOptionsFlow:
        """Return the options flow handler."""
        return ArborOptionsFlow()

    async def _async_login(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[ArborApiClient, dict[str, Any]] | None:
        """Authenticate with Arbor, recording a form error key on failure."""
        client = ArborApiClient(async_get_clientsession(self.hass))
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
            return client, auth_result
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect credentials."""
        errors: dict[str, str] = {}

        if user_input is not None and (
            login := await self._async_login(user_input, errors)
        ):
            client, auth_result = login

            # Discover students from dashboard
            try:
                dashboard = await client.get_dashboard()
                students = client.parse_students(dashboard)
            except Exception:
                _LOGGER.exception("Failed to discover students")
                errors["base"] = "unknown"
                students = []

            if not students:
                errors.setdefault("base", "no_students")
            else:
                academic_year_id = await self._async_discover_year(
                    client, students[0]["student_id"]
                )

                # One entry per school. Re-adding a school that is already
                # set up refreshes its credentials and reloads it, so a bad
                # password can be fixed without deleting the integration.
                await self.async_set_unique_id(f"arbor_{auth_result['school_domain']}")
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: auth_result["refresh_token"],
                        CONF_ACCESS_TOKEN: auth_result["access_token"],
                        CONF_TOKEN_EXPIRY: auth_result["token_expiry"],
                    },
                    reload_on_update=True,
                )

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

    @staticmethod
    async def _async_discover_year(client: ArborApiClient, student_id: str) -> str:
        """Discover the academic year ID, falling back to the default."""
        try:
            year_id = await client.discover_academic_year_id(student_id)
        except Exception:
            _LOGGER.warning("Failed to discover academic year, using default")
            year_id = None
        return year_id or DEFAULT_ACADEMIC_YEAR_ID

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect new credentials after Home Assistant rejected the old ones."""
        return await self._async_update_credentials(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user re-enter credentials at any time, from the entry menu.

        Reauth only appears once Home Assistant has decided the credentials
        are bad. Reconfigure is always available, so a password change can be
        applied before the integration starts failing.
        """
        return await self._async_update_credentials(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_update_credentials(
        self,
        step_id: str,
        entry: ConfigEntry,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Re-authenticate and write the new credentials to an existing entry."""
        errors: dict[str, str] = {}

        if user_input is not None and (
            login := await self._async_login(user_input, errors)
        ):
            _client, auth_result = login
            await self.async_set_unique_id(f"arbor_{auth_result['school_domain']}")
            self._abort_if_unique_id_mismatch(reason="wrong_account")

            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REFRESH_TOKEN: auth_result["refresh_token"],
                    CONF_ACCESS_TOKEN: auth_result["access_token"],
                    CONF_TOKEN_EXPIRY: auth_result["token_expiry"],
                },
            )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {CONF_USERNAME: entry.data.get(CONF_USERNAME)},
            ),
            description_placeholders={"school": entry.title},
            errors=errors,
        )


class ArborOptionsFlow(OptionsFlowWithReload):
    """Handle Arbor School options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
