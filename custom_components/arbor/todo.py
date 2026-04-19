"""Todo platform for Arbor School integration."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_ASSIGNMENTS_DUE,
    DATA_ASSIGNMENTS_OVERDUE,
    DOMAIN,
)
from .coordinator import ArborDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORE_SAVE_DELAY = 2.0


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
    """Todo list of outstanding assignments for a student.

    Items ticked off in HA are hidden locally only — Arbor's parent portal
    has no "mark submitted" endpoint, so ticks are a local UI convenience.
    When the student actually submits in Arbor and the item drops out of
    the due/overdue feeds, the local "completed" marker is auto-pruned.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:clipboard-text-clock"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

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

        self._completed_uids: set[str] = set()
        self._store: Store | None = None

    async def async_added_to_hass(self) -> None:
        """Load persisted locally-completed UIDs from storage."""
        await super().async_added_to_hass()
        entry_id = self.coordinator.config_entry.entry_id
        self._store = Store(
            self.hass,
            _STORAGE_VERSION,
            f"arbor.{entry_id}.completed_todos.{self._student_id}",
        )
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._completed_uids = set(data.get("completed_uids", []))
            self.async_write_ha_state()

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
            item = self._to_todo_item(assignment)
            if item.uid in seen_uids or item.uid in self._completed_uids:
                continue
            seen_uids.add(item.uid)
            items.append(item)

        for assignment in student_data.get(DATA_ASSIGNMENTS_DUE, []):
            item = self._to_todo_item(assignment)
            if item.uid in seen_uids or item.uid in self._completed_uids:
                continue
            seen_uids.add(item.uid)
            items.append(item)

        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick/untick locally — never sent to Arbor."""
        if item.uid is None:
            return
        if item.status == TodoItemStatus.COMPLETED:
            self._completed_uids.add(item.uid)
        elif item.status == TodoItemStatus.NEEDS_ACTION:
            self._completed_uids.discard(item.uid)
        else:
            return
        self._schedule_save()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Prune completed UIDs that no longer appear in Arbor's feed."""
        if self.coordinator.data is not None and self._completed_uids:
            student_data = self.coordinator.data.get(self._student_id, {})
            live_uids = {
                _stable_uid(a)
                for bucket in (DATA_ASSIGNMENTS_OVERDUE, DATA_ASSIGNMENTS_DUE)
                for a in student_data.get(bucket, [])
            }
            stale = self._completed_uids - live_uids
            if stale:
                self._completed_uids -= stale
                self._schedule_save()
        super()._handle_coordinator_update()

    def _to_todo_item(self, assignment: dict[str, Any]) -> TodoItem:
        """Convert an assignment dict to a TodoItem."""
        title = assignment.get("title", "")
        due_raw = assignment.get("due_date", "")
        status_text = assignment.get("status") or None

        return TodoItem(
            uid=_stable_uid(assignment),
            summary=title,
            status=TodoItemStatus.NEEDS_ACTION,
            due=_parse_due_date(due_raw),
            description=status_text,
        )

    def _schedule_save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(
                lambda: {"completed_uids": sorted(self._completed_uids)},
                _STORE_SAVE_DELAY,
            )


def _stable_uid(assignment: dict[str, Any]) -> str:
    """Return a UID that survives due→overdue transitions."""
    return f"{assignment.get('due_date', '')}:{assignment.get('title', '')}"


def _parse_due_date(text: str) -> date | None:
    """Parse Arbor's 'DD MMM YYYY' format into a date."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d %b %Y").date()
    except ValueError:
        return None
