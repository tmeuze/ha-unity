"""The whitelist policy. Ported from tests/policy_model.py (the blueprint model)."""
import pytest

from custom_components.unity.policy import (
    ChildPolicy, EntityFacts, HubPolicy, exposed_set, is_exposed,
)

FLOOR_AREAS = {
    "beta": ["beta", "l1_living"],
    "alpha": ["alpha", "l2_kitchen"],
    "shared": ["entry", "outdoors"],
    "meta": ["people", "roaming", "services", "staging"],
}
HUB = HubPolicy(common_label="shared", common_floors=("shared",))
ALPHA = ChildPolicy("alpha", unit_label="alpha", unit_floors=("alpha",))
BETA = ChildPolicy("beta", unit_label="beta", unit_floors=("beta",))


def E(entity_id, area, labels, state="on", **attrs):
    return EntityFacts(entity_id, area_id=area, labels=frozenset(labels), state=state, attributes=attrs)


ENTITIES = [
    E("light.beta_lamp", "l1_living", {"beta"}),
    E("light.alpha_lamp", "alpha", {"alpha"}),
    E("light.kitchen_pendant", "l2_kitchen", {"alpha"}),
    E("light.front_door", "entry", {"shared"}),
    E("fan.alpha_fan", "alpha", {"alpha"}),
    E("scene.alpha_movie", "alpha", {"alpha"}, state="unknown"),
    E("cover.alpha_blind", "alpha", {"alpha"}, device_class="blind"),
    E("cover.alpha_nodc", "alpha", {"alpha"}),
    E("cover.alpha_garage", "alpha", {"alpha"}, device_class="garage"),
    E("cover.alpha_gate", "alpha", {"alpha"}, device_class="gate"),
    E("cover.alpha_unavail", "alpha", {"alpha"}, state="unavailable", device_class="blind"),
    E("cover.garage", "entry", {"shared"}, device_class="garage"),
    E("switch.entry_relay", "entry", {"shared"}),
    E("sensor.beta_temp", "beta", {"beta"}, state="21"),
    E("light.unlabelled", "alpha", set()),
    E("light.no_area", None, {"alpha"}),
    E("light.staging_test", "staging", {"alpha"}),
    E("switch.services_pump", "services", {"shared"}),
    E("lock.front_door", "entry", {"shared"}),
    E("camera.front", "entry", {"shared"}),
    E("light.old_live_label", "alpha", {"live"}),
    E("light.shared_label_wrong_floor", "alpha", {"shared"}),
    E("light.alpha_label_on_commons", "entry", {"alpha"}),
    E("light.beta_label_on_alpha", "alpha", {"beta"}),
]


def seen(child):
    return set(exposed_set(ENTITIES, child, HUB, FLOOR_AREAS))


def test_each_child_sees_its_own_entities():
    assert {"light.alpha_lamp", "light.kitchen_pendant"} <= seen(ALPHA)
    assert {"light.beta_lamp", "sensor.beta_temp"} <= seen(BETA)


def test_common_label_on_common_floor_reaches_both():
    assert {"light.front_door", "switch.entry_relay"} <= seen(ALPHA) & seen(BETA)


def test_no_cross_child_leakage():
    assert "light.beta_lamp" not in seen(ALPHA)
    assert "light.alpha_lamp" not in seen(BETA)


@pytest.mark.parametrize(
    "entity",
    [
        "light.beta_label_on_alpha",      # another child's label on this child's floor
        "light.shared_label_wrong_floor",  # common label on a unit floor
        "light.alpha_label_on_commons",   # unit label on a common floor (strict pairs)
        "light.unlabelled",
        "light.old_live_label",
        "light.no_area",
        "light.staging_test",             # floor not configured
        "switch.services_pump",
        "lock.front_door",                # domain not on the list
        "camera.front",
    ],
)
def test_fail_closed_cases(entity):
    assert entity not in seen(ALPHA) | seen(BETA)


def test_covers():
    s = seen(ALPHA)
    assert {"cover.alpha_blind", "cover.alpha_nodc"} <= s
    assert not ({"cover.alpha_garage", "cover.alpha_gate", "cover.garage"} & (s | seen(BETA)))
    assert "cover.alpha_unavail" not in s


def test_fans_and_scenes_are_exposed():
    assert {"fan.alpha_fan", "scene.alpha_movie"} <= seen(ALPHA)


def test_blocked_cover_classes_are_configurable():
    hub = HubPolicy(common_label="shared", common_floors=("shared",), blocked_cover_classes=frozenset({"blind"}))
    got = set(exposed_set(ENTITIES, ALPHA, hub, FLOOR_AREAS))
    assert "cover.alpha_blind" not in got
    assert "cover.alpha_garage" in got   # no longer blocked because the user chose


@pytest.mark.parametrize(
    "child,hub",
    [
        (ChildPolicy("x"), HubPolicy()),                                   # nothing configured
        (ChildPolicy("x", unit_label="", unit_floors=("alpha",)), HubPolicy(common_label="")),
        (ChildPolicy("x", unit_label="alpha", unit_floors=()), HubPolicy()),  # label but no floor
        (ChildPolicy("x", unit_label="alpha", unit_floors=("missing",)), HubPolicy()),
    ],
)
def test_missing_or_empty_configuration_exposes_nothing(child, hub):
    assert exposed_set(ENTITIES, child, hub, FLOOR_AREAS) == {}


def test_is_exposed_matches_exposed_set():
    for e in ENTITIES:
        assert is_exposed(e, ALPHA, HUB, FLOOR_AREAS) == (e.entity_id in seen(ALPHA))
