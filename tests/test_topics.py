import pytest

from custom_components.unity import topics as t


@pytest.mark.parametrize("unit", ["alpha", "a1", "unit-2", "unit_3"])
def test_valid_units(unit):
    assert t.valid_unit(unit)


@pytest.mark.parametrize(
    "unit", ["", "Alpha", "a/b", "a+b", "#", "+", "a b", "-x", "homeassistant", "ha-unity", "x" * 33, "a#"]
)
def test_invalid_units_are_rejected(unit):
    assert not t.valid_unit(unit)


@pytest.mark.parametrize("topic", ["ha-unity/hub/status", "a/b", "status"])
def test_valid_hub_topics(topic):
    assert t.valid_hub_status_topic(topic)


@pytest.mark.parametrize("topic", ["", "a/+", "a/#", "/a", "a//b", "a/"])
def test_invalid_hub_topics(topic):
    assert not t.valid_hub_status_topic(topic)


def test_topic_builders():
    assert t.entity_base("alpha", "light.kitchen") == "alpha/light/kitchen"
    assert t.discovery_topic("alpha", "fan.tower") == "alpha/ha/fan/tower/config"
    assert t.command_filter("alpha") == "alpha/+/+/set"
    with pytest.raises(ValueError):
        t.entity_base("alpha", "nodot")


def test_parse_command_topic():
    assert t.parse_command_topic("alpha/light/kitchen/set", "alpha") == "light.kitchen"
    # the entity comes from the topic; anything off-pattern is rejected
    assert t.parse_command_topic("beta/light/kitchen/set", "alpha") is None   # another unit
    assert t.parse_command_topic("alpha/light/kitchen/state", "alpha") is None
    assert t.parse_command_topic("alpha/light/set", "alpha") is None
    assert t.parse_command_topic("alpha/ha/light/kitchen/set", "alpha") is None
    assert t.parse_command_topic("alpha/light/Kitchen/set", "alpha") is None
    assert t.parse_command_topic("alpha/light/a.b/set", "alpha") is None
    assert t.parse_command_topic("alpha/light/+/set", "alpha") is None
