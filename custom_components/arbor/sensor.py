"""Sensor platform for Arbor School integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SCHOOL_DOMAIN,
    CONF_SCHOOL_NAME,
    DATA_ACCOUNT,
    DATA_ASSIGNMENT_COUNTS,
    DATA_ASSIGNMENTS_DUE,
    DATA_ASSIGNMENTS_OVERDUE,
    DATA_ASSIGNMENTS_SUBMITTED,
    DATA_CALENDAR,
    DATA_CLUBS,
    DATA_KPIS,
    DATA_SCHOOL_MESSAGES,
    DOMAIN,
    RECENT_MESSAGES_LIMIT,
)
from .coordinator import ArborDataUpdateCoordinator
from .util import parse_school_datetime

_LOGGER = logging.getLogger(__name__)


def _slugify_name(name: str) -> str:
    """Convert a student name to a slug for entity IDs."""
    return name.lower().replace(" ", "_").replace("'", "")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Arbor sensors from a config entry."""
    coordinator: ArborDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    school_name = config_entry.data.get(CONF_SCHOOL_NAME, "School")

    entities: list[SensorEntity] = []

    for student in coordinator.students:
        sid = student["student_id"]
        name = student["name"]
        slug = _slugify_name(name)
        first_name = name.split()[0] if name else name

        # ── Attendance sensors ──
        entities.append(
            ArborKpiSensor(
                coordinator=coordinator,
                student_id=sid,
                student_name=name,
                slug=slug,
                key="attendance_year",
                friendly_name=f"{first_name} Attendance (Year)",
                icon="mdi:school",
                unit="%",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                school_name=school_name,
            )
        )
        entities.append(
            ArborKpiSensor(
                coordinator=coordinator,
                student_id=sid,
                student_name=name,
                slug=slug,
                key="attendance_last_4_weeks",
                friendly_name=f"{first_name} Attendance (Last 4 Weeks)",
                icon="mdi:school",
                unit="%",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                school_name=school_name,
            )
        )

        # ── Positive points sensors ──
        for period_key, period_label in [
            ("positive_this_term", "Current Term"),
            ("positive_last_term", "Previous Term"),
            ("positive_year", "Year"),
        ]:
            entities.append(
                ArborKpiSensor(
                    coordinator=coordinator,
                    student_id=sid,
                    student_name=name,
                    slug=slug,
                    key=period_key,
                    friendly_name=f"{first_name} Positive Points ({period_label})",
                    icon="mdi:thumb-up",
                    unit="points",
                    device_class=None,
                    state_class=SensorStateClass.TOTAL,
                    school_name=school_name,
                )
            )

        # ── Negative points sensors ──
        for period_key, period_label in [
            ("negative_this_term", "Current Term"),
            ("negative_last_term", "Previous Term"),
            ("negative_year", "Year"),
        ]:
            entities.append(
                ArborKpiSensor(
                    coordinator=coordinator,
                    student_id=sid,
                    student_name=name,
                    slug=slug,
                    key=period_key,
                    friendly_name=f"{first_name} Negative Points ({period_label})",
                    icon="mdi:thumb-down",
                    unit="points",
                    device_class=None,
                    state_class=SensorStateClass.TOTAL,
                    school_name=school_name,
                )
            )

        # ── Assignment count sensors ──
        for count_key, count_label, icon in [
            ("due", "Assignments Due", "mdi:clipboard-text-clock"),
            ("overdue", "Assignments Overdue", "mdi:clipboard-alert"),
            ("submitted", "Assignments Submitted", "mdi:clipboard-check"),
        ]:
            entities.append(
                ArborAssignmentCountSensor(
                    coordinator=coordinator,
                    student_id=sid,
                    student_name=name,
                    slug=slug,
                    count_key=count_key,
                    friendly_name=f"{first_name} {count_label}",
                    icon=icon,
                    school_name=school_name,
                )
            )

        # ── Assignment list sensors ──
        for list_data_key, list_label, icon in [
            (DATA_ASSIGNMENTS_DUE, "Assignments Due List", "mdi:clipboard-text-clock"),
            (
                DATA_ASSIGNMENTS_OVERDUE,
                "Assignments Overdue List",
                "mdi:clipboard-alert",
            ),
            (
                DATA_ASSIGNMENTS_SUBMITTED,
                "Assignments Submitted List",
                "mdi:clipboard-check",
            ),
        ]:
            entities.append(
                ArborAssignmentListSensor(
                    coordinator=coordinator,
                    student_id=sid,
                    student_name=name,
                    slug=slug,
                    data_key=list_data_key,
                    friendly_name=f"{first_name} {list_label}",
                    icon=icon,
                    school_name=school_name,
                )
            )

        # ── Calendar / timetable sensor ──
        entities.append(
            ArborTimetableSensor(
                coordinator=coordinator,
                student_id=sid,
                student_name=name,
                slug=slug,
                school_name=school_name,
            )
        )

        # ── Club sensors ──
        for bucket, label, icon in [
            ("registered", "Clubs", "mdi:account-group"),
            ("available", "Clubs Available", "mdi:account-multiple-plus"),
        ]:
            entities.append(
                ArborClubsSensor(
                    coordinator=coordinator,
                    student_id=sid,
                    student_name=name,
                    slug=slug,
                    bucket=bucket,
                    friendly_name=f"{first_name} {label}",
                    icon=icon,
                    school_name=school_name,
                )
            )

    entities.append(
        ArborSchoolMessagesSensor(
            coordinator=coordinator,
            school_domain=config_entry.data[CONF_SCHOOL_DOMAIN],
            school_name=school_name,
        )
    )

    async_add_entities(entities)


class ArborBaseSensor(CoordinatorEntity[ArborDataUpdateCoordinator], SensorEntity):
    """Base class for Arbor sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        school_name: str,
    ) -> None:
        """Initialise the base sensor."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = student_name
        self._slug = slug

        # Device info groups sensors per student
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"arbor_{student_id}")},
            "name": f"Arbor - {student_name}",
            "manufacturer": "Arbor Education",
            "model": "Parent Portal",
            "configuration_url": f"https://{coordinator.client.school_domain}",
        }

    def _get_student_data(self) -> dict[str, Any] | None:
        """Get this student's data from the coordinator."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._student_id)


class ArborKpiSensor(ArborBaseSensor):
    """Sensor for KPI values (attendance, behaviour points)."""

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        key: str,
        friendly_name: str,
        icon: str,
        unit: str,
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass | None,
        school_name: str,
    ) -> None:
        """Initialise the KPI sensor."""
        super().__init__(coordinator, student_id, student_name, slug, school_name)
        self._key = key
        self._attr_unique_id = f"arbor_{student_id}_{key}"
        self._attr_name = friendly_name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def native_value(self) -> float | int | None:
        """Return the current KPI value."""
        data = self._get_student_data()
        if data is None:
            return None
        kpis = data.get(DATA_KPIS, {})
        return kpis.get(self._key)


class ArborAssignmentCountSensor(ArborBaseSensor):
    """Sensor for assignment counts."""

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        count_key: str,
        friendly_name: str,
        icon: str,
        school_name: str,
    ) -> None:
        """Initialise the assignment count sensor."""
        super().__init__(coordinator, student_id, student_name, slug, school_name)
        self._count_key = count_key
        self._attr_unique_id = f"arbor_{student_id}_assignments_{count_key}_count"
        self._attr_name = friendly_name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = "assignments"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return the assignment count."""
        data = self._get_student_data()
        if data is None:
            return None
        counts = data.get(DATA_ASSIGNMENT_COUNTS, {})
        return counts.get(self._count_key)


class ArborAssignmentListSensor(ArborBaseSensor):
    """Sensor that holds a list of assignments as attributes."""

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        data_key: str,
        friendly_name: str,
        icon: str,
        school_name: str,
    ) -> None:
        """Initialise the assignment list sensor."""
        super().__init__(coordinator, student_id, student_name, slug, school_name)
        self._data_key = data_key
        # Create unique ID from data key
        short_key = data_key.replace("assignments_", "")
        self._attr_unique_id = f"arbor_{student_id}_{short_key}_list"
        self._attr_name = friendly_name
        self._attr_icon = icon

    @property
    def native_value(self) -> int | None:
        """Return the count of items in the list."""
        data = self._get_student_data()
        if data is None:
            return None
        items = data.get(self._data_key, [])
        return len(items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the assignment list as attributes."""
        data = self._get_student_data()
        if data is None:
            return {"assignments": []}
        items = data.get(self._data_key, [])
        return {
            "assignments": items,
            "total": len(items),
        }


class ArborTimetableSensor(ArborBaseSensor):
    """Sensor showing today's timetable as state + attributes."""

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        school_name: str,
    ) -> None:
        """Initialise the timetable sensor."""
        first_name = student_name.split()[0] if student_name else student_name
        super().__init__(coordinator, student_id, student_name, slug, school_name)
        self._attr_unique_id = f"arbor_{student_id}_timetable"
        self._attr_name = f"{first_name} Timetable"
        self._attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> int | None:
        """Return the number of lessons today."""
        data = self._get_student_data()
        if data is None:
            return None
        lessons = data.get(DATA_CALENDAR, [])
        return len(lessons)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit."""
        return "lessons"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full timetable as attributes."""
        data = self._get_student_data()
        if data is None:
            return {"lessons": []}
        lessons = data.get(DATA_CALENDAR, [])
        return {
            "lessons": lessons,
            "total": len(lessons),
        }


class ArborClubsSensor(ArborBaseSensor):
    """Sensor counting a student's registered or available clubs."""

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
        slug: str,
        bucket: str,
        friendly_name: str,
        icon: str,
        school_name: str,
    ) -> None:
        """Initialise the clubs sensor."""
        super().__init__(coordinator, student_id, student_name, slug, school_name)
        self._bucket = bucket
        self._attr_unique_id = f"arbor_{student_id}_clubs_{bucket}"
        self._attr_name = friendly_name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = "clubs"

    def _clubs(self) -> list[dict[str, str]] | None:
        data = self._get_student_data()
        if data is None:
            return None
        return data.get(DATA_CLUBS, {}).get(self._bucket, [])

    @property
    def native_value(self) -> int | None:
        """Return the number of clubs."""
        clubs = self._clubs()
        return None if clubs is None else len(clubs)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the clubs as attributes."""
        return {"clubs": self._clubs() or []}


class ArborSchoolMessagesSensor(
    CoordinatorEntity[ArborDataUpdateCoordinator], SensorEntity
):
    """When the latest school message arrived, with recent messages attached."""

    _attr_has_entity_name = True
    _attr_name = "Latest School Message"
    _attr_icon = "mdi:email-newsletter"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        school_domain: str,
        school_name: str,
    ) -> None:
        """Initialise the school messages sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"arbor_{school_domain}_latest_school_message"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"arbor_school_{school_domain}")},
            "name": f"Arbor - {school_name}",
            "manufacturer": "Arbor Education",
            "model": "Parent Portal",
            "configuration_url": f"https://{school_domain}",
        }

    def _messages(self) -> list[dict[str, str]]:
        if self.coordinator.data is None:
            return []
        account = self.coordinator.data.get(DATA_ACCOUNT, {})
        return account.get(DATA_SCHOOL_MESSAGES, [])

    @property
    def native_value(self) -> datetime | None:
        """Return when the newest message was received."""
        messages = self._messages()
        return parse_school_datetime(messages[0]["received"]) if messages else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the latest message and a short list of recent ones."""
        messages = self._messages()
        latest = messages[0] if messages else {}
        return {
            "subject": latest.get("subject"),
            "preview": latest.get("preview"),
            "body": latest.get("body"),
            "sent_by": latest.get("sent_by"),
            "message_id": latest.get("id"),
            "recent_messages": messages[:RECENT_MESSAGES_LIMIT],
            "total": len(messages),
        }
