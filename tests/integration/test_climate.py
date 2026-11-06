"""Thermostats: publishing, the setpoint command path, expiry, revocation."""
import json
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_mqtt_message,
    async_fire_time_changed,
    async_mock_service,
)

from .conftest import add_thermostat, climate_subentry, make_entry, published

K = "alpha/climate/kitchen"


async def setup(hass, world, *subs, entry_overrides=None, label="alpha", area="alpha", **thermostat):
    add_thermostat(hass, area=world[f"area_{area}"], labels={world[f"label_{label}"]}, **thermostat)
    entry = make_entry(world, extra_subentries=subs or (climate_subentry(world),), **(entry_overrides or {}))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def later(hass, **delta):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**delta))
    await hass.async_block_till_done()


# ------------------------------------------------------------------ publishing
async def test_publishes_discovery_and_values(hass: HomeAssistant, mqtt_mock, world) -> None:
    await setup(hass, world)
    sent = published(mqtt_mock)
    cfg = json.loads(sent["alpha/ha/climate/kitchen/config"][0])
    assert cfg["unique_id"] == "alpha_climate_kitchen" and cfg["name"] == "Kitchen"
    assert cfg["modes"] == ["auto"] and (cfg["min_temp"], cfg["max_temp"]) == (64.0, 80.0)
    assert cfg["temperature_command_topic"] == f"{K}/target/set"
    assert sent[f"{K}/current"] == ("75", True)
    assert sent[f"{K}/target/state"] == ("71", True)
    assert sent[f"{K}/action"] == ("idle", True)
    assert sent[f"{K}/availability"] == ("online", True)
    assert not any(t.startswith("beta/") and "climate" in t for t in sent)       # not another child's


async def test_not_published_without_label_or_on_the_wrong_floor(hass, mqtt_mock, world) -> None:
    await setup(hass, world, area="beta")                       # alpha label on a beta-floor area
    assert not any("climate" in t and t.startswith("alpha/") and t.endswith("config") and published(mqtt_mock)[t][0]
                   for t in published(mqtt_mock))


async def test_climate_is_never_a_plain_exposed_entity(hass, mqtt_mock, world) -> None:
    """A labelled thermostat is not exposed as an entity; only the explicit grant publishes it."""
    add_thermostat(hass, area=world["area_alpha"], labels={world["label_alpha"]})
    entry = make_entry(world)                                    # no thermostat grant
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not any("climate" in t for t in published(mqtt_mock))


async def test_common_label_grants_to_a_child(hass, mqtt_mock, world) -> None:
    await setup(hass, world, label="shared", area="shared")
    assert published(mqtt_mock)["alpha/ha/climate/kitchen/config"][0] != ""


async def test_state_changes_are_republished(hass, mqtt_mock, world) -> None:
    await setup(hass, world)
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("climate.roommind_kitchen", "heat",
                          {**__import__("tests.integration.conftest", fromlist=["ROOMMIND"]).ROOMMIND,
                           "current_temperature": 72, "hvac_action": "heating"})
    await hass.async_block_till_done()
    sent = published(mqtt_mock)
    assert sent[f"{K}/current"][0] == "72" and sent[f"{K}/action"][0] == "heating"
    assert f"{K}/target/state" not in sent                                      # unchanged: not repeated


async def test_unavailable_thermostat_goes_offline(hass, mqtt_mock, world) -> None:
    await setup(hass, world)
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("climate.roommind_kitchen", "unavailable", {"friendly_name": "Kitchen"})
    await hass.async_block_till_done()
    sent = published(mqtt_mock)
    assert sent[f"{K}/availability"][0] == "offline" and f"{K}/current" not in sent


async def test_sensors_can_replace_the_attributes(hass, mqtt_mock, world) -> None:
    hass.states.async_set("sensor.room_t", "68.4")
    hass.states.async_set("sensor.room_goal", "70")
    sub = climate_subentry(world, current_sensor="sensor.room_t", target_sensor="sensor.room_goal")
    await setup(hass, world, sub)
    sent = published(mqtt_mock)
    assert sent[f"{K}/current"][0] == "68.4" and sent[f"{K}/target/state"][0] == "70"
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("sensor.room_t", "69")
    await hass.async_block_till_done()
    assert published(mqtt_mock)[f"{K}/current"][0] == "69"


# --------------------------------------------------------------------- commands
async def test_setpoint_is_clamped_and_calls_set_temperature(hass, mqtt_mock, world) -> None:
    calls = async_mock_service(hass, "climate", "set_temperature")
    await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    async_fire_mqtt_message(hass, f"{K}/target/set", "120")
    async_fire_mqtt_message(hass, f"{K}/target/set", "10")
    await hass.async_block_till_done()
    assert [c.data["temperature"] for c in calls] == [72.0, 80.0, 64.0]
    assert all(c.data["entity_id"] == "climate.roommind_kitchen" for c in calls)


async def test_commands_that_must_be_ignored(hass, mqtt_mock, world) -> None:
    calls = async_mock_service(hass, "climate", "set_temperature")
    other = async_mock_service(hass, "climate", "turn_off")
    await setup(hass, world)
    for topic, payload, retain in [
        (f"{K}/target/set", "72", True),                                   # retained: never executed
        ("beta/climate/kitchen/target/set", "72", False),                  # another child (not subscribed/granted)
        ("alpha/climate/unknown_room/target/set", "72", False),            # no such grant
        (f"{K}/target/set", "abc", False), (f"{K}/target/set", "", False),
        (f"{K}/target/set", '{"hvac_mode":"off"}', False),
        (f"{K}/mode/set", "off", False), (f"{K}/set", "72", False),
    ]:
        async_fire_mqtt_message(hass, topic, payload, 0, retain)
    await hass.async_block_till_done()
    assert calls == [] and other == []


async def test_command_after_the_grant_is_revoked_is_ignored(hass, mqtt_mock, world) -> None:
    calls = async_mock_service(hass, "climate", "set_temperature")
    await setup(hass, world)
    er.async_get(hass).async_update_entity("climate.roommind_kitchen", labels=set())
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    assert calls == []


async def test_a_failing_service_does_not_raise_or_arm_expiry(hass, mqtt_mock, world) -> None:
    from homeassistant.exceptions import HomeAssistantError

    async def boom(call):
        raise HomeAssistantError("controller offline")

    hass.services.async_register("climate", "set_temperature", boom)
    entry = await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    assert entry.runtime_data.climate._timers == {}


# ----------------------------------------------------------------------- expiry
async def test_override_is_released_after_the_timeout(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    await later(hass, minutes=29)
    assert release == []
    await later(hass, minutes=31)
    assert [(c.data["entity_id"], c.data["preset_mode"]) for c in release] == [("climate.roommind_kitchen", "none")]


async def test_a_newer_command_restarts_the_countdown(hass, mqtt_mock, world) -> None:
    """The first timer must be cancelled: otherwise both would fire and the
    guest's second setpoint would be released twice (and, over time, early)."""
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    entry = await setup(hass, world)
    ctl = entry.runtime_data.climate
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    first = ctl._timers[("alpha", "kitchen")]
    cancelled = []
    ctl._timers[("alpha", "kitchen")] = lambda: (cancelled.append(True), first())[1]   # spy on the cancel
    async_fire_mqtt_message(hass, f"{K}/target/set", "73")
    await hass.async_block_till_done()
    assert cancelled == [True]                                   # the earlier countdown was cancelled
    assert ctl._timers[("alpha", "kitchen")] is not first
    await later(hass, minutes=29)
    assert release == []
    await later(hass, minutes=31)
    assert len(release) == 1                                     # released once, not twice


async def test_no_expiry_when_not_configured(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    sub = climate_subentry(world, expire_after_minutes=0, expire_mode="none")
    entry = await setup(hass, world, sub)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    await later(hass, hours=48)
    assert release == [] and entry.runtime_data.climate._timers == {}


async def test_expiry_does_nothing_if_the_entity_has_no_none_preset(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    await setup(hass, world, preset_modes=["comfort", "eco"])
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    await later(hass, hours=1)
    assert release == []


async def test_expiry_is_skipped_if_the_grant_was_revoked_meanwhile(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    er.async_get(hass).async_update_entity("climate.roommind_kitchen", labels=set())
    await later(hass, hours=1)
    assert release == []


async def test_a_deadline_that_passed_while_off_is_released_at_startup(hass, mqtt_mock, world, hass_storage) -> None:
    release = async_mock_service(hass, "climate", "set_preset_mode")
    hass_storage["unity.published"] = {
        "version": 1, "minor_version": 1, "key": "unity.published",
        "data": {"entities": {}, "climate": {"alpha": ["kitchen"]},
                 "expiry": {"alpha/kitchen": dt_util.utcnow().timestamp() - 600}},
    }
    await setup(hass, world)
    assert len(release) == 1 and release[0].data["preset_mode"] == "none"


async def test_a_future_deadline_is_re_armed_after_a_restart(hass, mqtt_mock, world, hass_storage) -> None:
    release = async_mock_service(hass, "climate", "set_preset_mode")
    hass_storage["unity.published"] = {
        "version": 1, "minor_version": 1, "key": "unity.published",
        "data": {"entities": {}, "climate": {"alpha": ["kitchen"]},
                 "expiry": {"alpha/kitchen": dt_util.utcnow().timestamp() + 600}},
    }
    await setup(hass, world)
    assert release == []
    await later(hass, minutes=9)
    assert release == []
    await later(hass, minutes=11)
    assert len(release) == 1


async def test_the_deadline_is_persisted_when_a_setpoint_arrives(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    entry = await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    data = entry.runtime_data._serialise()
    assert "alpha/kitchen" in data["expiry"]
    assert data["expiry"]["alpha/kitchen"] > dt_util.utcnow().timestamp() + 25 * 60


# ------------------------------------------------------------ revocation / stop
async def test_revoked_when_the_label_is_removed(hass, mqtt_mock, world) -> None:
    await setup(hass, world)
    er.async_get(hass).async_update_entity("climate.roommind_kitchen", labels=set())
    await hass.async_block_till_done()
    await later(hass, seconds=6)
    sent = published(mqtt_mock)
    for topic in ("alpha/ha/climate/kitchen/config", f"{K}/current", f"{K}/target/state", f"{K}/action", f"{K}/availability"):
        assert sent[topic] == ("", True), topic


async def test_removing_the_grant_clears_it(hass, mqtt_mock, world) -> None:
    entry = await setup(hass, world)
    sub_id = next(i for i, s in entry.subentries.items() if s.subentry_type == "climate")
    hass.config_entries.async_remove_subentry(entry, sub_id)
    await hass.async_block_till_done()
    assert published(mqtt_mock)["alpha/ha/climate/kitchen/config"] == ("", True)


async def test_stale_climate_topics_from_a_previous_run_are_cleaned_up(hass, mqtt_mock, world, hass_storage) -> None:
    hass_storage["unity.published"] = {
        "version": 1, "minor_version": 1, "key": "unity.published",
        "data": {"entities": {}, "climate": {"alpha": ["old_room"], "gone": ["x"]}, "expiry": {}},
    }
    await setup(hass, world)
    sent = published(mqtt_mock)
    assert sent["alpha/ha/climate/old_room/config"] == ("", True)
    assert sent["gone/ha/climate/x/config"] == ("", True)


async def test_stop_cancels_the_expiry_timer(hass, mqtt_mock, world) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    release = async_mock_service(hass, "climate", "set_preset_mode")
    entry = await setup(hass, world)
    async_fire_mqtt_message(hass, f"{K}/target/set", "72")
    await hass.async_block_till_done()
    ctl = entry.runtime_data.climate
    assert ctl._timers
    cancelled = []
    for key, cancel in list(ctl._timers.items()):
        ctl._timers[key] = (lambda c=cancel: (cancelled.append(True), c())[1])
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancelled == [True] and ctl._timers == {}
    await later(hass, hours=2)
    assert release == []


async def test_removing_the_hub_clears_the_thermostat(hass, mqtt_mock, world) -> None:
    entry = await setup(hass, world)
    await hass.config_entries.async_unload(entry.entry_id)
    mqtt_mock.async_publish.reset_mock()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert published(mqtt_mock)["alpha/ha/climate/kitchen/config"] == ("", True)


async def test_invalid_grants_are_ignored(hass, mqtt_mock, world) -> None:
    bad = [
        climate_subentry(world, unit="nobody"),                       # unknown child
        climate_subentry(world, obj="Bad/Room"),
        climate_subentry(world, obj="r2", min_temp=80.0, max_temp=64.0),
        climate_subentry(world, obj="r3", entity="light.kitchen"),
        climate_subentry(world, obj="r4", expire_mode="rm -rf"),
    ]
    entry = await setup(hass, world, *bad)
    assert entry.runtime_data.climate.grants == {}
    assert not any("climate" in t and published(mqtt_mock)[t][0] for t in published(mqtt_mock))


async def test_old_store_without_climate_still_loads(hass, mqtt_mock, world, hass_storage) -> None:
    hass_storage["unity.published"] = {
        "version": 1, "minor_version": 1, "key": "unity.published", "data": {"alpha": ["light.deleted"]},
    }
    await setup(hass, world)
    assert published(mqtt_mock)["alpha/ha/light/deleted/config"] == ("", True)


async def test_diagnostics_list_the_grants(hass, mqtt_mock, world) -> None:
    from custom_components.unity.diagnostics import async_get_config_entry_diagnostics

    entry = await setup(hass, world)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["climate_grants"][0]["entity"] == "climate.roommind_kitchen" and diag["climate_grants"][0]["granted"]
    assert {c["unit"]: c["climate_granted"] for c in diag["children"]}["alpha"] == ["kitchen"]
