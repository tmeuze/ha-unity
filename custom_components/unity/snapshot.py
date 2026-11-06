"""Turn Home Assistant's registries and states into the policy's plain facts.

This is the only place that knows how labels, areas, floors and devices relate
in Home Assistant. The rules, matching what was verified live on the blueprints:

* An entity's labels are its own (not its device's).
* Its area is its own, falling back to its device's area.
* A floor's areas come from the area registry.
* The device name is the one the user set (``name_by_user``), else the
  integration's default name.
* Disabled entities are skipped. Entities with no registry entry have no
  labels and so can never be exposed.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)

from .policy import EXPOSED_DOMAINS, EntityFacts, FloorAreas, domain_of


def floor_areas(hass: HomeAssistant) -> dict[str, frozenset[str]]:
    """floor id -> the ids of the areas on that floor."""
    area_reg = ar.async_get(hass)
    floor_reg = fr.async_get(hass)
    return {
        floor.floor_id: frozenset(
            a.id for a in ar.async_entries_for_floor(area_reg, floor.floor_id)
        )
        for floor in floor_reg.async_list_floors()
    }


def _facts(
    hass: HomeAssistant,
    entry: er.RegistryEntry,
    area_reg: ar.AreaRegistry,
    dev_reg: dr.DeviceRegistry,
) -> EntityFacts:
    device = dev_reg.async_get(entry.device_id) if entry.device_id else None
    area_id = entry.area_id or (device.area_id if device else None)
    area = area_reg.async_get_area(area_id) if area_id else None
    state = hass.states.get(entry.entity_id)
    return EntityFacts(
        entity_id=entry.entity_id,
        area_id=area_id,
        labels=frozenset(entry.labels),
        state=state.state if state else "unavailable",
        attributes=dict(state.attributes) if state else {},
        device_id=device.id if device else None,
        device_name=(device.name_by_user or device.name) if device else None,
        friendly_name=state.attributes.get("friendly_name") if state else None,
        area_name=area.name if area else None,
    )


def entity_facts(
    hass: HomeAssistant, entity_id: str, domains: frozenset[str] = EXPOSED_DOMAINS
) -> EntityFacts | None:
    """Facts for one entity, or None if it is unknown, disabled or out of scope."""
    if domain_of(entity_id) not in domains:
        return None
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.disabled_by:
        return None
    return _facts(hass, entry, ar.async_get(hass), dr.async_get(hass))


def all_facts(hass: HomeAssistant) -> list[EntityFacts]:
    """Facts for every enabled registry entity in an exposable domain."""
    area_reg = ar.async_get(hass)
    dev_reg = dr.async_get(hass)
    return [
        _facts(hass, entry, area_reg, dev_reg)
        for entry in er.async_get(hass).entities.values()
        if entry.domain in EXPOSED_DOMAINS and not entry.disabled_by
    ]


def common_area_names(hass: HomeAssistant, common_floors: tuple[str, ...], areas: FloorAreas) -> frozenset[str]:
    """Names of the areas on the common floors (they pass through unchanged)."""
    area_reg = ar.async_get(hass)
    names: set[str] = set()
    for floor in common_floors:
        for area_id in areas.get(floor, ()):
            area = area_reg.async_get_area(area_id)
            if area:
                names.add(area.name)
    return frozenset(names)
