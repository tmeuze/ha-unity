"""Unity exposure policy: pure logic, no Home Assistant imports.

Everything that decides *what a child may see or control* lives here so it can
be tested exhaustively without a running Home Assistant. The glue that reads
Home Assistant's registries and publishes over MQTT lives in ``manager.py``.

The rule, fail closed. An entity is exposed to a child only if

    (the child's label AND an area on one of the child's floors)
    OR (the common label AND an area on one of the common floors)

and its domain is on a fixed list. A label on the wrong floor, an entity with
no area, an unlabelled entity, a missing or empty configuration: none of them
exposes anything. The domain lists are deliberately not configuration, apart
from the blocked cover device classes.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Domains a child may see (state) and the subset it may command.
EXPOSED_DOMAINS: frozenset[str] = frozenset(
    {"light", "switch", "sensor", "binary_sensor", "fan", "scene", "cover"}
)
COMMAND_DOMAINS: frozenset[str] = frozenset({"light", "switch", "fan", "scene", "cover"})

DEFAULT_BLOCKED_COVER_CLASSES: frozenset[str] = frozenset({"garage", "gate", "door"})
UNAVAILABLE_STATES: frozenset[str] = frozenset({"unavailable", "unknown"})


def domain_of(entity_id: str) -> str:
    """The domain part of an entity id ("" if malformed)."""
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


@dataclass(frozen=True, slots=True)
class EntityFacts:
    """What the policy and the adapters need to know about one hub entity."""

    entity_id: str
    area_id: str | None = None
    labels: frozenset[str] = frozenset()
    state: str = "unknown"
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Display names, resolved by the glue (the user-set device name wins over
    # the integration's default name).
    device_id: str | None = None
    device_name: str | None = None
    friendly_name: str | None = None
    area_name: str | None = None

    @property
    def domain(self) -> str:
        return domain_of(self.entity_id)

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1] if "." in self.entity_id else ""


@dataclass(frozen=True, slots=True)
class HubPolicy:
    """Settings shared by every child of one hub."""

    common_label: str | None = None
    common_floors: tuple[str, ...] = ()
    blocked_cover_classes: frozenset[str] = DEFAULT_BLOCKED_COVER_CLASSES


@dataclass(frozen=True, slots=True)
class ChildPolicy:
    """One child instance (a "unit")."""

    unit: str
    unit_label: str | None = None
    unit_floors: tuple[str, ...] = ()


FloorAreas = Mapping[str, Collection[str]]


def _areas(floors: Iterable[str], floor_areas: FloorAreas) -> frozenset[str]:
    out: set[str] = set()
    for floor in floors:
        out.update(floor_areas.get(floor, ()))
    return frozenset(out)


def _cover_blocked(facts: EntityFacts, hub: HubPolicy) -> bool:
    """Covers of a blocked class, or whose state is unknown, are never exposed."""
    if facts.domain != "cover":
        return False
    if facts.state in UNAVAILABLE_STATES:
        return True
    return facts.attributes.get("device_class") in hub.blocked_cover_classes


def is_exposed(
    facts: EntityFacts,
    child: ChildPolicy,
    hub: HubPolicy,
    floor_areas: FloorAreas,
) -> bool:
    """Whether ``facts`` may be seen by (and, in command domains, controlled by) ``child``."""
    if facts.domain not in EXPOSED_DOMAINS:
        return False
    if facts.area_id is None:
        return False
    if _cover_blocked(facts, hub):
        return False
    if (
        child.unit_label
        and child.unit_label in facts.labels
        and facts.area_id in _areas(child.unit_floors, floor_areas)
    ):
        return True
    return bool(
        hub.common_label
        and hub.common_label in facts.labels
        and facts.area_id in _areas(hub.common_floors, floor_areas)
    )


def exposed_set(
    entities: Iterable[EntityFacts],
    child: ChildPolicy,
    hub: HubPolicy,
    floor_areas: FloorAreas,
) -> dict[str, EntityFacts]:
    """The entities a child may see, keyed by entity id."""
    return {
        e.entity_id: e
        for e in entities
        if is_exposed(e, child, hub, floor_areas)
    }
