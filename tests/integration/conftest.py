"""A small fake property: two children, a common floor, and a few entities."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.unity.const import DOMAIN


def published(mqtt_mock) -> dict[str, tuple[str, bool]]:
    """topic -> (last payload, retain) from the mocked MQTT client."""
    out: dict[str, tuple[str, bool]] = {}
    for call in mqtt_mock.async_publish.call_args_list:
        topic, payload, _qos, retain = call.args[:4]
        out[topic] = (payload, retain)
    return out


def add_entity(hass: HomeAssistant, entity_id: str, *, area=None, labels=(), state="on", **attrs: Any) -> None:
    domain, obj = entity_id.split(".")
    reg = er.async_get(hass)
    entry = reg.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
    assert entry.entity_id == entity_id
    reg.async_update_entity(entity_id, area_id=area, labels=set(labels))
    hass.states.async_set(entity_id, state, {"friendly_name": obj.replace("_", " ").title(), **attrs})


@pytest.fixture
def world(hass: HomeAssistant) -> dict[str, str]:
    """Floors alpha/beta/shared, an area on each, labels, and ids by name."""
    floors = fr.async_get(hass)
    areas = ar.async_get(hass)
    labels = lr.async_get(hass)
    ids: dict[str, str] = {}
    for name in ("alpha", "beta", "shared"):
        f = floors.async_create(name.title())
        a = areas.async_create(f"L2 {name.title()}" if name != "shared" else "Entry", floor_id=f.floor_id)
        ids[f"floor_{name}"], ids[f"area_{name}"] = f.floor_id, a.id
        ids[f"label_{name}"] = labels.async_create(f"Live: {name.title()}").label_id
    return ids


def make_entry(world: dict[str, str], **overrides: Any) -> MockConfigEntry:
    data = {
        "hub_status_topic": "ha-unity/hub/status",
        "common_label": world["label_shared"],
        "common_floors": [world["floor_shared"]],
        "area_prefix_regex": r"^L[0-9]+ ",
        "blocked_cover_classes": ["garage", "gate", "door"],
    }
    data.update(overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        subentries_data=[
            ConfigSubentryData(
                data={"unit": "alpha", "unit_label": world["label_alpha"], "unit_floors": [world["floor_alpha"]]},
                subentry_type="child", title="alpha", unique_id=None,
            ),
            ConfigSubentryData(
                data={"unit": "beta", "unit_label": world["label_beta"], "unit_floors": [world["floor_beta"]]},
                subentry_type="child", title="beta", unique_id=None,
            ),
        ],
    )


@pytest.fixture(autouse=True)
async def unload_everything(hass: HomeAssistant):
    """Flush pending timers and unload all entries (MQTT's included) so no timer
    outlives the test. This keeps the harness's lingering-timer check meaningful
    for Unity's own debounce and interval timers."""
    yield
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1))
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries():
        if entry.state.value == "loaded":
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """MQTT's own subscription-batching timer (EnsureJobAfterCooldown) re-arms
    itself and cannot be flushed from a test. Unity's timers are checked
    explicitly in test_hub.py::test_stop_leaves_no_timers."""
    return True
