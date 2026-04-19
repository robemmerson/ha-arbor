"""Data update coordinator for Arbor School integration."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import ArborApiClient, ArborApiError, ArborAuthError
from .const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DATA_ASSIGNMENT_COUNTS,
    DATA_ASSIGNMENTS_DUE,
    DATA_ASSIGNMENTS_OVERDUE,
    DATA_ASSIGNMENTS_SUBMITTED,
    DATA_CALENDAR,
    DATA_KPIS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ArborDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch data from Arbor for all students."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: ArborApiClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=config_entry,
        )
        self.client = client
        self._students: list[dict[str, Any]] = config_entry.data.get(CONF_STUDENTS, [])
        self._academic_year_id: str = config_entry.data.get(CONF_ACADEMIC_YEAR_ID, "24")

    @property
    def students(self) -> list[dict[str, Any]]:
        """Return the list of discovered students."""
        return self._students

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data for all students."""
        try:
            return await self._fetch_all_students()
        except ArborAuthError as err:
            # Re-auth was attempted (refresh + full authenticate) and still
            # failed — the stored credentials are no longer valid. Surface
            # this as ConfigEntryAuthFailed so HA prompts the user to
            # re-enter credentials via the reauth flow.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err

    async def _fetch_all_students(self) -> dict[str, Any]:
        """Fetch all per-student data; let ArborAuthError propagate."""
        await self.client.ensure_valid_token()

        # Persist updated tokens back to config entry
        self._persist_tokens()

        all_data: dict[str, Any] = {}

        for student in self._students:
            sid = student["student_id"]
            student_data: dict[str, Any] = {}

            try:
                # KPIs (attendance + behaviour)
                student_data[DATA_KPIS] = await self.client.get_kpis(sid)
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning("Failed to fetch KPIs for %s: %s", sid, err)
                student_data[DATA_KPIS] = {}

            try:
                # Assignment counts
                student_data[
                    DATA_ASSIGNMENT_COUNTS
                ] = await self.client.get_assignment_counts(sid, self._academic_year_id)
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning(
                    "Failed to fetch assignment counts for %s: %s", sid, err
                )
                student_data[DATA_ASSIGNMENT_COUNTS] = {}

            try:
                # Assignment lists
                student_data[
                    DATA_ASSIGNMENTS_DUE
                ] = await self.client.get_assignments_due(sid, self._academic_year_id)
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning("Failed to fetch assignments due for %s: %s", sid, err)
                student_data[DATA_ASSIGNMENTS_DUE] = []

            try:
                student_data[
                    DATA_ASSIGNMENTS_OVERDUE
                ] = await self.client.get_assignments_overdue(
                    sid, self._academic_year_id
                )
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning(
                    "Failed to fetch overdue assignments for %s: %s", sid, err
                )
                student_data[DATA_ASSIGNMENTS_OVERDUE] = []

            try:
                student_data[
                    DATA_ASSIGNMENTS_SUBMITTED
                ] = await self.client.get_assignments_submitted(
                    sid, self._academic_year_id
                )
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning(
                    "Failed to fetch submitted assignments for %s: %s",
                    sid,
                    err,
                )
                student_data[DATA_ASSIGNMENTS_SUBMITTED] = []

            try:
                # Today's calendar
                student_data[DATA_CALENDAR] = await self.client.get_calendar(
                    sid, date.today()
                )
            except ArborAuthError:
                raise
            except ArborApiError as err:
                _LOGGER.warning("Failed to fetch calendar for %s: %s", sid, err)
                student_data[DATA_CALENDAR] = []

            all_data[sid] = student_data

        # Persist tokens after all requests (refresh token may have rotated
        # as a side-effect of a mid-poll re-auth inside ArborApiClient._get).
        self._persist_tokens()

        return all_data

    def _persist_tokens(self) -> None:
        """Save the latest tokens back to the config entry."""
        new_data = dict(self.config_entry.data)
        changed = False

        if self.client.refresh_token != new_data.get(CONF_REFRESH_TOKEN):
            new_data[CONF_REFRESH_TOKEN] = self.client.refresh_token
            changed = True
        if self.client.access_token != new_data.get(CONF_ACCESS_TOKEN):
            new_data[CONF_ACCESS_TOKEN] = self.client.access_token
            changed = True
        if self.client.token_expiry != new_data.get(CONF_TOKEN_EXPIRY):
            new_data[CONF_TOKEN_EXPIRY] = self.client.token_expiry
            changed = True

        if changed:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
