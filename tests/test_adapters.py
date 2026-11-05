"""Discovery, state and command adapters (ported from the verified blueprints)."""
import json

import pytest

from custom_components.unity import adapters as ad
from custom_components.unity.policy import EntityFacts

STATUS = "ha-unity/hub/status"


def F(entity_id, state="on", area="Kitchen", device=None, dev_name=None, fname=None, **attrs):
    return EntityFacts(
        entity_id, area_id="x", state=state, attributes=attrs, area_name=area,
        device_id=device, device_name=dev_name, friendly_name=fname,
    )


NAMING = ad.NamingContext(area_prefix=r"^L[0-9]+ ", common_area_names=("Entry",))


def disc(f, naming=NAMING):
    return ad.discovery_payload("alpha", f, STATUS, naming)


# ------------------------------ naming ------------------------------------
def test_common_block_and_availability():
    d = disc(F("light.k", "on", "L2 Kitchen", device="dev1", dev_name="Pendant", fname="Pendant"))
    assert d["unique_id"] == "alpha_light_k"
    assert d["device"]["identifiers"] == ["alpha_dev_dev1"]
    assert d["availability"] == [{"topic": STATUS}, {"topic": "alpha/light/k/availability"}]
    assert d["availability_mode"] == "all"


def test_user_set_device_name_is_used_and_entity_name_is_null_when_it_is_the_device():
    f = F("light.k", device="d", dev_name="My Lamp", fname="My Lamp")
    d = disc(f)
    assert d["device"]["name"] == "My Lamp" and d["name"] is None


def test_entity_name_strips_the_device_prefix():
    f = F("sensor.t", device="d", dev_name="Heater", fname="Heater Temperature")
    assert disc(f)["name"] == "Temperature"
    f = F("sensor.t", device="d", dev_name="Heater", fname="Something else")
    assert disc(f)["name"] == "Something else"


def test_entity_without_device_uses_friendly_name_as_device_name():
    f = F("switch.s", fname="Pump")
    d = disc(f)
    assert d["device"]["name"] == "Pump" and d["name"] is None
    assert d["device"]["identifiers"] == ["alpha_dev_switch.s"]


@pytest.mark.parametrize(
    "area,expect",
    [("L2 Kitchen", "Kitchen"), ("Entry", "Entry"), ("Staging", None), (None, None)],
)
def test_suggested_area(area, expect):
    d = disc(F("light.k", area=area, fname="K"))
    assert d["device"].get("suggested_area") == expect


def test_empty_prefix_strips_nothing_and_bad_regex_is_ignored():
    n = ad.NamingContext(area_prefix="", common_area_names=())
    assert "suggested_area" not in disc(F("light.k", area="L2 Kitchen", fname="K"), n)["device"]
    n = ad.NamingContext(area_prefix="(", common_area_names=())
    assert "suggested_area" not in disc(F("light.k", area="L2 Kitchen", fname="K"), n)["device"]


def test_custom_hub_status_topic_is_used():
    d = ad.discovery_payload("alpha", F("light.k", fname="K"), "custom/up", NAMING)
    assert {"topic": "custom/up"} in d["availability"]


# ------------------------------- lights -----------------------------------
def test_light_modes_are_normalised():
    d = disc(F("light.a", supported_color_modes=["rgbw"]))
    assert d["supported_color_modes"] == ["rgb"] and d["brightness"] is True
    d = disc(F("light.a", supported_color_modes=["onoff"]))
    assert d["supported_color_modes"] == ["onoff"]
    d = disc(F("light.a", supported_color_modes=["white"]))
    assert d["supported_color_modes"] == ["brightness"]
    assert disc(F("light.a"))["supported_color_modes"] == ["onoff"]  # no attributes at all


def test_light_color_temp_and_effects():
    d = disc(F("light.a", supported_color_modes=["color_temp", "xy"],
               min_color_temp_kelvin=2200, max_color_temp_kelvin=6500, effect_list=["a", "b"]))
    assert d["color_temp_kelvin"] is True and d["min_kelvin"] == 2200 and d["max_kelvin"] == 6500
    assert d["effect"] is True and d["effect_list"] == ["a", "b"]
    d = disc(F("light.a", supported_color_modes=["color_temp"]))
    assert (d["min_kelvin"], d["max_kelvin"]) == (2000, 6535)


def test_light_state_payload():
    f = F("light.a", "on", brightness=211, color_mode="color_temp", color_temp_kelvin=3000, effect="X")
    assert json.loads(ad.state_payload(f)) == {
        "state": "ON", "brightness": 211, "effect": "X", "color_mode": "color_temp", "color_temp": 3000}
    f = F("light.a", "on", color_mode="rgbw", rgb_color=[1, 2, 3])
    assert json.loads(ad.state_payload(f))["color"] == {"r": 1, "g": 2, "b": 3}
    assert json.loads(ad.state_payload(F("light.a", "on", color_mode="white")))["color_mode"] == "brightness"
    f = F("light.a", "on", color_mode="xy", xy_color=[0.3, 0.3])
    assert json.loads(ad.state_payload(f))["color"] == {"x": 0.3, "y": 0.3}
    f = F("light.a", "on", color_mode="hs", hs_color=[10, 20])
    assert json.loads(ad.state_payload(f))["color"] == {"h": 10, "s": 20}


def call(f, payload):
    return [(c.domain, c.service, dict(c.data)) for c in ad.command_calls(f, payload)]


LIGHT = F(
    "light.a", supported_color_modes=["color_temp", "hs", "xy", "rgb"],
    min_color_temp_kelvin=2000, max_color_temp_kelvin=6500, effect_list=["Rainbow"],
)


def test_light_commands_off_and_on():
    assert call(LIGHT, '{"state": "OFF"}') == [("light", "turn_off", {})]
    assert call(LIGHT, '{"state": "ON"}') == [("light", "turn_on", {})]
    assert call(LIGHT, '{"state": "on"}') == [("light", "turn_on", {})]


def test_light_brightness_and_colour_temp_are_clamped():
    assert call(LIGHT, '{"state":"ON","brightness":9999}')[0][2] == {"brightness": 255}
    assert call(LIGHT, '{"state":"ON","brightness":-5}')[0][2] == {"brightness": 1}
    assert call(LIGHT, '{"state":"ON","brightness":"junk"}')[0][2] == {"brightness": 255}
    assert call(LIGHT, '{"state":"ON","color_temp":100}')[0][2] == {"color_temp_kelvin": 2000}
    assert call(LIGHT, '{"state":"ON","color_temp":99999}')[0][2] == {"color_temp_kelvin": 6500}


def test_light_colour_models_are_clamped_and_gated_by_supported_modes():
    assert call(LIGHT, '{"state":"ON","color":{"h":999,"s":-4}}')[0][2] == {"hs_color": [360.0, 0.0]}
    assert call(LIGHT, '{"state":"ON","color":{"x":2,"y":-1}}')[0][2] == {"xy_color": [1.0, 0.0]}
    assert call(LIGHT, '{"state":"ON","color":{"r":300,"g":-1,"b":7}}')[0][2] == {"rgb_color": [255, 0, 7]}
    only_ct = F("light.b", supported_color_modes=["color_temp"])
    assert call(only_ct, '{"state":"ON","color":{"r":1,"g":2,"b":3}}')[0][2] == {}
    assert call(only_ct, '{"state":"ON","color":{"h":1,"s":2}}')[0][2] == {}
    no_ct = F("light.c", supported_color_modes=["brightness"])
    assert call(no_ct, '{"state":"ON","color_temp":3000}')[0][2] == {}
    assert call(F("light.d", supported_color_modes=["rgbw"]), '{"state":"ON","color":{"r":1,"g":2,"b":3}}')[0][2] == {
        "rgb_color": [1, 2, 3]}


def test_light_effect_must_be_advertised():
    assert call(LIGHT, '{"state":"ON","effect":"Rainbow"}')[0][2] == {"effect": "Rainbow"}
    assert call(LIGHT, '{"state":"ON","effect":"Evil"}')[0][2] == {}


@pytest.mark.parametrize("payload", ["", "ON", "not json", "[1]", '{"state":"maybe"}', "null", '{"brightness":10}'])
def test_unparseable_light_commands_do_nothing(payload):
    assert call(LIGHT, payload) == []


# ------------------------- switch / scene / sensors -----------------------
def test_switch_and_scene():
    s = F("switch.s")
    assert call(s, "ON") == [("switch", "turn_on", {})]
    assert call(s, "off") == [("switch", "turn_off", {})]
    assert call(s, "TOGGLE") == []
    assert call(F("scene.m"), "ON") == [("scene", "turn_on", {})]
    assert call(F("scene.m"), "OFF") == []


def test_sensors_have_no_command_path():
    assert call(F("sensor.t"), "ON") == []
    assert call(F("binary_sensor.t"), "ON") == []
    assert call(F("lock.front"), '{"state":"ON"}') == []   # not a command domain


def test_sensor_discovery_and_state():
    d = disc(F("sensor.t", "21.5", device_class="temperature", unit_of_measurement="°C"))
    assert d["device_class"] == "temperature" and d["unit_of_measurement"] == "°C"
    assert "command_topic" not in d
    assert ad.state_payload(F("sensor.t", "21.5")) == "21.5"
    assert ad.state_payload(F("binary_sensor.b", "on")) == "ON"
    assert ad.state_payload(F("switch.s", "off")) == "OFF"


# ------------------------------- availability -----------------------------
@pytest.mark.parametrize(
    "eid,state,expect",
    [("light.a", "on", "online"), ("light.a", "unavailable", "offline"), ("light.a", "unknown", "offline"),
     ("scene.s", "unknown", "online"), ("scene.s", "unavailable", "offline")],
)
def test_availability(eid, state, expect):
    assert ad.availability_payload(F(eid, state)) == expect


def test_no_state_for_unavailable_entities_or_scenes():
    assert ad.state_payload(F("light.a", "unavailable")) is None
    assert ad.state_payload(F("scene.s", "unknown")) is None


# ---------------------------------- fans ----------------------------------
FAN = F("fan.f", supported_features=1 | 2 | 4 | 8, preset_modes=["auto", "sleep"])


def test_fan_discovery_follows_features():
    d = disc(FAN)
    assert d["speed_range_max"] == 100 and d["preset_modes"] == ["auto", "sleep"]
    assert "oscillation_command_topic" in d and "direction_command_topic" in d
    basic = disc(F("fan.b", supported_features=0))
    assert not any(k.startswith(("percentage", "preset", "oscillation", "direction")) for k in basic)
    assert "preset_modes" not in disc(F("fan.n", supported_features=8))   # feature but no list


def test_fan_state_payload():
    f = F("fan.f", "on", percentage=50, preset_mode="auto", oscillating=False, direction="forward")
    assert json.loads(ad.state_payload(f)) == {
        "state": "ON", "percentage": 50, "preset_mode": "auto", "oscillating": False, "direction": "forward"}
    assert json.loads(ad.state_payload(F("fan.f", "off"))) == {"state": "OFF"}


def test_fan_commands():
    assert call(FAN, '{"state":"ON"}') == [("fan", "turn_on", {})]
    assert call(FAN, '{"state":"OFF"}') == [("fan", "turn_off", {})]
    assert call(FAN, '{"percentage": 150}') == [("fan", "set_percentage", {"percentage": 100})]
    assert call(FAN, '{"percentage": -3}') == [("fan", "set_percentage", {"percentage": 0})]
    assert call(FAN, '{"percentage": "x"}') == []
    assert call(FAN, '{"preset_mode":"auto"}') == [("fan", "set_preset_mode", {"preset_mode": "auto"})]
    assert call(FAN, '{"preset_mode":"evil"}') == []
    assert call(FAN, '{"oscillating": true}') == [("fan", "oscillate", {"oscillating": True})]
    assert call(FAN, '{"oscillating": "yes"}') == []
    assert call(FAN, '{"direction":"reverse"}') == [("fan", "set_direction", {"direction": "reverse"})]
    assert call(FAN, '{"direction":"sideways"}') == []


def test_fan_features_gate_commands():
    basic = F("fan.b", supported_features=0, preset_modes=["auto"])
    assert call(basic, '{"percentage": 50}') == []
    assert call(basic, '{"preset_mode":"auto"}') == []
    assert call(basic, '{"oscillating": true}') == []
    assert call(basic, '{"direction":"forward"}') == []
    assert call(basic, '{"state":"ON"}') == [("fan", "turn_on", {})]


def test_fan_off_ignores_other_keys():
    assert call(FAN, '{"state":"OFF","percentage":50}') == [("fan", "turn_off", {})]


# --------------------------------- covers ---------------------------------
COVER = F("cover.c", supported_features=1 | 2 | 4 | 8, device_class="blind")


def test_cover_discovery_hides_unsupported_buttons():
    d = disc(COVER)
    assert d["payload_open"] and d["payload_close"] and d["payload_stop"] and d["device_class"] == "blind"
    assert d["position_open"] == 100
    d = disc(F("cover.o", supported_features=1 | 2))
    assert d["payload_stop"] is None and "position_topic" not in d and "device_class" not in d


def test_cover_state_payload():
    assert json.loads(ad.state_payload(F("cover.c", "open", current_position=40))) == {"state": "open", "position": 40}
    assert json.loads(ad.state_payload(F("cover.c", "closed"))) == {"state": "closed"}


def test_cover_commands_and_clamps():
    assert call(COVER, '{"command":"open"}') == [("cover", "open_cover", {})]
    assert call(COVER, '{"command":"close"}') == [("cover", "close_cover", {})]
    assert call(COVER, '{"command":"stop"}') == [("cover", "stop_cover", {})]
    assert call(COVER, '{"command":"explode"}') == []
    assert call(COVER, '{"position": 250}') == [("cover", "set_cover_position", {"position": 100})]
    assert call(COVER, '{"position": -5}') == [("cover", "set_cover_position", {"position": 0})]
    assert call(COVER, '{"position": "x"}') == []
    openonly = F("cover.o", supported_features=1)
    assert call(openonly, '{"command":"close"}') == []
    assert call(openonly, '{"position": 50}') == []


def test_services_are_never_taken_from_the_payload():
    evil = '{"state":"ON","service":"lock.unlock","domain":"lock","entity_id":"lock.front"}'
    for f in (LIGHT, FAN):
        assert all(c.domain in ("light", "fan") and c.entity_id == f.entity_id for c in ad.command_calls(f, evil))
