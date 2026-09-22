"""Data update coordinator for Arbor School integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from functools import partial
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ArborApiClient, ArborApiError, ArborAuthError
from .const import (
    CONF_ACADEMIC_YEAR_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCHOOL_DOMAIN,
    CONF_STUDENTS,
    CONF_TOKEN_EXPIRY,
    DATA_ACCOUNT,
    DATA_ASSIGNMENT_COUNTS,
    DATA_ASSIGNMENTS_DUE,
    DATA_ASSIGNMENTS_OVERDUE,
    DATA_ASSIGNMENTS_SUBMITTED,
    DATA_CALENDAR,
    DATA_CLUBS,
    DATA_KPIS,
    DATA_SCHOOL_MESSAGES,
    DEFAULT_ACADEMIC_YEAR_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_SCHOOL_MESSAGE,
    MAX_ANNOUNCED_MESSAGES,
    MESSAGE_DETAIL_LIMIT,
)

_LOGGER = logging.getLogger(__name__)

# Errors that affect a single request; anything else aborts the poll.
_RECOVERABLE_ERRORS = (ArborApiError, aiohttp.ClientError, TimeoutError, ValueError)


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
            update_interval=timedelta(
                minutes=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                )
            ),
            config_entry=config_entry,
        )
        self.client = client
        self._students: list[dict[str, Any]] = config_entry.data.get(CONF_STUDENTS, [])
        self._academic_year_id: str = config_entry.data.get(
            CONF_ACADEMIC_YEAR_ID, DEFAULT_ACADEMIC_YEAR_ID
        )
        self._academic_year_checked_on: date | None = None
        # None until the first successful fetch, so existing messages are not
        # announced as new when HA starts.
        self._seen_message_ids: set[str] | None = None
        # Full body/sender per message ID, so each detail page is fetched once
        self._message_details: dict[str, dict[str, str]] = {}
        self._requests_attempted = 0
        self._requests_failed = 0

    @property
    def students(self) -> list[dict[str, Any]]:
        """Return the list of discovered students."""
        return self._students

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data for all students."""
        try:
            return await self._fetch_all()
        except ArborAuthError as err:
            # Re-auth was attempted (refresh + full authenticate) and still
            # failed — the stored credentials are no longer valid. Surface
            # this as ConfigEntryAuthFailed so HA prompts the user to
            # re-enter credentials via the reauth flow.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err

    async def _fetch_all(self) -> dict[str, Any]:
        """Fetch per-student and account data; let ArborAuthError propagate."""
        await self.client.ensure_valid_token()

        # Persist updated tokens back to config entry
        self._persist_tokens()

        await self._refresh_academic_year()

        self._requests_attempted = 0
        self._requests_failed = 0

        all_data: dict[str, Any] = {
            student["student_id"]: await self._fetch_student(student["student_id"])
            for student in self._students
        }
        all_data[DATA_ACCOUNT] = await self._fetch_account()

        # Persist tokens after all requests (refresh token may have rotated
        # as a side-effect of a mid-poll re-auth inside ArborApiClient._get).
        self._persist_tokens()

        if self._requests_attempted and (
            self._requests_failed == self._requests_attempted
        ):
            raise UpdateFailed("Every request to Arbor failed; will retry")

        return all_data

    def _previous(self, *keys: str) -> Any:
        """Return a value from the last successful poll, or None."""
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    async def _fetch_student(self, sid: str) -> dict[str, Any]:
        """Fetch one student's data, keeping last-known values on failure."""
        year = self._academic_year_id
        client = self.client
        # (data key, empty value, keep previous value on failure, request)
        requests: list[tuple[str, Any, bool, Callable[[], Awaitable[Any]]]] = [
            (DATA_KPIS, {}, True, partial(client.get_kpis, sid)),
            (
                DATA_ASSIGNMENT_COUNTS,
                {},
                True,
                partial(client.get_assignment_counts, sid, year),
            ),
            (
                DATA_ASSIGNMENTS_DUE,
                [],
                True,
                partial(client.get_assignments_due, sid, year),
            ),
            (
                DATA_ASSIGNMENTS_OVERDUE,
                [],
                True,
                partial(client.get_assignments_overdue, sid, year),
            ),
            (
                DATA_ASSIGNMENTS_SUBMITTED,
                [],
                True,
                partial(client.get_assignments_submitted, sid, year),
            ),
            # Never reuse a previous timetable: it may be yesterday's.
            (
                DATA_CALENDAR,
                [],
                False,
                partial(client.get_calendar, sid, dt_util.now().date()),
            ),
            (DATA_CLUBS, {}, True, partial(client.get_clubs, sid)),
        ]

        student_data: dict[str, Any] = {}
        for key, empty, keep_previous, request in requests:
            fallback = self._previous(sid, key) if keep_previous else None
            student_data[key] = await self._fetch_or_fallback(
                f"{key} for {sid}",
                request,
                empty if fallback is None else fallback,
            )
        return student_data

    async def _fetch_account(self) -> dict[str, Any]:
        """Fetch guardian-level data and announce new school messages."""
        previous = self._previous(DATA_ACCOUNT, DATA_SCHOOL_MESSAGES)
        messages = await self._fetch_or_fallback(
            "school messages", self.client.get_school_messages, None
        )
        if messages is None:
            return {DATA_SCHOOL_MESSAGES: previous or []}

        await self._announce_new_messages(messages)
        return {DATA_SCHOOL_MESSAGES: await self._with_details(messages)}

    async def _message_detail(self, message_id: str) -> dict[str, str]:
        """Return a message's detail page, fetching it only on a cache miss.

        Failed fetches are not cached, so they are retried on the next poll.
        """
        if message_id in self._message_details:
            return self._message_details[message_id]
        detail = await self._fetch_or_fallback(
            f"school message {message_id}",
            partial(self.client.get_school_message, message_id),
            {},
        )
        if detail:
            self._message_details[message_id] = detail
        return detail

    async def _with_details(
        self, messages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Attach full body and sender to the newest messages."""
        enriched = []
        for index, message in enumerate(messages):
            if index < MESSAGE_DETAIL_LIMIT:
                detail = await self._message_detail(message["id"])
                message = _merge_detail(message, detail)
            enriched.append(message)
        current_ids = {message["id"] for message in messages}
        self._message_details = {
            mid: detail
            for mid, detail in self._message_details.items()
            if mid in current_ids
        }
        return enriched

    async def _fetch_or_fallback(
        self, what: str, request: Callable[[], Awaitable[Any]], fallback: Any
    ) -> Any:
        """Run one request, returning fallback if it fails recoverably."""
        self._requests_attempted += 1
        try:
            return await request()
        except ArborAuthError:
            raise
        except _RECOVERABLE_ERRORS as err:
            self._requests_failed += 1
            _LOGGER.warning("Failed to fetch %s: %s", what, err)
            return fallback

    async def _announce_new_messages(self, messages: list[dict[str, str]]) -> None:
        """Fire an event for each message not seen in a previous poll."""
        ids = {message["id"] for message in messages}
        if self._seen_message_ids is None:
            self._seen_message_ids = ids
            return

        # Messages arrive newest first; announce oldest first. Anything over
        # the cap stays unseen and is announced on the following polls.
        unseen = [
            m for m in reversed(messages) if m["id"] not in self._seen_message_ids
        ]
        batch = unseen[:MAX_ANNOUNCED_MESSAGES]
        self._seen_message_ids |= {message["id"] for message in batch}
        if len(unseen) > len(batch):
            _LOGGER.info(
                "%d new school messages; announcing %d now, the rest next poll",
                len(unseen),
                len(batch),
            )

        for message in batch:
            merged = _merge_detail(message, await self._message_detail(message["id"]))
            self.hass.bus.async_fire(
                EVENT_SCHOOL_MESSAGE,
                {
                    "config_entry_id": self.config_entry.entry_id,
                    "school_domain": self.config_entry.data.get(CONF_SCHOOL_DOMAIN),
                    **merged,
                },
            )

    async def _refresh_academic_year(self) -> None:
        """Re-discover the academic year once per day.

        The year ID is baked into every assignments URL, so without this the
        assignment sensors keep querying last year after the September
        rollover. The stored ID is kept if the page has no year toggle, and a
        failed request is retried on the next poll.
        """
        today = dt_util.now().date()
        if self._academic_year_checked_on == today or not self._students:
            return

        try:
            year_id = await self.client.discover_academic_year_id(
                self._students[0]["student_id"]
            )
        except ArborAuthError:
            raise
        except _RECOVERABLE_ERRORS as err:
            _LOGGER.warning("Failed to check current academic year: %s", err)
            return

        self._academic_year_checked_on = today
        if year_id is None or year_id == self._academic_year_id:
            return

        _LOGGER.info(
            "Academic year changed from %s to %s", self._academic_year_id, year_id
        )
        self._academic_year_id = year_id
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_ACADEMIC_YEAR_ID: year_id},
        )

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


def _merge_detail(message: dict[str, str], detail: dict[str, str]) -> dict[str, str]:
    """Overlay detail fields, keeping list fields the detail page lacks."""
    return {**message, **{k: v for k, v in detail.items() if v}}
