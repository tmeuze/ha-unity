"""Synthetic thermostat for a child: pure logic, no Home Assistant imports.

A guest-facing thermostat built from ONE hub climate entity (for example a
RoomMind room entity). The child sees an MQTT climate entity that shows the
current temperature, the target and what the system is doing, and lets the
guest change the target within a clamped range. The guest can change nothing
else: no mode, no preset, no on/off.

Everything derives from the hub climate entity's attributes, which any Home
Assistant climate entity provides:

* current temperature  <- ``current_temperature`` (or an optional sensor)
* target temperature   <- ``temperature``         (or an optional sensor)
* action               <- ``hvac_action`` (heating, cooling, idle, off, ...)

Hard limits, as for every adapter:

* The entity and the service come from the configuration and the topic, never
  from the payload. The one service a command can reach is
  ``climate.set_temperature`` on the granted entity.
* The value is clamped to the grant's range AND the entity's own range.
* Anything unparseable, or sent to an entity that cannot take a target, does
  nothing.

Expiry. A guest setpoint is usually an override that a controller holds until
you clear it. With ``expire_mode = "preset_none"`` the hub releases it after the
configured time by selecting the ``none`` preset, which is how RoomMind (and
other preset-based controllers) return to their schedule.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .adapters import ServiceCall
from .policy import EntityFacts, UNAVAILABLE_STATES
from .topics import climate_base

EXPIRE_NONE = "none"
EXPIRE_PRESET_NONE = "preset_none"
EXPIRE_MODES = (EXPIRE_NONE, EXPIRE_PRESET_NONE)

# What an MQTT climate entity accepts as an action.
HVAC_ACTIONS = frozenset({"off", "heating", "cooling", "drying", "idle", "fan"})

# ClimateEntityFeature bits.
FEATURE_TARGET_TEMPERATURE = 1
FEATURE_PRESET_MODE = 16


@dataclass(frozen=True, slots=True)
class ClimateGrant:
    """One synthetic thermostat: a hub climate entity granted to a child."""

    unit: str
    obj: str                       # room slug used in the topics
    entity_id: str                 # the hub climate entity
    min_temp: float
    max_temp: float
    current_sensor: str | None = None   # optional override of current_temperature
    target_sensor: str | None = None    # optional override of temperature
    expire_after_minutes: int = 0
    expire_mode: str = EXPIRE_NONE

    @property
    def key(self) -> tuple[str, str]:
        return (self.unit, self.obj)

    @property
    def base(self) -> str:
        return climate_base(self.unit, self.obj)

    def watched(self) -> frozenset[str]:
        """Every entity whose state change should republish this thermostat."""
        return frozenset(
            e for e in (self.entity_id, self.current_sensor, self.target_sensor) if e
        )


def _number(value: Any) -> float | None:
    """A finite float, or None (bools and unavailable-like strings are not numbers)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in UNAVAILABLE_STATES | {""}:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _feature(facts: EntityFacts, bit: int) -> bool:
    try:
        return (int(facts.attributes.get("supported_features") or 0) & bit) == bit
    except (TypeError, ValueError):
        return False


def _fmt(value: float) -> str:
    """Compact numeric payload: 71.0 -> "71", 71.5 -> "71.5"."""
    return str(int(value)) if float(value).is_integer() else str(value)


# ---------------------------------------------------------------- state values
def current_temperature(facts: EntityFacts, sensor_state: str | None = None) -> float | None:
    if sensor_state is not None:
        return _number(sensor_state)
    return _number(facts.attributes.get("current_temperature"))


def target_temperature(facts: EntityFacts, sensor_state: str | None = None) -> float | None:
    if sensor_state is not None:
        return _number(sensor_state)
    return _number(facts.attributes.get("temperature"))


def hvac_action(facts: EntityFacts) -> str | None:
    action = facts.attributes.get("hvac_action")
    if isinstance(action, str) and action.lower() in HVAC_ACTIONS:
        return action.lower()
    return None


def is_available(facts: EntityFacts) -> bool:
    return facts.state not in UNAVAILABLE_STATES


def state_payloads(
    grant: ClimateGrant,
    facts: EntityFacts,
    *,
    current_sensor_state: str | None = None,
    target_sensor_state: str | None = None,
) -> dict[str, str]:
    """topic -> payload for every value that currently has one. A value that is
    unknown is simply not published (the child keeps its last good value)."""
    base = grant.base
    out: dict[str, str] = {
        f"{base}/availability": "online" if is_available(facts) else "offline"
    }
    if not is_available(facts):
        return out
    cur = current_temperature(facts, current_sensor_state if grant.current_sensor else None)
    tgt = target_temperature(facts, target_sensor_state if grant.target_sensor else None)
    action = hvac_action(facts)
    if cur is not None:
        out[f"{base}/current"] = _fmt(cur)
    if tgt is not None:
        out[f"{base}/target/state"] = _fmt(tgt)
    if action is not None:
        out[f"{base}/action"] = action
    return out


# ------------------------------------------------------------------- discovery
def discovery_payload(
    grant: ClimateGrant,
    facts: EntityFacts,
    hub_status_topic: str,
    temperature_unit: str,
) -> dict[str, Any]:
    """MQTT climate discovery. ``temperature_unit`` is "C" or "F", the hub's
    unit system, in which every published value is expressed."""
    base = grant.base
    d: dict[str, Any] = {
        "name": facts.friendly_name,
        "unique_id": f"{grant.unit}_climate_{grant.obj}",
        "temperature_unit": temperature_unit,
        "min_temp": grant.min_temp,
        "max_temp": grant.max_temp,
        "temp_step": 1,
        # The guest sets a target and nothing else.
        "modes": ["auto"],
        "temperature_command_topic": f"{base}/target/set",
        "temperature_state_topic": f"{base}/target/state",
        "current_temperature_topic": f"{base}/current",
        "availability": [{"topic": hub_status_topic}, {"topic": f"{base}/availability"}],
        "availability_mode": "all",
    }
    if "hvac_action" in facts.attributes:
        d["action_topic"] = f"{base}/action"
    return d


# -------------------------------------------------------------------- commands
def effective_range(grant: ClimateGrant, facts: EntityFacts) -> tuple[float, float] | None:
    """The grant's range narrowed to the entity's own limits; None if empty."""
    lo, hi = grant.min_temp, grant.max_temp
    e_lo, e_hi = _number(facts.attributes.get("min_temp")), _number(facts.attributes.get("max_temp"))
    if e_lo is not None:
        lo = max(lo, e_lo)
    if e_hi is not None:
        hi = min(hi, e_hi)
    return (lo, hi) if lo <= hi else None


def command_call(grant: ClimateGrant, facts: EntityFacts, payload: Any) -> ServiceCall | None:
    """Validate a setpoint command; None means ignore it."""
    if not isinstance(payload, str) or facts.domain != "climate":
        return None
    if not is_available(facts) or not _feature(facts, FEATURE_TARGET_TEMPERATURE):
        return None
    value = _number(payload)
    limits = effective_range(grant, facts)
    if value is None or limits is None:
        return None
    clamped = max(limits[0], min(limits[1], value))
    return ServiceCall("climate", "set_temperature", facts.entity_id, {"temperature": clamped})


def expiry_call(grant: ClimateGrant, facts: EntityFacts) -> ServiceCall | None:
    """The call that releases a guest override, or None if there is none to make."""
    if grant.expire_mode != EXPIRE_PRESET_NONE:
        return None
    presets = facts.attributes.get("preset_modes") or []
    if "none" not in presets or not _feature(facts, FEATURE_PRESET_MODE) or not is_available(facts):
        return None
    return ServiceCall("climate", "set_preset_mode", facts.entity_id, {"preset_mode": "none"})
