"""Per-domain adapters: discovery, state and validated commands. Pure; no HA imports.

Ported from the blueprints that were verified live, with the same hard limits:

* A service is never taken from a payload. Each command maps to a hardcoded
  service of its own domain.
* The entity comes from the topic (checked by the caller against the policy),
  never from the payload.
* Values are clamped to the entity's own range, and a command is accepted only
  if the entity advertises the feature (supported_features / colour modes /
  preset list / effect list).
* Anything unparseable produces no service call at all.

One JSON state topic and one JSON command topic per entity (fans, covers,
lights) keeps commands on the four-level topic the broker ACL allows.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from .policy import COMMAND_DOMAINS, EntityFacts, UNAVAILABLE_STATES
from .topics import entity_base


# ---------------------------------------------------------------------------
# small coercion helpers (mirror the Jinja int()/float() filters used before)
# ---------------------------------------------------------------------------
def _int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _float(value: Any) -> float | None:
    """A finite float, or None."""
    try:
        if isinstance(value, bool):
            return None
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _feature(facts: EntityFacts, bit: int) -> bool:
    return (_int(facts.attributes.get("supported_features"), 0) & bit) == bit


# ---------------------------------------------------------------------------
# naming (device and entity names, suggested area)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NamingContext:
    """Hub-wide naming settings."""

    # Regex for a prefix on hub area names that must not reach the child
    # ("L2 Kitchen" -> "Kitchen"). Empty = send names unchanged.
    area_prefix: str = ""
    # Names of areas on the common floors; they pass through unchanged.
    common_area_names: Collection[str] = field(default_factory=tuple)


def device_display_name(facts: EntityFacts) -> str | None:
    """The device's name (user-set name wins), else the entity's friendly name."""
    return facts.device_name if facts.device_id else facts.friendly_name


def entity_display_name(facts: EntityFacts) -> str | None:
    """None when the entity *is* the device; else the friendly name minus the
    device-name prefix. MQTT entities with a device are shown as
    "<device> <entity>", so sending the full friendly name would double it."""
    dev = device_display_name(facts)
    fname = facts.friendly_name
    if not dev or not fname or fname == dev:
        return None
    if fname.startswith(dev + " "):
        return fname[len(dev) + 1 :]
    return fname


def suggested_area(facts: EntityFacts, naming: NamingContext) -> str | None:
    name = facts.area_name
    if not name:
        return None
    if naming.area_prefix:
        try:
            if re.match(naming.area_prefix, name):
                return re.sub(naming.area_prefix, "", name).strip() or None
        except re.error:
            pass
    if name in naming.common_area_names:
        return name
    return None


def _common(unit: str, facts: EntityFacts, hub_status_topic: str, naming: NamingContext) -> dict[str, Any]:
    base = entity_base(unit, facts.entity_id)
    device: dict[str, Any] = {
        "identifiers": [f"{unit}_dev_{facts.device_id or facts.entity_id}"],
        "name": device_display_name(facts),
    }
    area = suggested_area(facts, naming)
    if area:
        device["suggested_area"] = area
    return {
        "device": device,
        "name": entity_display_name(facts),
        "unique_id": f"{unit}_{facts.entity_id.replace('.', '_')}",
        "availability": [{"topic": hub_status_topic}, {"topic": f"{base}/availability"}],
        "availability_mode": "all",
    }


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
_LIGHT_MODES = ("onoff", "brightness", "color_temp", "hs", "xy", "rgb")


def _light_modes(facts: EntityFacts) -> list[str]:
    raw = facts.attributes.get("supported_color_modes") or []
    out: list[str] = []
    for mode in raw:
        mode = "rgb" if mode in ("rgbw", "rgbww") else mode
        if mode in _LIGHT_MODES and mode not in out:
            out.append(mode)
    if not out:
        out = ["brightness"] if raw else ["onoff"]
    return out


def discovery_payload(
    unit: str,
    facts: EntityFacts,
    hub_status_topic: str,
    naming: NamingContext,
) -> dict[str, Any] | None:
    """The MQTT discovery config for one exposed entity, or None for a domain
    that has no adapter."""
    builder = _DISCOVERY.get(facts.domain)
    if builder is None:
        return None
    base = entity_base(unit, facts.entity_id)
    return builder(facts, base, _common(unit, facts, hub_status_topic, naming))


def _d_light(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    modes = _light_modes(f)
    d = common | {
        "schema": "json",
        "state_topic": f"{base}/state",
        "command_topic": f"{base}/set",
        "supported_color_modes": modes,
    }
    if "color_temp" in modes:
        d |= {
            "color_temp_kelvin": True,
            "min_kelvin": _int(f.attributes.get("min_color_temp_kelvin"), 2000),
            "max_kelvin": _int(f.attributes.get("max_color_temp_kelvin"), 6535),
        }
    if "rgb" in modes:
        d["brightness"] = True
    effects = f.attributes.get("effect_list")
    if effects:
        d |= {"effect": True, "effect_list": list(effects)}
    return d


def _d_switch(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    return common | {"state_topic": f"{base}/state", "command_topic": f"{base}/set"}


def _d_sensor(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    return common | {
        "state_topic": f"{base}/state",
        "device_class": f.attributes.get("device_class"),
        "unit_of_measurement": f.attributes.get("unit_of_measurement"),
    }


def _d_binary_sensor(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    return common | {
        "state_topic": f"{base}/state",
        "device_class": f.attributes.get("device_class"),
    }


# Fan feature bits: 1 speed, 2 oscillate, 4 direction, 8 preset mode.
def _d_fan(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    state, cmd = f"{base}/state", f"{base}/set"
    d = common | {
        "state_topic": state,
        "command_topic": cmd,
        "command_template": '{"state": "{{ value }}"}',
        "state_value_template": "{{ value_json.state }}",
    }
    if _feature(f, 1):
        d |= {
            "percentage_command_topic": cmd,
            "percentage_command_template": '{"percentage": {{ value }}}',
            "percentage_state_topic": state,
            "percentage_value_template": "{{ value_json.percentage }}",
            "speed_range_min": 1,
            "speed_range_max": 100,
        }
    presets = f.attributes.get("preset_modes")
    if _feature(f, 8) and presets:
        d |= {
            "preset_modes": list(presets),
            "preset_mode_command_topic": cmd,
            "preset_mode_command_template": '{"preset_mode": "{{ value }}"}',
            "preset_mode_state_topic": state,
            "preset_mode_value_template": "{{ value_json.preset_mode }}",
        }
    if _feature(f, 2):
        d |= {
            "oscillation_command_topic": cmd,
            "oscillation_command_template": '{"oscillating": {{ "true" if value == "oscillate_on" else "false" }}}',
            "oscillation_state_topic": state,
            "oscillation_value_template": '{{ "oscillate_on" if value_json.oscillating else "oscillate_off" }}',
        }
    if _feature(f, 4):
        d |= {
            "direction_command_topic": cmd,
            "direction_command_template": '{"direction": "{{ value }}"}',
            "direction_state_topic": state,
            "direction_value_template": "{{ value_json.direction }}",
        }
    return d


def _d_scene(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    # MQTT scenes are command-only: no state topic.
    return common | {"command_topic": f"{base}/set", "payload_on": "ON"}


# Cover feature bits: 1 open, 2 close, 4 set position, 8 stop. A null payload
# hides that button. Tilt is not carried.
def _d_cover(f: EntityFacts, base: str, common: dict[str, Any]) -> dict[str, Any]:
    d = common | {
        "state_topic": f"{base}/state",
        "value_template": "{{ value_json.state }}",
        "command_topic": f"{base}/set",
        "payload_open": '{"command": "open"}' if _feature(f, 1) else None,
        "payload_close": '{"command": "close"}' if _feature(f, 2) else None,
        "payload_stop": '{"command": "stop"}' if _feature(f, 8) else None,
    }
    if f.attributes.get("device_class"):
        d["device_class"] = f.attributes["device_class"]
    if _feature(f, 4):
        d |= {
            "position_topic": f"{base}/state",
            "position_template": "{{ value_json.position }}",
            "set_position_topic": f"{base}/set",
            "set_position_template": '{"position": {{ position }}}',
            "position_open": 100,
            "position_closed": 0,
        }
    return d


_DISCOVERY = {
    "light": _d_light,
    "switch": _d_switch,
    "sensor": _d_sensor,
    "binary_sensor": _d_binary_sensor,
    "fan": _d_fan,
    "scene": _d_scene,
    "cover": _d_cover,
}


# ---------------------------------------------------------------------------
# availability and state
# ---------------------------------------------------------------------------
def is_available(facts: EntityFacts) -> bool:
    """Scenes sit in "unknown" until first used, which is still available."""
    if facts.domain == "scene":
        return facts.state != "unavailable"
    return facts.state not in UNAVAILABLE_STATES


def availability_payload(facts: EntityFacts) -> str:
    return "online" if is_available(facts) else "offline"


def state_payload(facts: EntityFacts) -> str | None:
    """The retained state payload, or None when nothing should be published
    (unavailable entity, or a command-only scene)."""
    if not is_available(facts) or facts.domain == "scene":
        return None
    a = facts.attributes
    state = facts.state
    if facts.domain == "light":
        return json.dumps(_light_state(facts))
    if facts.domain == "fan":
        d: dict[str, Any] = {"state": state.upper()}
        for key in ("percentage", "preset_mode", "oscillating", "direction"):
            if a.get(key) is not None:
                d[key] = a[key]
        return json.dumps(d)
    if facts.domain == "cover":
        d = {"state": state}
        if a.get("current_position") is not None:
            d["position"] = a["current_position"]
        return json.dumps(d)
    if facts.domain in ("switch", "binary_sensor"):
        return state.upper()
    return state


def _light_state(f: EntityFacts) -> dict[str, Any]:
    a = f.attributes
    d: dict[str, Any] = {"state": f.state.upper()}
    cm0 = a.get("color_mode")
    cm = "rgb" if cm0 in ("rgbw", "rgbww") else ("brightness" if cm0 == "white" else cm0)
    if a.get("brightness") is not None:
        d["brightness"] = a["brightness"]
    if a.get("effect") is not None:
        d["effect"] = a["effect"]
    if cm:
        d["color_mode"] = cm
    if cm == "color_temp" and a.get("color_temp_kelvin") is not None:
        d["color_temp"] = a["color_temp_kelvin"]
    if cm == "hs" and a.get("hs_color"):
        c = a["hs_color"]
        d["color"] = {"h": c[0], "s": c[1]}
    if cm == "xy" and a.get("xy_color"):
        c = a["xy_color"]
        d["color"] = {"x": c[0], "y": c[1]}
    if cm == "rgb" and a.get("rgb_color"):
        c = a["rgb_color"]
        d["color"] = {"r": c[0], "g": c[1], "b": c[2]}
    return d


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ServiceCall:
    """A service call the hub is allowed to make for a child's command."""

    domain: str
    service: str
    entity_id: str
    data: Mapping[str, Any] = field(default_factory=dict)


def command_calls(facts: EntityFacts, payload: str) -> list[ServiceCall]:
    """Validate a child's command against the entity's *current* attributes and
    return the service calls to make (possibly none). The caller has already
    checked that the entity is exposed to that child."""
    if facts.domain not in COMMAND_DOMAINS:
        return []
    handler = _COMMANDS.get(facts.domain)
    return handler(facts, payload) if handler else []


def _json_object(payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _word(payload: str) -> str:
    return payload.strip().upper() if isinstance(payload, str) else ""


def _c_light(f: EntityFacts, payload: str) -> list[ServiceCall]:
    p = _json_object(payload)
    state = str(p.get("state", "")).upper()
    if state == "OFF":
        return [ServiceCall("light", "turn_off", f.entity_id)]
    if state != "ON":
        return []
    a = f.attributes
    modes = list(a.get("supported_color_modes") or [])
    c = p.get("color") if isinstance(p.get("color"), dict) else {}
    d: dict[str, Any] = {}
    if "brightness" in p:
        d["brightness"] = int(_clamp(_int(p["brightness"], 255), 1, 255))
    if "color_temp" in p and "color_temp" in modes:
        lo = _int(a.get("min_color_temp_kelvin"), 2000)
        hi = _int(a.get("max_color_temp_kelvin"), 6535)
        d["color_temp_kelvin"] = int(_clamp(_int(p["color_temp"], hi), lo, hi))
    if "h" in c and "s" in c and "hs" in modes:
        d["hs_color"] = [
            _clamp(_float(c["h"]) or 0.0, 0, 360),
            _clamp(_float(c["s"]) or 0.0, 0, 100),
        ]
    if "x" in c and "y" in c and "xy" in modes:
        d["xy_color"] = [
            _clamp(_float(c["x"]) or 0.0, 0, 1),
            _clamp(_float(c["y"]) or 0.0, 0, 1),
        ]
    if (
        all(k in c for k in ("r", "g", "b"))
        and any(m in modes for m in ("rgb", "rgbw", "rgbww"))
    ):
        d["rgb_color"] = [int(_clamp(_int(c[k], 0), 0, 255)) for k in ("r", "g", "b")]
    effect = p.get("effect")
    if isinstance(effect, str) and effect in (a.get("effect_list") or []):
        d["effect"] = effect
    return [ServiceCall("light", "turn_on", f.entity_id, d)]


def _c_switch(f: EntityFacts, payload: str) -> list[ServiceCall]:
    word = _word(payload)
    if word == "ON":
        return [ServiceCall("switch", "turn_on", f.entity_id)]
    if word == "OFF":
        return [ServiceCall("switch", "turn_off", f.entity_id)]
    return []


def _c_scene(f: EntityFacts, payload: str) -> list[ServiceCall]:
    # The scene's own contents are the privilege; they are not inspected.
    return [ServiceCall("scene", "turn_on", f.entity_id)] if _word(payload) == "ON" else []


def _c_fan(f: EntityFacts, payload: str) -> list[ServiceCall]:
    p = _json_object(payload)
    state = str(p.get("state", "")).upper()
    if state == "OFF":
        return [ServiceCall("fan", "turn_off", f.entity_id)]
    calls: list[ServiceCall] = []
    if state == "ON":
        calls.append(ServiceCall("fan", "turn_on", f.entity_id))
    pct = _float(p.get("percentage")) if "percentage" in p else None
    if pct is not None and _feature(f, 1):
        calls.append(
            ServiceCall("fan", "set_percentage", f.entity_id, {"percentage": int(_clamp(round(pct), 0, 100))})
        )
    preset = p.get("preset_mode")
    if (
        isinstance(preset, str)
        and preset in (f.attributes.get("preset_modes") or [])
        and _feature(f, 8)
    ):
        calls.append(ServiceCall("fan", "set_preset_mode", f.entity_id, {"preset_mode": preset}))
    if isinstance(p.get("oscillating"), bool) and _feature(f, 2):
        calls.append(ServiceCall("fan", "oscillate", f.entity_id, {"oscillating": p["oscillating"]}))
    direction = p.get("direction")
    if direction in ("forward", "reverse") and _feature(f, 4):
        calls.append(ServiceCall("fan", "set_direction", f.entity_id, {"direction": direction}))
    return calls


def _c_cover(f: EntityFacts, payload: str) -> list[ServiceCall]:
    p = _json_object(payload)
    command = p.get("command")
    if command == "open" and _feature(f, 1):
        return [ServiceCall("cover", "open_cover", f.entity_id)]
    if command == "close" and _feature(f, 2):
        return [ServiceCall("cover", "close_cover", f.entity_id)]
    if command == "stop" and _feature(f, 8):
        return [ServiceCall("cover", "stop_cover", f.entity_id)]
    pos = _float(p.get("position")) if "position" in p else None
    if pos is not None and _feature(f, 4):
        return [
            ServiceCall(
                "cover", "set_cover_position", f.entity_id,
                {"position": int(_clamp(round(pos), 0, 100))},
            )
        ]
    return []


_COMMANDS = {
    "light": _c_light,
    "switch": _c_switch,
    "scene": _c_scene,
    "fan": _c_fan,
    "cover": _c_cover,
}
