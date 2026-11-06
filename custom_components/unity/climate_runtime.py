"""The climate side of the hub: publishes thermostats and takes setpoints.

One :class:`ClimateController` per hub. It mirrors what ``UnityHub`` does for
entities, for the synthetic thermostats described in ``climate.py``:

* publishes retained discovery and values for every granted thermostat, and
  revokes (retained empty payloads) when a grant, a child or the climate
  entity's label or floor goes away. What was published is persisted;
* takes ``<unit>/climate/<room>/target/set``: the grant comes from the topic,
  the policy is re-checked on every command, the value is clamped, and the one
  service it can reach is ``climate.set_temperature`` on the granted entity.
  Retained commands are never executed;
* releases a guest override after the configured time. The deadline is
  persisted, so a restart neither forgets it nor extends it: one that passed
  while Home Assistant was down is released at startup.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import climate, snapshot
from .adapters import ServiceCall
from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_CURRENT_SENSOR,
    CONF_EXPIRE_AFTER,
    CONF_EXPIRE_MODE,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OBJECT,
    CONF_TARGET_SENSOR,
    CONF_UNIT,
    SUBENTRY_CLIMATE,
)
from .policy import ChildPolicy, EntityFacts, FloorAreas, is_climate_granted
from .topics import (
    climate_command_filter,
    climate_discovery_topic,
    parse_climate_command_topic,
    valid_object,
    valid_unit,
)

if TYPE_CHECKING:
    from .manager import UnityHub

_LOGGER = logging.getLogger(__name__)

_CLIMATE = frozenset({"climate"})
GrantKey = tuple[str, str]


def grants_from(entry: ConfigEntry, children: dict[str, ChildPolicy]) -> dict[GrantKey, climate.ClimateGrant]:
    """The configured thermostats. Anything invalid is skipped (fail closed)."""
    out: dict[GrantKey, climate.ClimateGrant] = {}
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_CLIMATE:
            continue
        d = sub.data
        unit, obj, entity = d.get(CONF_UNIT, ""), d.get(CONF_OBJECT, ""), d.get(CONF_CLIMATE_ENTITY, "")
        try:
            lo, hi = float(d[CONF_MIN_TEMP]), float(d[CONF_MAX_TEMP])
            minutes = int(d.get(CONF_EXPIRE_AFTER) or 0)
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring climate grant with invalid numbers: %s", sub.title)
            continue
        mode = d.get(CONF_EXPIRE_MODE) or climate.EXPIRE_NONE
        if (
            unit not in children or not valid_unit(unit) or not valid_object(obj)
            or not entity.startswith("climate.") or not lo < hi or minutes < 0
            or mode not in climate.EXPIRE_MODES or (unit, obj) in out
        ):
            _LOGGER.warning("Ignoring invalid or duplicate climate grant: %s", sub.title)
            continue
        out[(unit, obj)] = climate.ClimateGrant(
            unit=unit, obj=obj, entity_id=entity, min_temp=lo, max_temp=hi,
            current_sensor=d.get(CONF_CURRENT_SENSOR) or None,
            target_sensor=d.get(CONF_TARGET_SENSOR) or None,
            expire_after_minutes=minutes, expire_mode=mode,
        )
    return out


class ClimateController:
    """Synthetic thermostats for the children of one hub."""

    def __init__(self, hub: UnityHub) -> None:
        self.hub = hub
        self.hass: HomeAssistant = hub.hass
        self.grants = grants_from(hub.entry, hub.children)
        # unit -> room slugs whose discovery is currently published
        self._published: dict[str, set[str]] = {}
        self.granted: set[GrantKey] = set()
        self._deadlines: dict[GrantKey, float] = {}
        self._timers: dict[GrantKey, CALLBACK_TYPE] = {}
        unit = self.hass.config.units.temperature_unit
        self.temperature_unit = "F" if "F" in unit else "C"

    # --------------------------------------------------------- persistence
    def restore(self, published: dict[str, list[str]], deadlines: dict[str, float]) -> None:
        self._published = {u: set(objs) for u, objs in published.items()}
        for key, ts in deadlines.items():
            unit, _, obj = key.partition("/")
            try:
                self._deadlines[(unit, obj)] = float(ts)
            except (TypeError, ValueError):
                continue

    def serialise(self) -> tuple[dict[str, list[str]], dict[str, float]]:
        published = {u: sorted(o) for u, o in self._published.items() if o}
        expiry = {f"{u}/{o}": ts for (u, o), ts in self._deadlines.items()}
        return published, expiry

    # ----------------------------------------------------------- lifecycle
    async def async_subscribe(self) -> list[CALLBACK_TYPE]:
        unsubs: list[CALLBACK_TYPE] = []
        for unit in self.hub.children:
            unsubs.append(
                await mqtt.async_subscribe(
                    self.hass, climate_command_filter(unit), self._handler(unit), qos=0
                )
            )
        return unsubs

    def stop(self) -> None:
        """Cancel timers; the deadlines stay persisted."""
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    def watches(self, entity_id: str) -> bool:
        return any(entity_id in g.watched() for g in self.grants.values())

    def watched(self) -> set[str]:
        return {e for g in self.grants.values() for e in g.watched()}

    # ---------------------------------------------------------------- facts
    def _facts(self, grant: climate.ClimateGrant) -> EntityFacts | None:
        return snapshot.entity_facts(self.hass, grant.entity_id, domains=_CLIMATE)

    def _granted(self, grant: climate.ClimateGrant, facts: EntityFacts | None, floors: FloorAreas) -> bool:
        child = self.hub.children.get(grant.unit)
        return bool(facts and child and is_climate_granted(facts, child, self.hub.policy, floors))

    def _sensor_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state else "unavailable"

    # ------------------------------------------------------------ publishing
    async def async_resync(self, floors: FloorAreas) -> None:
        """Revoke what is no longer granted, then publish what is."""
        for unit, objs in list(self._published.items()):
            for obj in sorted(objs):
                if (unit, obj) not in self.grants:
                    await self._revoke(unit, obj)
                    objs.discard(obj)
            if not objs:
                del self._published[unit]

        self.granted = set()
        for key, grant in self.grants.items():
            facts = self._facts(grant)
            if not self._granted(grant, facts, floors):
                if grant.obj in self._published.get(grant.unit, set()):
                    await self._revoke(grant.unit, grant.obj)
                    self._published[grant.unit].discard(grant.obj)
                continue
            assert facts is not None
            self.granted.add(key)
            payload = climate.discovery_payload(grant, facts, self.hub.status_topic, self.temperature_unit)
            await self.hub.publish(climate_discovery_topic(grant.unit, grant.obj), _dumps(payload))
            self._published.setdefault(grant.unit, set()).add(grant.obj)
            await self._publish_states(grant, facts)

    async def _publish_states(self, grant: climate.ClimateGrant, facts: EntityFacts) -> None:
        values = climate.state_payloads(
            grant, facts,
            current_sensor_state=self._sensor_state(grant.current_sensor),
            target_sensor_state=self._sensor_state(grant.target_sensor),
        )
        for topic, payload in values.items():
            await self.hub.publish(topic, payload)

    async def _revoke(self, unit: str, obj: str) -> None:
        base = f"{unit}/climate/{obj}"
        topics = (
            climate_discovery_topic(unit, obj), f"{base}/current", f"{base}/target/state",
            f"{base}/action", f"{base}/availability",
        )
        for topic in topics:
            self.hub.forget(topic)
            await mqtt.async_publish(self.hass, topic, "", qos=0, retain=True)

    async def async_state_changed(self, entity_id: str, floors: FloorAreas) -> bool:
        """Republish affected thermostats. Returns True if a grant changed
        (so the caller should schedule a full resync)."""
        for key, grant in self.grants.items():
            if entity_id not in grant.watched():
                continue
            facts = self._facts(grant)
            now = self._granted(grant, facts, floors)
            if now != (key in self.granted):
                return True
            if now and facts is not None:
                await self._publish_states(grant, facts)
        return False

    # -------------------------------------------------------------- commands
    def _handler(self, unit: str) -> Callable[[ReceiveMessage], Any]:
        async def handle(msg: ReceiveMessage) -> None:
            await self.async_handle_command(unit, msg.topic, msg.payload, msg.retain)

        return handle

    async def async_handle_command(
        self, unit: str, topic: str, payload: Any, retained: bool = False
    ) -> ServiceCall | None:
        """Validate one setpoint command and run it. Returns the call made."""
        if retained or not isinstance(payload, str):
            return None                       # a retained command is never executed
        obj = parse_climate_command_topic(topic, unit)
        grant = self.grants.get((unit, obj)) if obj else None
        if grant is None:
            return None
        facts = self._facts(grant)
        if not self._granted(grant, facts, snapshot.floor_areas(self.hass)) or facts is None:
            _LOGGER.debug("Ignoring setpoint from %s for %s: not granted", unit, grant.entity_id)
            return None
        call = climate.command_call(grant, facts, payload)
        if call is None:
            return None
        if not await self._run(call, f"setpoint from {unit}"):
            return None
        if grant.expire_after_minutes > 0 and grant.expire_mode != climate.EXPIRE_NONE:
            self._arm(grant, grant.expire_after_minutes * 60.0)
        return call

    async def _run(self, call: ServiceCall, what: str) -> bool:
        async with self.hub.command_slots:
            try:
                await self.hass.services.async_call(
                    call.domain, call.service, {"entity_id": call.entity_id, **call.data}, blocking=True
                )
            except Exception:  # noqa: BLE001 - one failed call must not stop the hub
                _LOGGER.exception("%s failed: %s.%s on %s", what, call.domain, call.service, call.entity_id)
                return False
        return True

    # ---------------------------------------------------------------- expiry
    def _arm(self, grant: climate.ClimateGrant, delay: float) -> None:
        """(Re)start the release timer; a newer command restarts the countdown."""
        key = grant.key
        if key in self._timers:
            self._timers.pop(key)()
        self._deadlines[key] = dt_util.utcnow().timestamp() + delay

        async def fire(_now: Any) -> None:
            self._timers.pop(key, None)
            await self._expire(key)

        self._timers[key] = async_call_later(self.hass, delay, fire)
        self.hub.save_soon()

    async def _expire(self, key: GrantKey) -> None:
        self._deadlines.pop(key, None)
        self.hub.save_soon()
        grant = self.grants.get(key)
        if grant is None:
            return
        facts = self._facts(grant)
        if not self._granted(grant, facts, snapshot.floor_areas(self.hass)) or facts is None:
            return
        call = climate.expiry_call(grant, facts)
        if call is None:
            _LOGGER.debug("Override expiry for %s: nothing to release", grant.entity_id)
            return
        await self._run(call, "override release")

    async def async_arm_restored(self) -> None:
        """After a restart: re-arm pending deadlines, and release any that passed."""
        now = dt_util.utcnow().timestamp()
        for key, deadline in list(self._deadlines.items()):
            grant = self.grants.get(key)
            if grant is None:
                self._deadlines.pop(key, None)
                continue
            remaining = deadline - now
            if remaining <= 0:
                await self._expire(key)
            else:
                self._arm_existing(grant, deadline, remaining)

    def _arm_existing(self, grant: climate.ClimateGrant, deadline: float, remaining: float) -> None:
        key = grant.key

        async def fire(_now: Any) -> None:
            self._timers.pop(key, None)
            await self._expire(key)

        self._deadlines[key] = deadline
        self._timers[key] = async_call_later(self.hass, remaining, fire)


def _dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True)
