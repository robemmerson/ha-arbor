"""Calendar platform for Arbor School integration."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArborApiError
from .const import DATA_CALENDAR, DOMAIN
from .coordinator import ArborDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Arbor calendar entities from a config entry."""
    coordinator: ArborDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[CalendarEntity] = []

    for student in coordinator.students:
        entities.append(
            ArborCalendarEntity(
                coordinator=coordinator,
                student_id=student["student_id"],
                student_name=student["name"],
            )
        )

    async_add_entities(entities)


class ArborCalendarEntity(
    CoordinatorEntity[ArborDataUpdateCoordinator], CalendarEntity
):
    """A calendar entity representing a student's school timetable."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
    ) -> None:
        """Initialise the calendar entity."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = student_name
        first_name = student_name.split()[0] if student_name else student_name

        self._attr_unique_id = f"arbor_{student_id}_calendar"
        self._attr_name = f"{first_name} School Timetable"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"arbor_{student_id}")},
            "name": f"Arbor - {student_name}",
            "manufacturer": "Arbor Education",
            "model": "Parent Portal",
            "configuration_url": (
                f"https://{coordinator.client.school_domain}"
            ),
        }

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next upcoming event."""
        if self.coordinator.data is None:
            return None

        student_data = self.coordinator.data.get(self._student_id, {})
        lessons = student_data.get(DATA_CALENDAR, [])

        if not lessons:
            return None

        now = datetime.now()

        for lesson in lessons:
            start = self._parse_datetime(lesson.get("start", ""))
            end = self._parse_datetime(lesson.get("end", ""))
            if start and end and end > now:
                return CalendarEvent(
                    summary=lesson.get("subject", ""),
                    start=start,
                    end=end,
                    location=lesson.get("location", ""),
                    description=lesson.get("full_title", ""),
                )

        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Fetch events for a date range (used by calendar card)."""
        events: list[CalendarEvent] = []
        current = start_date.date()
        end = end_date.date()

        while current <= end:
            try:
                lessons = await self.coordinator.client.get_calendar(
                    self._student_id, current
                )
            except ArborApiError as err:
                _LOGGER.debug(
                    "Failed to fetch calendar for %s on %s: %s",
                    self._student_id,
                    current,
                    err,
                )
                lessons = []

            for lesson in lessons:
                start_dt = self._parse_datetime(lesson.get("start", ""))
                end_dt = self._parse_datetime(lesson.get("end", ""))
                if start_dt and end_dt:
                    events.append(
                        CalendarEvent(
                            summary=lesson.get("subject", ""),
                            start=start_dt,
                            end=end_dt,
                            location=lesson.get("location", ""),
                            description=lesson.get("full_title", ""),
                        )
                    )

            current += timedelta(days=1)

        return events

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime | None:
        """Parse a datetime string from the API."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except ValueError:
            return None
