"""Diagnostics: what each child is currently allowed to see."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    hub = entry.runtime_data
    return {
        "hub": {
            "status_topic": hub.status_topic,
            "common_label": hub.policy.common_label,
            "common_floors": list(hub.policy.common_floors),
            "blocked_cover_classes": sorted(hub.policy.blocked_cover_classes),
            "area_prefix": hub.area_prefix,
        },
        "climate_grants": [
            {
                "unit": g.unit, "room": g.obj, "entity": g.entity_id, "range": [g.min_temp, g.max_temp],
                "expire_after_minutes": g.expire_after_minutes, "expire_mode": g.expire_mode,
                "granted": g.key in hub.climate.granted,
            }
            for g in hub.climate.grants.values()
        ],
        "children": [
            {
                "unit": s.unit,
                "label": hub.children[s.unit].unit_label,
                "floors": list(hub.children[s.unit].unit_floors),
                "exposed_count": len(s.exposed),
                "exposed": list(s.exposed),
                "climate_granted": list(s.climate),
            }
            for s in hub.status()
        ],
    }
