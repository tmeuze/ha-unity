"""The synthetic thermostat (pure logic). Fixtures mirror a real RoomMind room entity."""
import pytest

from custom_components.unity import climate as cl
from custom_components.unity.policy import (
    ChildPolicy, EntityFacts, HubPolicy, is_climate_granted, is_exposed,
)
from custom_components.unity import topics as t

# Attributes as read from a live RoomMind room entity (climate.roommind_<area>).
ROOMMIND = {
    "hvac_modes": ["off", "heat", "cool", "heat_cool"], "min_temp": 41, "max_temp": 95,
    "target_temp_step": 0.5, "preset_modes": ["none", "comfort", "eco", "manual"],
    "current_temperature": 75, "temperature": 71, "current_humidity": 54, "hvac_action": "idle",
    "preset_mode": "manual", "override_until": None, "temperature_unit": "°F",
    "friendly_name": "Kitchen", "supported_features": 401,
}


def F(state="heat", **over):
    attrs = {**ROOMMIND, **over}
    return EntityFacts("climate.roommind_kitchen", area_id="a", state=state, attributes=attrs,
                       friendly_name=attrs.get("friendly_name"))


GRANT = cl.ClimateGrant("alpha", "kitchen", "climate.roommind_kitchen", 64, 80,
                        expire_after_minutes=30, expire_mode=cl.EXPIRE_PRESET_NONE)


# ------------------------------------------------------------------ discovery
def test_discovery_payload():
    d = cl.discovery_payload(GRANT, F(), "ha-unity/hub/status", "F")
    assert d["unique_id"] == "alpha_climate_kitchen"
    assert d["name"] == "Kitchen"
    assert (d["min_temp"], d["max_temp"], d["temperature_unit"]) == (64, 80, "F")
    assert d["modes"] == ["auto"]                       # the guest cannot change mode
    assert d["temperature_command_topic"] == "alpha/climate/kitchen/target/set"
    assert d["temperature_state_topic"] == "alpha/climate/kitchen/target/state"
    assert d["current_temperature_topic"] == "alpha/climate/kitchen/current"
    assert d["action_topic"] == "alpha/climate/kitchen/action"
    assert d["availability"] == [{"topic": "ha-unity/hub/status"}, {"topic": "alpha/climate/kitchen/availability"}]
    assert d["availability_mode"] == "all"


def test_no_action_topic_when_the_entity_has_no_hvac_action():
    attrs = {k: v for k, v in ROOMMIND.items() if k != "hvac_action"}
    f = EntityFacts("climate.x", state="heat", attributes=attrs, friendly_name="X")
    assert "action_topic" not in cl.discovery_payload(GRANT, f, "s", "C")


# --------------------------------------------------------------------- state
def test_state_payloads_from_attributes():
    out = cl.state_payloads(GRANT, F())
    assert out == {
        "alpha/climate/kitchen/availability": "online",
        "alpha/climate/kitchen/current": "75",
        "alpha/climate/kitchen/target/state": "71",
        "alpha/climate/kitchen/action": "idle",
    }


def test_fractional_values_and_unknown_action():
    out = cl.state_payloads(GRANT, F(current_temperature=72.5, temperature=70.5, hvac_action="weird"))
    assert out["alpha/climate/kitchen/current"] == "72.5"
    assert out["alpha/climate/kitchen/target/state"] == "70.5"
    assert "alpha/climate/kitchen/action" not in out


def test_missing_values_are_not_published():
    out = cl.state_payloads(GRANT, F(current_temperature=None, temperature="unknown"))
    assert set(out) == {"alpha/climate/kitchen/availability", "alpha/climate/kitchen/action"}


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_unavailable_entity_publishes_only_offline(state):
    assert cl.state_payloads(GRANT, F(state=state)) == {"alpha/climate/kitchen/availability": "offline"}


def test_optional_sensors_override_the_attributes():
    g = cl.ClimateGrant("alpha", "kitchen", "climate.x", 64, 80, current_sensor="sensor.t", target_sensor="sensor.s")
    out = cl.state_payloads(g, F(), current_sensor_state="68.4", target_sensor_state="69")
    assert out["alpha/climate/kitchen/current"] == "68.4"
    assert out["alpha/climate/kitchen/target/state"] == "69"
    out = cl.state_payloads(g, F(), current_sensor_state="unavailable", target_sensor_state=None)
    assert "alpha/climate/kitchen/current" not in out       # a sensor is the source: no silent fallback
    assert g.watched() == {"climate.x", "sensor.t", "sensor.s"}


# ------------------------------------------------------------------- commands
def call(payload, facts=None, grant=GRANT):
    c = cl.command_call(grant, facts or F(), payload)
    return None if c is None else (c.domain, c.service, c.entity_id, dict(c.data))


def test_valid_command_calls_set_temperature_only():
    assert call("72") == ("climate", "set_temperature", "climate.roommind_kitchen", {"temperature": 72.0})
    assert call(" 72.5 ")[3] == {"temperature": 72.5}


@pytest.mark.parametrize("payload,expect", [("50", 64.0), ("200", 80.0), ("-5", 64.0), ("64", 64.0), ("80", 80.0)])
def test_value_is_clamped_to_the_grant_range(payload, expect):
    assert call(payload)[3] == {"temperature": expect}


def test_value_is_also_clamped_to_the_entitys_own_range():
    assert call("78", F(max_temp=75))[3] == {"temperature": 75.0}
    assert call("66", F(min_temp=70))[3] == {"temperature": 70.0}
    assert call("72", F(min_temp=90)) is None          # empty intersection: refuse


@pytest.mark.parametrize("payload", ["", "abc", "nan", "inf", "-inf", "1e999", "true", None, 72, b"72", '{"temperature": 72}'])
def test_unparseable_commands_do_nothing(payload):
    assert call(payload) is None


def test_commands_need_an_available_entity_that_accepts_a_target():
    assert call("72", F(state="unavailable")) is None
    assert call("72", F(supported_features=0)) is None             # cannot take a target
    assert call("72", EntityFacts("light.x", state="on", attributes={"supported_features": 1})) is None


def test_payload_cannot_choose_the_service_or_entity():
    evil = '{"service":"climate.turn_off","entity_id":"climate.other","hvac_mode":"off"}'
    assert call(evil) is None
    c = cl.command_call(GRANT, F(), "72")
    assert c.service == "set_temperature" and c.entity_id == GRANT.entity_id and set(c.data) == {"temperature"}


# --------------------------------------------------------------------- expiry
def test_expiry_selects_the_none_preset():
    c = cl.expiry_call(GRANT, F())
    assert (c.domain, c.service, dict(c.data)) == ("climate", "set_preset_mode", {"preset_mode": "none"})


def test_expiry_is_a_no_op_when_it_cannot_apply():
    assert cl.expiry_call(cl.ClimateGrant("a", "b", "climate.x", 1, 2), F()) is None          # mode none
    no_preset = F(preset_modes=["comfort", "eco"])
    assert cl.expiry_call(GRANT, no_preset) is None                                            # no "none" preset
    assert cl.expiry_call(GRANT, F(supported_features=1)) is None                              # no preset feature
    assert cl.expiry_call(GRANT, F(state="unavailable")) is None


# ----------------------------------------------------------- policy and topics
HUB = HubPolicy(common_label="shared", common_floors=("fs",))
CHILD = ChildPolicy("alpha", unit_label="alpha", unit_floors=("fa",))
AREAS = {"fa": {"a"}, "fs": {"s"}}


def facts(entity_id="climate.x", area="a", labels=("alpha",)):
    return EntityFacts(entity_id, area_id=area, labels=frozenset(labels), state="heat")


def test_climate_is_granted_only_by_label_and_floor():
    assert is_climate_granted(facts(), CHILD, HUB, AREAS)
    assert is_climate_granted(facts(area="s", labels=("shared",)), CHILD, HUB, AREAS)
    assert not is_climate_granted(facts(labels=()), CHILD, HUB, AREAS)                       # unlabelled
    assert not is_climate_granted(facts(area=None), CHILD, HUB, AREAS)                       # no area
    assert not is_climate_granted(facts(area="s"), CHILD, HUB, AREAS)                        # unit label on common floor
    assert not is_climate_granted(facts(labels=("shared",)), CHILD, HUB, AREAS)              # common label on unit floor
    assert not is_climate_granted(facts("light.x"), CHILD, HUB, AREAS)                       # wrong domain
    assert not is_climate_granted(facts(labels=("beta",)), CHILD, HUB, AREAS)


def test_climate_is_not_a_generic_exposed_domain():
    """Labelling a thermostat must not expose it as a plain entity; the grant is explicit."""
    assert not is_exposed(facts(), CHILD, HUB, AREAS)


def test_climate_topics():
    assert t.climate_base("alpha", "kitchen") == "alpha/climate/kitchen"
    assert t.climate_discovery_topic("alpha", "kitchen") == "alpha/ha/climate/kitchen/config"
    assert t.climate_command_filter("alpha") == "alpha/climate/+/target/set"
    assert t.parse_climate_command_topic("alpha/climate/kitchen/target/set", "alpha") == "kitchen"
    for bad in ["beta/climate/kitchen/target/set", "alpha/climate/kitchen/target/state", "alpha/climate/kitchen/set",
                "alpha/light/kitchen/target/set", "alpha/climate/Kit/target/set", "alpha/climate/+/target/set",
                "alpha/climate/kitchen/target/set/x", "alpha/climate/kitchen/mode/set"]:
        assert t.parse_climate_command_topic(bad, "alpha") is None, bad
    with pytest.raises(ValueError):
        t.climate_base("alpha", "Bad/Slug")
    assert not t.valid_object("a/b") and not t.valid_object("A") and t.valid_object("entry_hall")
