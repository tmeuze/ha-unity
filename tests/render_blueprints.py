#!/usr/bin/env python3
"""Render the REAL template strings from the blueprints with Jinja2 against
stubbed Home Assistant functions, and compare with tests/policy_model.py.

Needs PyYAML and Jinja2:  python tests/render_blueprints.py
(any venv with PyYAML and Jinja2 installed)

What this proves: the template syntax is valid Jinja, the logic gives the
intended result, and the three blueprints agree with each other and with the
Python model. What it does NOT prove: that HA's own functions (floor_areas,
label_entities, area_id, state_attr) behave like the stubs below, that HA's
filters (to_json, combine, float(none)) match, or anything about EMQX. Those
are covered by docs/policy-test-plan.md on a real instance.
"""
import ast
import json
import re
import sys
from pathlib import Path

import jinja2
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy_model as pm  # noqa: E402

BP = Path(__file__).resolve().parent.parent / "blueprints" / "automation" / "ha_unity"


class Inp:
    def __init__(self, name):
        self.name = name


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!input", lambda l, n: Inp(l.construct_scalar(n)))


def load(name):
    return yaml.load((BP / name).read_text(), Loader=Loader)


# ---- stubs for HA template functions --------------------------------------
class State:
    def __init__(self, entity_id, state="on", **attrs):
        self.entity_id, self.state, self.attributes = entity_id, state, attrs
        self.domain = entity_id.split(".")[0]


def env_for(world_states, attrs=None):
    attrs = attrs or {}
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)

    def label_entities(label):
        return [e for e, (_a, labels) in pm.ENTITIES.items() if label in labels]

    class States(list):
        def __call__(self, eid):
            for s in self:
                if s.entity_id == eid:
                    return s.state
            return "unknown"

    states = States(world_states)
    env.globals.update(
        floor_areas=lambda f: pm.FLOOR_AREAS.get(f, []),
        label_entities=label_entities,
        area_id=lambda e: pm.ENTITIES.get(e, (None, set()))[0],
        state_attr=lambda e, a: attrs.get(e, {}).get(a),
        states=states,
    )
    def area_name(x):
        if x in pm.ENTITIES:
            x = pm.ENTITIES[x][0]
        return pm.AREA_NAMES.get(x)

    env.globals.update(
        area_name=area_name,
        device_id=lambda e: ("dev_" + e.split(".")[1]) if e in pm.ENTITIES else None,
        device_attr=lambda d, a: (USER_DEVICE_NAMES.get(d) if a == "name_by_user"
                                  else d.replace("dev_", "").replace("_", " ").title()),
    )
    env.tests["match"] = lambda v, pat: re.match(pat, str(v)) is not None
    env.filters["regex_replace"] = lambda v, pat, rep="": re.sub(pat, rep, str(v))
    env.filters["to_json"] = lambda v: json.dumps(v)
    env.filters["combine"] = lambda a, b: {**a, **b}
    return env


# Devices the user renamed on the hub (name_by_user); others have none.
USER_DEVICE_NAMES = {"dev_kitchen_pendant": "Pendant (renamed)"}


def render(env, tpl, ctx):
    """Render like HA's native templates: parse literals out of the result."""
    if not isinstance(tpl, str):
        return tpl
    out = env.from_string(tpl).render(**ctx).strip()
    if out == "":
        return ""
    try:
        return ast.literal_eval(out)
    except (ValueError, SyntaxError):
        return out


def render_data(env, data, ctx):
    if isinstance(data, dict):
        return {k: render_data(env, v, ctx) for k, v in data.items()}
    return render(env, data, ctx)


def run_vars(env, variables, inputs, ctx):
    ctx = dict(ctx)
    for k, v in variables.items():
        v = inputs[v.name] if isinstance(v, Inp) else v
        ctx[k] = render(env, v, ctx)
    return ctx


FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


WORLD = [State(e, "unavailable" if e in pm.UNAVAILABLE else ("unknown" if e.startswith("scene.") else "on")) for e in pm.ENTITIES]
ATTRS = {
    "light.alpha_color": {"friendly_name": "Alpha Color", "supported_color_modes": ["color_temp", "xy"],
                          "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6535},
    "light.alpha_rgb": {"friendly_name": "Alpha Rgb", "supported_color_modes": ["rgbw"]},
    "light.alpha_hs": {"friendly_name": "Alpha Hs", "supported_color_modes": ["hs"]},
    "light.alpha_white": {"friendly_name": "Alpha White", "supported_color_modes": ["white"]},
    "light.alpha_onoff": {"friendly_name": "Alpha Onoff", "supported_color_modes": ["onoff"]},
    "light.alpha_noattr": {"friendly_name": "Alpha Noattr"},
    "light.alpha_effects": {"friendly_name": "Alpha Effects", "supported_color_modes": ["brightness"],
                            "effect_list": ["rainbow", "pulse"]},
    "fan.alpha_fan": {"friendly_name": "Alpha Fan", "supported_features": 15, "preset_modes": ["auto", "sleep"]},
    "fan.alpha_basic": {"friendly_name": "Alpha Basic", "supported_features": 48},
    "scene.alpha_movie": {"friendly_name": "Alpha Movie"},
    "cover.alpha_blind": {"friendly_name": "Alpha Blind", "supported_features": 15},
    "cover.alpha_nodc": {"friendly_name": "Alpha Nodc", "supported_features": 3},
    "light.kitchen_pendant": {"friendly_name": "Kitchen Pendant", "brightness": 10,
                              "supported_color_modes": ["brightness"]},
    "light.front_door": {"friendly_name": "Front Door", "brightness": 255,
                         "supported_color_modes": ["brightness"]},
    "light.alpha_lamp": {"friendly_name": "Alpha Lamp", "brightness": 128,
                          "supported_color_modes": ["brightness"]},
    "switch.entry_relay": {"friendly_name": "Entry Relay"},
    "sensor.beta_temp": {"friendly_name": "Beta Temp Temperature", "device_class": "temperature",
                         "unit_of_measurement": "°F"},
}

for _e, _c in pm.COVER_CLASS.items():
    ATTRS.setdefault(_e, {})["device_class"] = _c


def test_syntax_and_exposed():
    for name in ["ha_unity_publish_discovery.yaml", "ha_unity_publish_state.yaml",
                 "ha_unity_command_bridge.yaml", "ha_unity_climate.yaml"]:
        bp = load(name)
        check(f"{name}: parses and has blueprint+actions",
              "blueprint" in bp and "actions" in bp)
    env = env_for(WORLD, ATTRS)
    for unit in ["alpha", "beta"]:
        inputs = pm.inputs(unit)
        # discovery builds `exposed` inside an actions variables step
        disc = load("ha_unity_publish_discovery.yaml")
        step = next(a for a in disc["actions"] if "variables" in a)
        ctx = run_vars(env, disc["variables"], inputs, {})
        ctx = run_vars(env, step["variables"], inputs, ctx)
        e_disc = set(ctx["exposed"])
        state_bp = load("ha_unity_publish_state.yaml")
        e_state = set(run_vars(env, state_bp["variables"], inputs, {})["exposed"])
        cmd_bp = load("ha_unity_command_bridge.yaml")
        # command variables reference `trigger`; give a dummy topic
        e_cmd = set(run_vars(env, cmd_bp["variables"], inputs,
                             {"trigger": {"topic": f"{unit}/light/x/set", "payload": "ON"}})["exposed"])
        # the command bridge only handles light and switch
        model = pm.exposed(unit)
        check(f"{unit}: discovery exposed == model", e_disc == model)
        check(f"{unit}: state exposed == model", e_state == model)
        check(f"{unit}: command exposed == model restricted to controllable domains",
              e_cmd == {e for e in model if e.split('.')[0] in ('light', 'switch', 'fan', 'scene', 'cover')})
        check(f"{unit}: stale list is every non-exposed light/switch/sensor",
              set(ctx["stale"]) == {s.entity_id for s in WORLD
                                    if s.domain in pm.DOMAINS} - model)


def bridge(unit, topic, payload):
    """Run the command-bridge variables/conditions/choose; return service calls."""
    env = env_for(WORLD, ATTRS)
    bp = load("ha_unity_command_bridge.yaml")
    inputs = pm.inputs(unit)
    trig = {"topic": topic, "payload": payload}
    try:
        trig["payload_json"] = json.loads(payload)
    except ValueError:
        pass
    ctx = run_vars(env, bp["variables"], inputs, {"trigger": trig})
    for c in bp["conditions"]:
        if render(env, c["value_template"], ctx) is not True:
            return []
    for branch in bp["actions"][0]["choose"]:
        if render(env, branch["conditions"], ctx) is True:
            seq = branch["sequence"][0]
            data = seq.get("data")
            data = render_data(env, data, ctx) if data is not None else {}
            return [(seq["action"], render(env, seq["target"]["entity_id"], ctx), data)]
    return []


def test_bridge():
    L = "alpha/light/alpha_lamp/set"
    check("bridge: ON -> light.turn_on",
          bridge("alpha", L, '{"state":"ON"}')[0][:2] == ("light.turn_on", "light.alpha_lamp"))
    check("bridge: OFF -> light.turn_off",
          bridge("alpha", L, '{"state":"OFF"}')[0][0] == "light.turn_off")
    r = bridge("alpha", L, '{"state":"ON","brightness":9999}')
    check("bridge: brightness clamped to 255", r and r[0][2] == {"brightness": 255})
    r = bridge("alpha", L, '{"state":"ON","brightness":-5}')
    check("bridge: brightness clamped to 1", r and r[0][2] == {"brightness": 1})
    check("bridge: non-JSON payload ignored", bridge("alpha", L, "garbage") == [])
    check("bridge: unknown state ignored", bridge("alpha", L, '{"state":"TOGGLE"}') == [])
    check("bridge: other unit's entity ignored",
          bridge("alpha", "alpha/light/beta_lamp/set", '{"state":"ON"}') == [])
    check("bridge: unlabelled entity ignored",
          bridge("alpha", "alpha/light/unlabelled/set", '{"state":"ON"}') == [])
    check("bridge: lock ignored", bridge("alpha", "alpha/lock/front_door/set", "UNLOCK") == [])
    check("bridge: camera/cover ignored",
          bridge("alpha", "alpha/cover/garage/set", "ON") == [])
    check("bridge: Meta-floor entity ignored",
          bridge("alpha", "alpha/light/staging_test/set", '{"state":"ON"}') == [])
    check("bridge: common-space light works from beta",
          bridge("beta", "beta/light/front_door/set", '{"state":"ON"}')[0][1] == "light.front_door")
    check("bridge: common-space switch ON",
          bridge("alpha", "alpha/switch/entry_relay/set", "ON")[0][:2] == ("switch.turn_on", "switch.entry_relay"))
    check("bridge: wrong prefix ignored",
          bridge("alpha", "beta/light/beta_lamp/set", '{"state":"ON"}') == [])
    check("bridge: 5-level topic ignored",
          bridge("alpha", "alpha/light/alpha_lamp/x/set", '{"state":"ON"}') == [])
    check("bridge: alpha/ha/light/set not treated as an entity",
          bridge("alpha", "alpha/ha/light/set", '{"state":"ON"}') == [])
    check("bridge: JSON payload for a switch (needs ON/OFF word) ignored",
          bridge("alpha", "alpha/switch/entry_relay/set", '{"state":"ON"}') == [])


def test_payloads():
    env = env_for(WORLD, ATTRS)
    disc = load("ha_unity_publish_discovery.yaml")
    inputs = pm.inputs("alpha")
    loop = [a for a in disc["actions"] if "repeat" in a][1]["repeat"]["sequence"]
    var_step = loop[0]["variables"]
    choose = loop[1]["choose"]
    for eid, domain in [("light.alpha_lamp", "light"), ("switch.entry_relay", "switch"),
                        ("sensor.beta_temp", "sensor")]:
        ctx = run_vars(env, var_step, inputs, {"repeat": {"item": eid}, "unit": "alpha",
                                                "area_prefix": "^L[0-9]+ ", "hub_status_topic": "ha-unity/hub/status",
                                                "common_area_names": ["Entry", "Outdoors"]})
        branch = next(b for b in choose if domain in b["conditions"])
        data = branch["sequence"][0]["data"]
        topic = render(env, data["topic"], ctx)
        payload = json.loads(render(env, data["payload"], ctx) if isinstance(
            render(env, data["payload"], ctx), str) else json.dumps(render(env, data["payload"], ctx)))
        want = {"light": None, "switch": None, "sensor": "Temperature"}[domain]
        check(f"discovery {domain}: entity name is {want!r} (not doubled)", payload.get("name") == want)
        check(f"discovery {domain}: topic under child prefix",
              topic.startswith(f"alpha/ha/{domain}/"))
        check(f"discovery {domain}: valid JSON with unique_id and state_topic",
              payload["unique_id"].startswith("alpha_") and payload["state_topic"].startswith("alpha/"))
        check(f"discovery {domain}: availability requires ha-unity/hub/status",
              payload["availability_mode"] == "all"
              and {"topic": "ha-unity/hub/status"} in payload["availability"])
        if domain != "sensor":
            check(f"discovery {domain}: command topic is <base>/set",
                  payload.get("command_topic", "").endswith("/set"))
        else:
            check("discovery sensor: no command topic", "command_topic" not in payload)
    # suggested_area / device block
    ctx0 = {"unit": "alpha", "area_prefix": "^L[0-9]+ ", "hub_status_topic": "ha-unity/hub/status",
            "common_area_names": [pm.AREA_NAMES[a] for a in pm.FLOOR_AREAS["shared"]]}
    for eid, expect_sa in [("light.kitchen_pendant", "Kitchen"), ("light.alpha_lamp", None),
                           ("light.front_door", "Entry")]:
        ctx = run_vars(env, var_step, inputs, dict(ctx0, repeat={"item": eid}))
        dev = ctx["device"]
        check(f"suggested_area {eid}: {expect_sa}", dev.get("suggested_area") == expect_sa)
        check(f"device block {eid}: identifiers scoped to unit",
              dev["identifiers"][0].startswith("alpha_dev_"))
    # user-set device name wins over the integration default; otherwise default
    ctx = run_vars(env, var_step, inputs, dict(ctx0, repeat={"item": "light.kitchen_pendant"}))
    check("device name uses name_by_user when set", ctx["device"]["name"] == "Pendant (renamed)")
    ctx = run_vars(env, var_step, inputs, dict(ctx0, repeat={"item": "light.alpha_lamp"}))
    check("device name falls back to the default name", ctx["device"]["name"] == "Alpha Lamp")
    # configurable inputs: custom hub topic; empty prefix rule strips nothing
    ctx = run_vars(env, var_step, inputs, {"repeat": {"item": "light.kitchen_pendant"}, "unit": "alpha",
                                            "area_prefix": "", "hub_status_topic": "custom/hub/up",
                                            "common_area_names": []})
    check("hub_status_topic input is used for availability",
          {"topic": "custom/hub/up"} in ctx["common"]["availability"])
    check("empty area_prefix_regex sends no suggested_area for a prefixed area",
          "suggested_area" not in ctx["device"])
    # state payloads
    st = load("ha_unity_publish_state.yaml")
    rep = [a for a in st["actions"] if "repeat" in a][0]["repeat"]["sequence"]
    pub = next(a for a in rep if a.get("action") == "mqtt.publish" and a["data"]["topic"].endswith("/state"))
    for eid, state, expect in [
        ("light.alpha_lamp", "on", {"state": "ON", "brightness": 128}),
        ("switch.entry_relay", "on", "ON"),
        ("sensor.beta_temp", "71", "71"),
    ]:
        world = [State(e, state if e == eid else "on") for e in pm.ENTITIES]
        e = env_for(world, ATTRS)
        ctx = run_vars(e, rep[0]["variables"], {}, {"repeat": {"item": eid}, "unit": "alpha"})
        out = render(e, pub["data"]["payload"], ctx)
        check(f"state payload {eid} == {expect}", out == expect or str(out) == str(expect))


def test_climate():
    bp = load("ha_unity_climate.yaml")
    base_inputs = dict(pm.inputs("alpha"), object="entry", min_temp=64, max_temp=80,
                       climate_entity="climate.rm_entry", current_sensor="sensor.entry_t",
                       command_topic="alpha/climate/entry/target/set",
                       target_sensor="", mode_sensor="", temperature_unit="F",
                       expire_after_minutes=0)
    # climate entity must pass the same whitelist; add it to the stub world
    pm.ENTITIES["climate.rm_entry"] = ("entry", {"Live: Shared"})
    pm.ENTITIES["climate.rm_alpha_bed"] = ("alpha", set())  # not labelled
    pm.ENTITIES["climate.rm_beta_wrongunit"] = ("alpha", {"Live: Beta"})  # label/floor mismatch
    pm.ENTITIES["climate.rm_virtual"] = ("staging", {"Live: Alpha"})
    world = WORLD + [State("sensor.entry_t", "71.5"), State("sensor.entry_target", "68"),
                     State("sensor.entry_mode", "Heating"), State("sensor.bad_mode", "weird"),
                     State("sensor.dead_t", "unavailable"), State("sensor.dead_target", "unknown")]
    attrs = dict(ATTRS)
    attrs["climate.rm_entry"] = {"friendly_name": "Entry", "temperature": 70}
    env = env_for(world, attrs)
    check("climate runs in parallel mode (expiry must not block state publishing)",
          bp["mode"] == "parallel")
    gate = bp["actions"][0]
    choose = bp["actions"][1]
    cmd = choose["choose"][0]
    for label, rm, topic, payload, expect in [
        ("in-range", "climate.rm_entry", "alpha/climate/entry/target/set", "72", 72.0),
        ("above max clamps to 80", "climate.rm_entry", "alpha/climate/entry/target/set", "95", 80.0),
        ("below min clamps to 64", "climate.rm_entry", "alpha/climate/entry/target/set", "10", 64.0),
        ("garbage ignored", "climate.rm_entry", "alpha/climate/entry/target/set", "warm", None),
        ("unlabelled entity ignored", "climate.rm_alpha_bed", "alpha/climate/entry/target/set", "70", None),
        ("label/floor mismatch ignored", "climate.rm_beta_wrongunit", "alpha/climate/entry/target/set", "70", None),
        ("Meta-floor entity ignored", "climate.rm_virtual", "alpha/climate/entry/target/set", "70", None),
        ("wrong topic ignored", "climate.rm_entry", "alpha/climate/bedroom/target/set", "70", None),
    ]:
        inputs = dict(base_inputs, climate_entity=rm)
        ctx = run_vars(env, bp["variables"], inputs,
                       {"trigger": {"id": "cmd", "topic": topic, "payload": payload}})
        ok = all(render(env, c["value_template"], ctx) is True
                 for c in cmd["sequence"] if c.get("condition") == "template")
        got = None
        if ok:
            call = next(a for a in cmd["sequence"] if a.get("action"))
            got = render(env, call["data"]["temperature"], ctx)
        check(f"climate command {label}", got == expect)

    # state_changed filter: only watched entities pass; resync/cmd always pass
    inputs = dict(base_inputs, target_sensor="sensor.entry_target", mode_sensor="sensor.entry_mode")
    for label, trig, expect in [
        ("watched climate entity passes", {"id": "state", "event": {"data": {"entity_id": "climate.rm_entry"}}}, True),
        ("watched current sensor passes", {"id": "state", "event": {"data": {"entity_id": "sensor.entry_t"}}}, True),
        ("watched target sensor passes", {"id": "state", "event": {"data": {"entity_id": "sensor.entry_target"}}}, True),
        ("watched mode sensor passes", {"id": "state", "event": {"data": {"entity_id": "sensor.entry_mode"}}}, True),
        ("unrelated entity filtered out", {"id": "state", "event": {"data": {"entity_id": "light.alpha_lamp"}}}, False),
        ("resync always passes", {"id": "resync"}, True),
    ]:
        ctx = run_vars(env, bp["variables"], inputs, {"trigger": trig})
        check(f"climate event filter: {label}", render(env, gate["value_template"], ctx) is expect)
    ctx = run_vars(env, bp["variables"], base_inputs,
                   {"trigger": {"id": "state", "event": {"data": {"entity_id": "sensor.entry_target"}}}})
    check("climate event filter: optional sensors not watched when empty",
          render(env, gate["value_template"], ctx) is False)

    # target and action sources
    for label, inp, key, expect in [
        ("target from the climate attribute when no target sensor", base_inputs, "target_val", 70),
        ("target from the target sensor when given", dict(base_inputs, target_sensor="sensor.entry_target"), "target_val", 68),
        ("mode 'Heating' maps to heating", dict(base_inputs, mode_sensor="sensor.entry_mode"), "action_val", "heating"),
        ("unknown mode string publishes nothing", dict(base_inputs, mode_sensor="sensor.bad_mode"), "action_val", ""),
        ("no mode sensor publishes nothing", base_inputs, "action_val", ""),
    ]:
        ctx = run_vars(env, bp["variables"], inp, {"trigger": {"id": "resync"}})
        check(f"climate {label}", ctx[key] == expect)

    # discovery payload
    default_pub = choose["default"][0]["default"]
    disc = next(x for x in default_pub if x.get("action") == "mqtt.publish")["data"]
    for label, inp, expect_action in [("without mode sensor", base_inputs, False),
                                     ("with mode sensor", dict(base_inputs, mode_sensor="sensor.entry_mode"), True)]:
        ctx = run_vars(env, bp["variables"], inp, {"trigger": {"id": "resync"}})
        out = render(env, disc["payload"], ctx)
        payload = json.loads(out) if isinstance(out, str) else out
        check(f"climate discovery {label}: action_topic present == {expect_action}",
              ("action_topic" in payload) is expect_action)
        check(f"climate discovery {label}: unit and range come from inputs",
              payload["temperature_unit"] == "F" and payload["min_temp"] == 64 and payload["max_temp"] == 80)
        check(f"climate discovery {label}: availability uses the hub topic",
              {"topic": "ha-unity/hub/status"} in payload["availability"])
    ctx = run_vars(env, bp["variables"], dict(base_inputs, temperature_unit="C"), {"trigger": {"id": "resync"}})
    out = render(env, disc["payload"], ctx)
    check("climate discovery honours temperature_unit input",
          (json.loads(out) if isinstance(out, str) else out)["temperature_unit"] == "C")
    # numeric guards on the publishes
    ifs = [x for x in default_pub if "if" in x]
    cur_guard, tgt_guard, act_guard = (x["if"][0]["value_template"] for x in ifs)
    for label, inp, guard, expect in [
        ("current published when numeric", base_inputs, cur_guard, True),
        ("current NOT published when unavailable", dict(base_inputs, current_sensor="sensor.dead_t"), cur_guard, False),
        ("target NOT published when sensor unknown", dict(base_inputs, target_sensor="sensor.dead_target"), tgt_guard, False),
        ("target published from attribute fallback", base_inputs, tgt_guard, True),
        ("action NOT published without a mode sensor", base_inputs, act_guard, False),
        ("action published for a known mode", dict(base_inputs, mode_sensor="sensor.entry_mode"), act_guard, True),
    ]:
        ctx = run_vars(env, bp["variables"], inp, {"trigger": {"id": "resync"}})
        check(f"climate guard: {label}", render(env, guard, ctx) is expect)

    # expiry wiring
    exp = next(x for x in cmd["sequence"] if "if" in x)
    wait = exp["then"][0]
    check("climate expiry waits on the same command topic",
          isinstance(wait["wait_for_trigger"][0]["topic"], Inp) and wait["wait_for_trigger"][0]["topic"].name == "command_topic")
    check("climate expiry continues on timeout", wait["continue_on_timeout"] is True)
    after = exp["then"][1]
    check("climate expiry runs the user action only when the wait timed out",
          "wait.trigger is none" in after["if"][0]["value_template"]
          and isinstance(after["then"], Inp) and after["then"].name == "expire_action")
    for minutes, expect in [(0, False), (30, True)]:
        ctx = run_vars(env, bp["variables"], dict(base_inputs, expire_after_minutes=minutes),
                       {"trigger": {"id": "cmd", "topic": "alpha/climate/entry/target/set", "payload": "70"}})
        check(f"climate expiry gate with expire_after_minutes={minutes}",
              render(env, exp["if"][0]["value_template"], ctx) is expect)
    check("climate default expiry is off",
          bp["blueprint"]["input"]["expire_after_minutes"]["default"] == 0
          and bp["blueprint"]["input"]["expire_action"]["default"] == [])


def light_discovery(eid):
    env = env_for(WORLD, ATTRS)
    disc = load("ha_unity_publish_discovery.yaml")
    inputs = pm.inputs("alpha")
    loop = [a for a in disc["actions"] if "repeat" in a][1]["repeat"]["sequence"]
    ctx = run_vars(env, loop[0]["variables"], inputs, {"repeat": {"item": eid}, "unit": "alpha",
                                                        "area_prefix": "", "hub_status_topic": "ha-unity/hub/status",
                                                        "common_area_names": []})
    branch = next(b for b in loop[1]["choose"] if "'light'" in b["conditions"])
    out = render(env, branch["sequence"][0]["data"]["payload"], ctx)
    return json.loads(out) if isinstance(out, str) else out


def light_state(eid, state, attrs):
    st = load("ha_unity_publish_state.yaml")
    rep = [a for a in st["actions"] if "repeat" in a][0]["repeat"]["sequence"]
    pub = next(a for a in rep if a.get("action") == "mqtt.publish" and a["data"]["topic"].endswith("/state"))
    world = [State(e, state if e == eid else "on") for e in pm.ENTITIES]
    e = env_for(world, {eid: attrs})
    ctx = run_vars(e, rep[0]["variables"], {}, {"repeat": {"item": eid}, "unit": "alpha"})
    out = render(e, pub["data"]["payload"], ctx)
    return json.loads(out) if isinstance(out, str) else out


def test_lights():
    # ---- discovery: advertised capabilities ----
    d = light_discovery("light.alpha_color")
    check("light discovery: colour-capable light that is OFF still advertises colour modes",
          d["supported_color_modes"] == ["color_temp", "xy"])
    check("light discovery: color_temp uses kelvin with the light's own range",
          d.get("color_temp_kelvin") is True and d["min_kelvin"] == 2000 and d["max_kelvin"] == 6535)
    check("light discovery: schema json and command topic", d["schema"] == "json" and d["command_topic"].endswith("/set"))
    d = light_discovery("light.alpha_rgb")
    check("light discovery: rgbw maps to rgb with brightness flag",
          d["supported_color_modes"] == ["rgb"] and d.get("brightness") is True)
    check("light discovery: hs light", light_discovery("light.alpha_hs")["supported_color_modes"] == ["hs"])
    check("light discovery: white-only light falls back to brightness",
          light_discovery("light.alpha_white")["supported_color_modes"] == ["brightness"])
    check("light discovery: onoff light", light_discovery("light.alpha_onoff")["supported_color_modes"] == ["onoff"])
    check("light discovery: no attributes at all falls back to onoff",
          light_discovery("light.alpha_noattr")["supported_color_modes"] == ["onoff"])
    check("light discovery: brightness-only light stays brightness",
          light_discovery("light.kitchen_pendant")["supported_color_modes"] == ["brightness"])

    # ---- state payloads ----
    check("light state: off light sends only state", light_state("light.alpha_color", "off", {}) == {"state": "OFF"})
    check("light state: colour temperature (kelvin) and mode",
          light_state("light.alpha_color", "on", {"brightness": 200, "color_mode": "color_temp", "color_temp_kelvin": 3000})
          == {"state": "ON", "brightness": 200, "color_mode": "color_temp", "color_temp": 3000})
    check("light state: xy colour",
          light_state("light.alpha_color", "on", {"brightness": 90, "color_mode": "xy", "xy_color": [0.3, 0.4]})
          == {"state": "ON", "brightness": 90, "color_mode": "xy", "color": {"x": 0.3, "y": 0.4}})
    check("light state: hs colour",
          light_state("light.alpha_hs", "on", {"brightness": 90, "color_mode": "hs", "hs_color": [120.0, 80.0]})
          == {"state": "ON", "brightness": 90, "color_mode": "hs", "color": {"h": 120.0, "s": 80.0}})
    check("light state: rgbw reports as rgb",
          light_state("light.alpha_rgb", "on", {"brightness": 10, "color_mode": "rgbw", "rgb_color": [1, 2, 3]})
          == {"state": "ON", "brightness": 10, "color_mode": "rgb", "color": {"r": 1, "g": 2, "b": 3}})
    check("light state: white mode reports as brightness",
          light_state("light.alpha_white", "on", {"brightness": 77, "color_mode": "white"})
          == {"state": "ON", "brightness": 77, "color_mode": "brightness"})

    # ---- commands ----
    def data(eid, payload):
        r = bridge("alpha", "alpha/light/" + eid.split(".")[1] + "/set", json.dumps(payload))
        return r[0][2] if r else None
    check("light command: color_temp in range", data("light.alpha_color", {"state": "ON", "color_temp": 3000}) == {"color_temp_kelvin": 3000})
    check("light command: color_temp clamped to max", data("light.alpha_color", {"state": "ON", "color_temp": 9999}) == {"color_temp_kelvin": 6535})
    check("light command: color_temp clamped to min", data("light.alpha_color", {"state": "ON", "color_temp": 100}) == {"color_temp_kelvin": 2000})
    check("light command: brightness and color_temp together",
          data("light.alpha_color", {"state": "ON", "brightness": 128, "color_temp": 4000}) == {"brightness": 128, "color_temp_kelvin": 4000})
    check("light command: xy colour", data("light.alpha_color", {"state": "ON", "color": {"x": 0.3, "y": 0.4}}) == {"xy_color": [0.3, 0.4]})
    check("light command: xy clamped to 0..1", data("light.alpha_color", {"state": "ON", "color": {"x": 5, "y": -2}}) == {"xy_color": [1.0, 0.0]})
    check("light command: hs colour accepted and clamped",
          data("light.alpha_hs", {"state": "ON", "color": {"h": 400, "s": 150}}) == {"hs_color": [360.0, 100.0]})
    check("light command: rgb clamped (light supports rgbw)",
          data("light.alpha_rgb", {"state": "ON", "color": {"r": 300, "g": -4, "b": 10}}) == {"rgb_color": [255, 0, 10]})
    check("light command: hs rejected on an xy-only light (still turns on)",
          data("light.alpha_color", {"state": "ON", "color": {"h": 10, "s": 10}}) == {})
    check("light command: color_temp rejected on a light without it",
          data("light.alpha_hs", {"state": "ON", "color_temp": 3000}) == {})
    check("light command: colour rejected on an onoff light",
          data("light.alpha_onoff", {"state": "ON", "color": {"r": 1, "g": 2, "b": 3}}) == {})
    check("light command: non-object colour ignored",
          data("light.alpha_color", {"state": "ON", "color": "red"}) == {})
    check("light command: OFF still works", bridge("alpha", "alpha/light/alpha_color/set", '{"state":"OFF"}')[0][0] == "light.turn_off")


def discovery_payload(eid):
    domain = eid.split(".")[0]
    env = env_for(WORLD, ATTRS)
    disc = load("ha_unity_publish_discovery.yaml")
    inputs = pm.inputs("alpha")
    loop = [a for a in disc["actions"] if "repeat" in a][1]["repeat"]["sequence"]
    ctx = run_vars(env, loop[0]["variables"], inputs, {"repeat": {"item": eid}, "unit": "alpha",
                                                        "area_prefix": "", "hub_status_topic": "ha-unity/hub/status",
                                                        "common_area_names": []})
    branch = next(b for b in loop[1]["choose"] if f"'{domain}'" in b["conditions"])
    out = render(env, branch["sequence"][0]["data"]["payload"], ctx)
    return json.loads(out) if isinstance(out, str) else out


def state_run(eid, state, attrs):
    """Returns (availability payload, state payload or None if not published)."""
    st = load("ha_unity_publish_state.yaml")
    rep = [a for a in st["actions"] if "repeat" in a][0]["repeat"]["sequence"]
    world = [State(e, state if e == eid else "on") for e in pm.ENTITIES]
    e = env_for(world, {eid: attrs})
    ctx = run_vars(e, rep[0]["variables"], {}, {"repeat": {"item": eid}, "unit": "alpha"})
    avail = render(e, rep[1]["data"]["payload"], ctx)
    gate = render(e, rep[2]["value_template"], ctx)
    if gate is not True:
        return avail, None
    out = render(e, rep[3]["data"]["payload"], ctx)
    return avail, (json.loads(out) if isinstance(out, str) and out.startswith("{") else out)


def test_domains():
    # ---------- discovery ----------
    for eid in ["light.alpha_lamp", "switch.entry_relay", "sensor.beta_temp"]:
        check(f"discovery {eid}: has a state topic", "state_topic" in discovery_payload(eid))
    d = discovery_payload("scene.alpha_movie")
    check("discovery scene: command-only (no state_topic), payload_on ON",
          "state_topic" not in d and d["command_topic"].endswith("/set") and d["payload_on"] == "ON")
    d = discovery_payload("fan.alpha_fan")
    check("discovery fan: JSON state and command through templates",
          d["state_topic"].endswith("/state") and d["command_topic"].endswith("/set")
          and d["state_value_template"] == "{{ value_json.state }}" and d["command_template"] == '{"state": "{{ value }}"}')
    check("discovery fan: speed, preset, oscillation and direction all advertised",
          all(k in d for k in ("percentage_command_topic", "preset_mode_command_topic", "oscillation_command_topic", "direction_command_topic"))
          and d["preset_modes"] == ["auto", "sleep"])
    check("discovery fan: command templates build JSON",
          d["percentage_command_template"] == '{"percentage": {{ value }}}'
          and d["preset_mode_command_template"] == '{"preset_mode": "{{ value }}"}'
          and d["direction_command_template"] == '{"direction": "{{ value }}"}'
          and "oscillating" in d["oscillation_command_template"])
    d = discovery_payload("fan.alpha_basic")
    check("discovery fan: on/off-only fan advertises no extra controls",
          not any(k.startswith(("percentage", "preset", "oscillation", "direction")) for k in d))
    d = discovery_payload("cover.alpha_blind")
    check("discovery cover: open, close, stop as JSON commands, device class",
          d["payload_open"] == '{"command": "open"}' and d["payload_close"] == '{"command": "close"}'
          and d["payload_stop"] == '{"command": "stop"}' and d["device_class"] == "blind")
    check("discovery cover: position uses the state topic and a JSON set command",
          d["position_topic"].endswith("/state") and d["set_position_topic"].endswith("/set")
          and d["set_position_template"] == '{"position": {{ position }}}')
    d = discovery_payload("cover.alpha_nodc")
    check("discovery cover: no stop button and no position without those features",
          d["payload_stop"] is None and "set_position_topic" not in d and "device_class" not in d)
    d = discovery_payload("light.alpha_effects")
    check("discovery light: effects advertised from the entity's effect list",
          d.get("effect") is True and d["effect_list"] == ["rainbow", "pulse"])
    check("discovery light: no effects when the light has none", "effect" not in discovery_payload("light.alpha_color"))

    # ---------- state ----------
    avail, st = state_run("scene.alpha_movie", "unknown", {})
    check("state scene: 'unknown' (never used) is still online, and no state is published",
          avail == "online" and st is None)
    check("state scene: unavailable is offline", state_run("scene.alpha_movie", "unavailable", {})[0] == "offline")
    check("state sensor: 'unknown' is offline", state_run("sensor.beta_temp", "unknown", {})[0] == "offline")
    check("state fan: all fields",
          state_run("fan.alpha_fan", "on", {"percentage": 40, "preset_mode": "auto", "oscillating": True, "direction": "forward"})[1]
          == {"state": "ON", "percentage": 40, "preset_mode": "auto", "oscillating": True, "direction": "forward"})
    check("state fan: off with no attributes", state_run("fan.alpha_fan", "off", {})[1] == {"state": "OFF"})
    check("state cover: state and position",
          state_run("cover.alpha_blind", "open", {"current_position": 70})[1] == {"state": "open", "position": 70})
    check("state cover: opening without a position",
          state_run("cover.alpha_blind", "opening", {})[1] == {"state": "opening"})
    check("state light: effect included",
          state_run("light.alpha_effects", "on", {"brightness": 5, "effect": "rainbow", "color_mode": "brightness"})[1]
          == {"state": "ON", "brightness": 5, "effect": "rainbow", "color_mode": "brightness"})

    # ---------- commands ----------
    def cmd(eid, payload):
        dom, obj = eid.split(".")
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        r = bridge("alpha", f"alpha/{dom}/{obj}/set", raw)
        return (r[0][0], r[0][2]) if r else None
    check("fan command: ON", cmd("fan.alpha_fan", {"state": "ON"}) == ("fan.turn_on", {}))
    check("fan command: OFF", cmd("fan.alpha_fan", {"state": "OFF"}) == ("fan.turn_off", {}))
    check("fan command: percentage", cmd("fan.alpha_fan", {"percentage": 50}) == ("fan.set_percentage", {"percentage": 50}))
    check("fan command: percentage clamped to 100", cmd("fan.alpha_fan", {"percentage": 150})[1] == {"percentage": 100})
    check("fan command: percentage clamped to 0", cmd("fan.alpha_fan", {"percentage": -5})[1] == {"percentage": 0})
    check("fan command: non-numeric percentage ignored", cmd("fan.alpha_fan", {"percentage": "abc"}) is None)
    check("fan command: percentage rejected on a fan without speed", cmd("fan.alpha_basic", {"percentage": 50}) is None)
    check("fan command: valid preset", cmd("fan.alpha_fan", {"preset_mode": "sleep"}) == ("fan.set_preset_mode", {"preset_mode": "sleep"}))
    check("fan command: unknown preset ignored", cmd("fan.alpha_fan", {"preset_mode": "turbo"}) is None)
    check("fan command: oscillation", cmd("fan.alpha_fan", {"oscillating": True}) == ("fan.oscillate", {"oscillating": True}))
    check("fan command: non-boolean oscillation ignored", cmd("fan.alpha_fan", {"oscillating": "yes"}) is None)
    check("fan command: oscillation rejected on a basic fan", cmd("fan.alpha_basic", {"oscillating": True}) is None)
    check("fan command: direction", cmd("fan.alpha_fan", {"direction": "reverse"}) == ("fan.set_direction", {"direction": "reverse"}))
    check("fan command: invalid direction ignored", cmd("fan.alpha_fan", {"direction": "sideways"}) is None)
    check("scene command: ON activates", cmd("scene.alpha_movie", "ON") == ("scene.turn_on", {}))
    check("scene command: anything else ignored", cmd("scene.alpha_movie", "OFF") is None and cmd("scene.alpha_movie", {"state": "ON"}) is None)
    check("cover command: open", cmd("cover.alpha_blind", {"command": "open"}) == ("cover.open_cover", {}))
    check("cover command: close", cmd("cover.alpha_blind", {"command": "close"}) == ("cover.close_cover", {}))
    check("cover command: stop", cmd("cover.alpha_blind", {"command": "stop"}) == ("cover.stop_cover", {}))
    check("cover command: position", cmd("cover.alpha_blind", {"position": 40}) == ("cover.set_cover_position", {"position": 40}))
    check("cover command: position clamped", cmd("cover.alpha_blind", {"position": 250})[1] == {"position": 100})
    check("cover command: position rejected without the feature", cmd("cover.alpha_nodc", {"position": 40}) is None)
    check("cover command: stop rejected without the feature", cmd("cover.alpha_nodc", {"command": "stop"}) is None)
    check("cover command: unknown command ignored", cmd("cover.alpha_blind", {"command": "explode"}) is None)
    check("cover command: blocked class ignored even for a valid command", cmd("cover.alpha_garage", {"command": "open"}) is None)
    check("cover command: unavailable cover ignored", cmd("cover.alpha_unavail", {"command": "open"}) is None)
    check("light command: valid effect", cmd("light.alpha_effects", {"state": "ON", "effect": "rainbow"}) == ("light.turn_on", {"effect": "rainbow"}))
    check("light command: unknown effect dropped (still turns on)", cmd("light.alpha_effects", {"state": "ON", "effect": "evil"}) == ("light.turn_on", {}))


if __name__ == "__main__":
    test_syntax_and_exposed()
    test_bridge()
    test_payloads()
    test_climate()
    test_lights()
    test_domains()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for x in FAILS:
            print("  -", x)
        sys.exit(1)
    print("all rendered-template checks passed")
