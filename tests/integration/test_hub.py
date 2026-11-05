import json

from homeassistant.core import HomeAssistant

from custom_components.unity.const import DOMAIN

from .conftest import add_entity, make_entry, published


async def test_publishes_only_what_each_child_may_see(hass: HomeAssistant, mqtt_mock, world) -> None:
    add_entity(hass, "light.alpha_lamp", area=world["area_alpha"], labels={world["label_alpha"]}, brightness=100,
               supported_color_modes=["brightness"])
    add_entity(hass, "light.beta_lamp", area=world["area_beta"], labels={world["label_beta"]})
    add_entity(hass, "switch.hall", area=world["area_shared"], labels={world["label_shared"]})
    add_entity(hass, "light.unlabelled", area=world["area_alpha"])
    add_entity(hass, "lock.front", area=world["area_shared"], labels={world["label_shared"]})

    entry = make_entry(world)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sent = published(mqtt_mock)
    assert sent["ha-unity/hub/status"] == ("online", True)
    # alpha: its lamp and the common switch
    assert "alpha/ha/light/alpha_lamp/config" in sent
    assert "alpha/ha/switch/hall/config" in sent
    assert "alpha/ha/light/beta_lamp/config" not in sent
    # beta: its lamp and the common switch
    assert "beta/ha/light/beta_lamp/config" in sent
    assert "beta/ha/switch/hall/config" in sent
    assert "beta/ha/light/alpha_lamp/config" not in sent
    # nothing else, ever
    assert not any("unlabelled" in t or "lock" in t for t in sent)
    # retained, with state and availability
    assert all(retain for _payload, retain in sent.values())
    assert json.loads(sent["alpha/light/alpha_lamp/state"][0])["brightness"] == 100
    assert sent["alpha/light/alpha_lamp/availability"][0] == "online"
    cfg = json.loads(sent["alpha/ha/light/alpha_lamp/config"][0])
    assert cfg["availability"][0] == {"topic": "ha-unity/hub/status"}


# ------------------------------------------------------------------ helpers
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_mqtt_message,
    async_fire_time_changed,
    async_mock_service,
)


async def setup_hub(hass, world, **overrides):
    entry = make_entry(world, **overrides)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def settle(hass, seconds=6):
    """Let the registry-event debounce elapse."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def cmd_world(hass, world):
    add_entity(hass, "light.alpha_lamp", area=world["area_alpha"], labels={world["label_alpha"]},
               supported_color_modes=["brightness"])
    add_entity(hass, "light.beta_lamp", area=world["area_beta"], labels={world["label_beta"]},
               supported_color_modes=["brightness"])
    add_entity(hass, "light.unlabelled", area=world["area_alpha"], supported_color_modes=["brightness"])
    add_entity(hass, "lock.front", area=world["area_alpha"], labels={world["label_alpha"]})
    add_entity(hass, "switch.hall", area=world["area_shared"], labels={world["label_shared"]})


# ----------------------------------------------------------------- commands
async def test_command_for_an_exposed_entity_runs(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/light/alpha_lamp/set", '{"state":"ON","brightness":300}')
    await hass.async_block_till_done()
    assert len(on) == 1
    assert on[0].data == {"entity_id": "light.alpha_lamp", "brightness": 255}   # clamped


async def test_common_entity_is_controllable_by_both_children(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "switch", "turn_on")
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/switch/hall/set", "ON")
    async_fire_mqtt_message(hass, "beta/switch/hall/set", "ON")
    await hass.async_block_till_done()
    assert len(on) == 2


async def test_other_childs_entity_is_ignored(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/light/beta_lamp/set", '{"state":"ON"}')
    async_fire_mqtt_message(hass, "beta/light/alpha_lamp/set", '{"state":"ON"}')
    await hass.async_block_till_done()
    assert on == []


async def test_unlabelled_and_out_of_scope_entities_are_ignored(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    unlock = async_mock_service(hass, "lock", "unlock")
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/light/unlabelled/set", '{"state":"ON"}')
    async_fire_mqtt_message(hass, "alpha/lock/front/set", '{"state":"ON"}')
    async_fire_mqtt_message(hass, "alpha/light/does_not_exist/set", '{"state":"ON"}')
    await hass.async_block_till_done()
    assert on == [] and unlock == []


async def test_entity_and_service_come_from_the_topic_not_the_payload(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    unlock = async_mock_service(hass, "lock", "unlock")
    await setup_hub(hass, world)
    evil = '{"state":"ON","entity_id":"light.beta_lamp","service":"lock.unlock","domain":"lock"}'
    async_fire_mqtt_message(hass, "alpha/light/alpha_lamp/set", evil)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in on] == ["light.alpha_lamp"]
    assert unlock == []


async def test_retained_commands_are_never_executed(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/light/alpha_lamp/set", '{"state":"ON"}', retain=True)
    await hass.async_block_till_done()
    assert on == []


async def test_malformed_topics_and_payloads_do_nothing(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    await setup_hub(hass, world)
    hub = hass.config_entries.async_entries("unity")[0].runtime_data
    for topic, payload in [
        ("alpha/light/alpha_lamp/state", '{"state":"ON"}'),
        ("alpha/ha/light/alpha_lamp/set", '{"state":"ON"}'),
        ("alpha/light/Alpha_Lamp/set", '{"state":"ON"}'),
        ("alpha/light/alpha_lamp/set", "not json"),
        ("alpha/light/alpha_lamp/set", ""),
        ("gamma/light/alpha_lamp/set", '{"state":"ON"}'),   # unknown child
    ]:
        assert await hub.async_handle_command(topic.split("/")[0], topic, payload) == []
    assert on == []


async def test_a_failing_service_does_not_raise(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    from homeassistant.exceptions import HomeAssistantError

    async def boom(call):
        raise HomeAssistantError("device offline")

    hass.services.async_register("light", "turn_on", boom)
    await setup_hub(hass, world)
    async_fire_mqtt_message(hass, "alpha/light/alpha_lamp/set", '{"state":"ON"}')
    await hass.async_block_till_done()   # no exception escapes


# ----------------------------------------------------------------- revocation
async def test_revoked_when_the_label_is_removed(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world)
    assert published(mqtt_mock)["alpha/ha/light/alpha_lamp/config"][0] != ""
    er.async_get(hass).async_update_entity("light.alpha_lamp", labels=set())
    await hass.async_block_till_done()
    await settle(hass)
    sent = published(mqtt_mock)
    assert sent["alpha/ha/light/alpha_lamp/config"] == ("", True)
    assert sent["alpha/light/alpha_lamp/state"] == ("", True)
    assert sent["alpha/light/alpha_lamp/availability"] == ("", True)


async def test_revoked_when_moved_to_another_floor(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world)
    er.async_get(hass).async_update_entity("light.alpha_lamp", area_id=world["area_beta"])
    await hass.async_block_till_done()
    await settle(hass)
    assert published(mqtt_mock)["alpha/ha/light/alpha_lamp/config"] == ("", True)


async def test_command_after_revocation_is_ignored_immediately(hass, mqtt_mock, world) -> None:
    """The policy is re-checked on every command, so a stale child dashboard
    cannot act before the debounced resync has even run."""
    cmd_world(hass, world)
    on = async_mock_service(hass, "light", "turn_on")
    await setup_hub(hass, world)
    er.async_get(hass).async_update_entity("light.alpha_lamp", labels=set())
    async_fire_mqtt_message(hass, "alpha/light/alpha_lamp/set", '{"state":"ON"}')
    await hass.async_block_till_done()
    assert on == []


async def test_stale_topics_from_a_previous_run_are_cleaned_up(hass, mqtt_mock, world, hass_storage) -> None:
    """An entity deleted while Home Assistant was off leaves retained topics;
    what was published is persisted so they are removed on the next start."""
    cmd_world(hass, world)
    hass_storage["unity.published"] = {
        "version": 1, "minor_version": 1, "key": "unity.published",
        "data": {"alpha": ["light.deleted_long_ago"], "gone_child": ["light.x"]},
    }
    await setup_hub(hass, world)
    sent = published(mqtt_mock)
    assert sent["alpha/ha/light/deleted_long_ago/config"] == ("", True)
    assert sent["gone_child/ha/light/x/config"] == ("", True)   # a removed child is cleared too


# ------------------------------------------------------------------- state
async def test_state_changes_are_published(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world)
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("light.alpha_lamp", "off", {"friendly_name": "Alpha Lamp",
                                                        "supported_color_modes": ["brightness"]})
    await hass.async_block_till_done()
    assert published(mqtt_mock)["alpha/light/alpha_lamp/state"][0] == '{"state": "OFF"}'
    assert "beta/light/alpha_lamp/state" not in published(mqtt_mock)


async def test_identical_state_is_not_republished(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world)
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("light.alpha_lamp", "on", {"friendly_name": "Alpha Lamp",
                                                       "supported_color_modes": ["brightness"], "noise": 1})
    await hass.async_block_till_done()
    assert "alpha/light/alpha_lamp/state" not in published(mqtt_mock)


async def test_unavailable_entity_goes_offline_without_state(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world)
    mqtt_mock.async_publish.reset_mock()
    hass.states.async_set("light.alpha_lamp", "unavailable", {"friendly_name": "Alpha Lamp"})
    await hass.async_block_till_done()
    sent = published(mqtt_mock)
    assert sent["alpha/light/alpha_lamp/availability"][0] == "offline"
    assert "alpha/light/alpha_lamp/state" not in sent


async def test_cover_becoming_unavailable_is_revoked(hass, mqtt_mock, world) -> None:
    add_entity(hass, "cover.blind", area=world["area_alpha"], labels={world["label_alpha"]},
               state="open", device_class="blind", supported_features=15)
    await setup_hub(hass, world)
    assert published(mqtt_mock)["alpha/ha/cover/blind/config"][0] != ""
    hass.states.async_set("cover.blind", "unavailable", {"device_class": "blind"})
    await hass.async_block_till_done()
    await settle(hass)
    assert published(mqtt_mock)["alpha/ha/cover/blind/config"] == ("", True)


async def test_blocked_cover_classes_are_never_exposed(hass, mqtt_mock, world) -> None:
    add_entity(hass, "cover.garage", area=world["area_alpha"], labels={world["label_alpha"]},
               state="closed", device_class="garage", supported_features=15)
    await setup_hub(hass, world)
    assert "alpha/ha/cover/garage/config" not in published(mqtt_mock)
    close = async_mock_service(hass, "cover", "close_cover")
    async_fire_mqtt_message(hass, "alpha/cover/garage/set", '{"command":"close"}')
    await hass.async_block_till_done()
    assert close == []


# ------------------------------------------------------ hub status / lifecycle
async def test_hub_marker_is_re_announced_on_reconnect(hass, mqtt_mock, world) -> None:
    """After an unclean disconnect the broker publishes the will ("offline");
    only the hub can restore "online"."""
    cmd_world(hass, world)
    entry = await setup_hub(hass, world)
    mqtt_mock.async_publish.reset_mock()
    entry.runtime_data._on_mqtt_status(True)
    await hass.async_block_till_done()
    sent = published(mqtt_mock)
    assert sent["ha-unity/hub/status"] == ("online", True)
    assert "alpha/ha/light/alpha_lamp/config" in sent   # retained data re-sent (broker may have lost it)


async def test_custom_status_topic_is_used(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    await setup_hub(hass, world, hub_status_topic="my/hub/up")
    sent = published(mqtt_mock)
    assert sent["my/hub/up"] == ("online", True)
    cfg = json.loads(sent["alpha/ha/light/alpha_lamp/config"][0])
    assert {"topic": "my/hub/up"} in cfg["availability"]


async def test_invalid_status_topic_falls_back_to_the_default(hass, mqtt_mock, world) -> None:
    await setup_hub(hass, world, hub_status_topic="bad/#")
    assert published(mqtt_mock)["ha-unity/hub/status"] == ("online", True)


async def test_stop_leaves_no_timers(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    entry = await setup_hub(hass, world)
    hub = entry.runtime_data
    er.async_get(hass).async_update_entity("light.alpha_lamp", labels=set())
    await hass.async_block_till_done()
    assert hub._debounce is not None          # a resync is pending
    cancelled = []
    real = hub._debounce
    hub._debounce = lambda: (cancelled.append(True), real())[1]   # spy on the cancel callback
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancelled == [True]                 # the pending timer was actually cancelled
    assert hub._debounce is None and hub._state_unsub is None and not hub._unsubs


async def test_removing_the_hub_clears_what_it_published(hass, mqtt_mock, world) -> None:
    cmd_world(hass, world)
    entry = await setup_hub(hass, world)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    mqtt_mock.async_publish.reset_mock()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    sent = published(mqtt_mock)
    assert sent["alpha/ha/light/alpha_lamp/config"] == ("", True)
    assert sent["ha-unity/hub/status"] == ("offline", True)


async def test_a_child_with_an_invalid_unit_name_is_ignored(hass, mqtt_mock, world) -> None:
    from homeassistant.config_entries import ConfigSubentry

    cmd_world(hass, world)
    entry = make_entry(world)
    entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={"unit": "bad/+", "unit_label": world["label_alpha"], "unit_floors": [world["floor_alpha"]]},
            subentry_type="child", title="bad", unique_id=None,
        ),
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(t.startswith("bad") for t in published(mqtt_mock))


async def test_diagnostics(hass, mqtt_mock, world) -> None:
    from custom_components.unity.diagnostics import async_get_config_entry_diagnostics

    cmd_world(hass, world)
    entry = await setup_hub(hass, world)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    by_unit = {c["unit"]: c for c in diag["children"]}
    assert by_unit["alpha"]["exposed"] == ["light.alpha_lamp", "switch.hall"]
    assert by_unit["beta"]["exposed"] == ["light.beta_lamp", "switch.hall"]
    assert diag["hub"]["status_topic"] == "ha-unity/hub/status"
