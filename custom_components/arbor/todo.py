"""Todo platform for Arbor School integration."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_ASSIGNMENTS_DUE,
    DATA_ASSIGNMENTS_OVERDUE,
    DOMAIN,
)
from .coordinator import ArborDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Arbor todo entities from a config entry."""
    coordinator: ArborDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[TodoListEntity] = [
        ArborAssignmentsTodoList(
            coordinator=coordinator,
            student_id=student["student_id"],
            student_name=student["name"],
        )
        for student in coordinator.students
    ]

    async_add_entities(entities)


class ArborAssignmentsTodoList(
    CoordinatorEntity[ArborDataUpdateCoordinator], TodoListEntity
):
    """Read-only todo list of outstanding assignments for a student."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clipboard-text-clock"

    def __init__(
        self,
        coordinator: ArborDataUpdateCoordinator,
        student_id: str,
        student_name: str,
    ) -> None:
        """Initialise the todo list entity."""
        super().__init__(coordinator)
        self._student_id = student_id
        self._student_name = student_name
        first_name = student_name.split()[0] if student_name else student_name

        self._attr_unique_id = f"arbor_{student_id}_assignments_todo"
        self._attr_name = f"{first_name} Assignments"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"arbor_{student_id}")},
            "name": f"Arbor - {student_name}",
            "manufacturer": "Arbor Education",
            "model": "Parent Portal",
            "configuration_url": (f"https://{coordinator.client.school_domain}"),
        }

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return outstanding assignments (overdue + due) as todo items."""
        if self.coordinator.data is None:
            return None

        student_data = self.coordinator.data.get(self._student_id, {})

        items: list[TodoItem] = []
        seen_uids: set[str] = set()

        # Overdue first so they appear at the top
        for assignment in student_data.get(DATA_ASSIGNMENTS_OVERDUE, []):
            item = self._to_todo_item(assignment, "overdue")
            if item.uid not in seen_uids:
                seen_uids.add(item.uid)
                items.append(item)

        for assignment in student_data.get(DATA_ASSIGNMENTS_DUE, []):
            item = self._to_todo_item(assignment, "due")
            if item.uid not in seen_uids:
                seen_uids.add(item.uid)
                items.append(item)

        return items

    def _to_todo_item(self, assignment: dict[str, Any], bucket: str) -> TodoItem:
        """Convert an assignment dict to a TodoItem."""
        title = assignment.get("title", "")
        due_raw = assignment.get("due_date", "")
        due_date = _parse_due_date(due_raw)
        status_text = assignment.get("status") or None

        uid = f"{bucket}:{due_raw}:{title}"

        return TodoItem(
            uid=uid,
            summary=title,
            status=TodoItemStatus.NEEDS_ACTION,
            due=due_date,
            description=status_text,
        )


def _parse_due_date(text: str) -> date | None:
    """Parse Arbor's 'DD MMM YYYY' format into a date."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d %b %Y").date()
    except ValueError:
        return None
