from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.unity.const import DOMAIN

from .conftest import make_entry


async def _start_hub(hass: HomeAssistant, world, **user) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    data = {
        "hub_status_topic": "ha-unity/hub/status",
        "common_label": world["label_shared"],
        "common_floors": [world["floor_shared"]],
        "area_prefix_regex": "",
        "blocked_cover_classes": ["garage"],
    }
    data.update(user)
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_create_hub(hass: HomeAssistant, mqtt_mock, world) -> None:
    result = await _start_hub(hass, world)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["common_label"] == world["label_shared"]


async def test_only_one_hub(hass: HomeAssistant, mqtt_mock, world) -> None:
    await _start_hub(hass, world)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"


async def test_hub_validation(hass: HomeAssistant, mqtt_mock, world) -> None:
    r = await _start_hub(hass, world, hub_status_topic="bad/#")
    assert r["type"] is FlowResultType.FORM and r["errors"]["hub_status_topic"] == "invalid_topic"
    r = await _start_hub(hass, world, area_prefix_regex="(")
    assert r["errors"]["area_prefix_regex"] == "invalid_regex"
    r = await _start_hub(hass, world, common_floors=[])      # label without floors
    assert r["errors"]["base"] == "common_pair"


async def test_hub_without_a_common_label_is_allowed(hass: HomeAssistant, mqtt_mock, world) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"hub_status_topic": "ha-unity/hub/status", "area_prefix_regex": "", "blocked_cover_classes": ["garage"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _child_flow(hass, entry, data):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "child"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


async def test_add_child(hass: HomeAssistant, mqtt_mock, world) -> None:
    entry = make_entry(world)
    entry.add_to_hass(hass)
    result = await _child_flow(
        hass, entry,
        {"unit": "gamma", "unit_label": world["label_alpha"], "unit_floors": [world["floor_alpha"]]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert any(s.data["unit"] == "gamma" for s in entry.subentries.values())


async def test_child_validation(hass: HomeAssistant, mqtt_mock, world) -> None:
    entry = make_entry(world)
    entry.add_to_hass(hass)
    base = {"unit_label": world["label_alpha"], "unit_floors": [world["floor_alpha"]]}
    for bad in ["Bad Name", "a/b", "a+b", "#", "homeassistant"]:
        r = await _child_flow(hass, entry, {"unit": bad, **base})
        assert r["type"] is FlowResultType.FORM and r["errors"]["unit"] == "invalid_unit", bad
    r = await _child_flow(hass, entry, {"unit": "alpha", **base})          # already exists
    assert r["errors"]["unit"] == "duplicate_unit"
    r = await _child_flow(hass, entry, {"unit": "gamma", "unit_label": world["label_alpha"], "unit_floors": []})
    assert r["errors"]["unit_floors"] == "no_floors"


async def test_reconfigure_child(hass: HomeAssistant, mqtt_mock, world) -> None:
    entry = make_entry(world)
    entry.add_to_hass(hass)
    sub = next(s for s in entry.subentries.values() if s.data["unit"] == "alpha")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "child"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": sub.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"unit": "alpha", "unit_label": world["label_beta"], "unit_floors": [world["floor_beta"]]},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.subentries[sub.subentry_id].data["unit_label"] == world["label_beta"]
