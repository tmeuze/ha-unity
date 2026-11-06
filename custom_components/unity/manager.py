"""The hub: publishes what each child may see and executes what it may do.

One :class:`UnityHub` per config entry. It owns the whole MQTT side, so the
user configures a hub and its children in the UI and creates no automations:

* retained discovery, state and availability for every entity a child may see
  (policy in ``policy.py``, payloads in ``adapters.py``);
* revocation: a retained empty payload when an entity stops qualifying, is
  deleted, or its child is removed. What was published is persisted, so stale
  retained topics are cleaned up even after a restart;
* commands: ``<unit>/<domain>/<object>/set``. The entity comes from the topic,
  the policy is re-checked against live registry data on every command, the
  service is hardcoded per domain, retained commands are never executed;
* the hub status marker (``online``), re-announced on every MQTT (re)connect so
  the broker's will message (``offline``) cannot leave children unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from . import adapters, snapshot
from .climate_runtime import ClimateController
from .const import (
    CONF_AREA_PREFIX,
    CONF_BLOCKED_COVER_CLASSES,
    CONF_COMMON_FLOORS,
    CONF_COMMON_LABEL,
    CONF_HUB_STATUS_TOPIC,
    CONF_UNIT,
    CONF_UNIT_FLOORS,
    CONF_UNIT_LABEL,
    DEFAULT_BLOCKED,
    DEFAULT_HUB_STATUS,
    DOMAIN,
    RESYNC_DEBOUNCE_SECONDS,
    RESYNC_INTERVAL_HOURS,
    STORAGE_VERSION,
    SUBENTRY_CHILD,
)
from .policy import (
    COMMAND_DOMAINS,
    ChildPolicy,
    EntityFacts,
    HubPolicy,
    domain_of,
    exposed_set,
    is_exposed,
)
from .topics import (
    climate_discovery_topic,
    discovery_topic,
    entity_base,
    parse_command_topic,
    valid_hub_status_topic,
    valid_unit,
)

_LOGGER = logging.getLogger(__name__)

_REGISTRY_EVENTS = (
    er.EVENT_ENTITY_REGISTRY_UPDATED,
    dr.EVENT_DEVICE_REGISTRY_UPDATED,
    ar.EVENT_AREA_REGISTRY_UPDATED,
    fr.EVENT_FLOOR_REGISTRY_UPDATED,
    lr.EVENT_LABEL_REGISTRY_UPDATED,
)

# A child cannot flood the hub: at most this many commands run at once.
_MAX_CONCURRENT_COMMANDS = 10


@dataclass(frozen=True, slots=True)
class ChildStatus:
    """What the UI and diagnostics show for one child."""

    unit: str
    exposed: tuple[str, ...]
    climate: tuple[str, ...] = ()


def hub_policy_from(data: dict[str, Any]) -> HubPolicy:
    return HubPolicy(
        common_label=data.get(CONF_COMMON_LABEL) or None,
        common_floors=tuple(data.get(CONF_COMMON_FLOORS) or ()),
        blocked_cover_classes=frozenset(data.get(CONF_BLOCKED_COVER_CLASSES, DEFAULT_BLOCKED)),
    )


def children_from(entry: ConfigEntry) -> dict[str, ChildPolicy]:
    """The configured children. An invalid unit name is skipped (fail closed)."""
    out: dict[str, ChildPolicy] = {}
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_CHILD:
            continue
        unit = sub.data.get(CONF_UNIT, "")
        if not valid_unit(unit) or unit in out:
            _LOGGER.warning("Ignoring child with invalid or duplicate unit name %r", unit)
            continue
        out[unit] = ChildPolicy(
            unit=unit,
            unit_label=sub.data.get(CONF_UNIT_LABEL) or None,
            unit_floors=tuple(sub.data.get(CONF_UNIT_FLOORS) or ()),
        )
    return out


class UnityHub:
    """Publishes to, and takes commands from, the children of one hub."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.policy = hub_policy_from(entry.data)
        self.children = children_from(entry)
        topic = entry.data.get(CONF_HUB_STATUS_TOPIC) or DEFAULT_HUB_STATUS
        self.status_topic = topic if valid_hub_status_topic(topic) else DEFAULT_HUB_STATUS
        self.area_prefix: str = entry.data.get(CONF_AREA_PREFIX) or ""

        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.published")
        # unit -> entity ids whose discovery is currently published
        self._published: dict[str, set[str]] = {}
        # unit -> entity ids currently exposed (current view, for status/UI)
        self.exposed: dict[str, dict[str, EntityFacts]] = {}
        # topic -> last payload sent (skip identical repeats)
        self._sent: dict[str, str] = {}

        self._unsubs: list[CALLBACK_TYPE] = []
        self._state_unsub: CALLBACK_TYPE | None = None
        self._debounce: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self.command_slots = asyncio.Semaphore(_MAX_CONCURRENT_COMMANDS)
        self._stopped = False
        self.climate = ClimateController(self)

    # ------------------------------------------------------------------ life
    async def async_start(self) -> None:
        entities, climate_published, deadlines = parse_store(await self._store.async_load())
        self._published = {unit: set(ids) for unit, ids in entities.items()}
        self.climate.restore(climate_published, deadlines)

        for unit in self.children:
            self._unsubs.append(
                await mqtt.async_subscribe(
                    self.hass,
                    f"{unit}/+/+/set",
                    self._command_handler(unit),
                    qos=0,
                )
            )
        self._unsubs.extend(await self.climate.async_subscribe())
        for event_type in _REGISTRY_EVENTS:
            self._unsubs.append(self.hass.bus.async_listen(event_type, self._on_registry_event))
        self._unsubs.append(mqtt.async_subscribe_connection_status(self.hass, self._on_mqtt_status))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_interval, timedelta(hours=RESYNC_INTERVAL_HOURS)
            )
        )
        await self._announce_online()
        await self.async_resync(force=True)
        await self.climate.async_arm_restored()

    async def async_stop(self) -> None:
        self._stopped = True
        self.climate.stop()
        if self._debounce:
            self._debounce()
            self._debounce = None
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._serialise())

    # ------------------------------------------------------------- publishing
    async def _publish(self, topic: str, payload: str, *, force: bool = False) -> None:
        if not force and self._sent.get(topic) == payload:
            return
        self._sent[topic] = payload
        await mqtt.async_publish(self.hass, topic, payload, qos=0, retain=True)

    async def _announce_online(self) -> None:
        await mqtt.async_publish(self.hass, self.status_topic, "online", qos=0, retain=True)

    def _serialise(self) -> dict[str, Any]:
        published, expiry = self.climate.serialise()
        return {
            "entities": {unit: sorted(ids) for unit, ids in self._published.items() if ids},
            "climate": published,
            "expiry": expiry,
        }

    # Used by the climate controller (same retained-publish dedupe and save).
    async def publish(self, topic: str, payload: str, *, force: bool = False) -> None:
        await self._publish(topic, payload, force=force)

    def forget(self, topic: str) -> None:
        self._sent.pop(topic, None)

    def save_soon(self) -> None:
        self._store.async_delay_save(self._serialise, 1)

    def _facts_for(self, unit: str) -> dict[str, EntityFacts]:
        return self.exposed.get(unit, {})

    async def _revoke(self, unit: str, entity_id: str) -> None:
        """Empty retained payloads remove the entity from the child."""
        base = entity_base(unit, entity_id)
        for topic in (discovery_topic(unit, entity_id), f"{base}/state", f"{base}/availability"):
            self._sent.pop(topic, None)
            await mqtt.async_publish(self.hass, topic, "", qos=0, retain=True)

    async def _publish_state(self, unit: str, facts: EntityFacts, *, force: bool = False) -> None:
        base = entity_base(unit, facts.entity_id)
        await self._publish(f"{base}/availability", adapters.availability_payload(facts), force=force)
        payload = adapters.state_payload(facts)
        if payload is not None:
            await self._publish(f"{base}/state", payload, force=force)

    async def async_resync(self, *, force: bool = False) -> None:
        """Make the broker match the policy: revoke, then publish."""
        if self._stopped:
            return
        async with self._lock:
            floors = snapshot.floor_areas(self.hass)
            facts = snapshot.all_facts(self.hass)
            naming = adapters.NamingContext(
                area_prefix=self.area_prefix,
                common_area_names=snapshot.common_area_names(
                    self.hass, self.policy.common_floors, floors
                ),
            )
            if force:
                self._sent.clear()

            # Children that no longer exist: revoke everything they had.
            for unit in [u for u in self._published if u not in self.children]:
                for entity_id in sorted(self._published.pop(unit)):
                    await self._revoke(unit, entity_id)

            new_exposed: dict[str, dict[str, EntityFacts]] = {}
            for unit, child in self.children.items():
                wanted = exposed_set(facts, child, self.policy, floors)
                new_exposed[unit] = wanted
                have = self._published.setdefault(unit, set())
                for entity_id in sorted(have - wanted.keys()):
                    await self._revoke(unit, entity_id)
                    have.discard(entity_id)
                for entity_id, f in wanted.items():
                    payload = adapters.discovery_payload(unit, f, self.status_topic, naming)
                    if payload is None:
                        continue
                    await self._publish(
                        discovery_topic(unit, entity_id),
                        json.dumps(payload, sort_keys=True),
                    )
                    have.add(entity_id)
                    await self._publish_state(unit, f)
            self.exposed = new_exposed
            await self.climate.async_resync(floors)
            self._retrack_states()
            self.save_soon()

    # ------------------------------------------------------------ state events
    def _retrack_states(self) -> None:
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        tracked = {eid for per_unit in self.exposed.values() for eid in per_unit}
        tracked |= self.climate.watched()
        if tracked:
            self._state_unsub = async_track_state_change_event(
                self.hass, sorted(tracked), self._on_state_change
            )

    @callback
    def _on_state_change(self, event: Event) -> None:
        self.hass.async_create_task(self._handle_state_change(event.data["entity_id"]))

    async def _handle_state_change(self, entity_id: str) -> None:
        if self._stopped:
            return
        floors = snapshot.floor_areas(self.hass)
        if self.climate.watches(entity_id) and await self.climate.async_state_changed(entity_id, floors):
            self._schedule_resync()          # a thermostat's grant changed
        if not any(entity_id in per_unit for per_unit in self.exposed.values()):
            return
        facts = snapshot.entity_facts(self.hass, entity_id)
        if facts is None:
            self._schedule_resync()
            return
        for unit, child in self.children.items():
            was = entity_id in self._facts_for(unit)
            now = is_exposed(facts, child, self.policy, floors)
            if was != now:
                # e.g. a cover became unavailable: let the resync decide.
                self._schedule_resync()
                return
            if now:
                self.exposed[unit][entity_id] = facts
                await self._publish_state(unit, facts)

    # ----------------------------------------------------------------- resync
    @callback
    def _on_registry_event(self, event: Event) -> None:
        self._schedule_resync()

    @callback
    def _on_interval(self, _now: Any) -> None:
        self.hass.async_create_task(self.async_resync())

    @callback
    def _schedule_resync(self) -> None:
        if self._stopped:
            return
        if self._debounce:
            self._debounce()
        self._debounce = async_call_later(
            self.hass, RESYNC_DEBOUNCE_SECONDS, self._debounced_resync
        )

    async def _debounced_resync(self, _now: Any) -> None:
        self._debounce = None
        await self.async_resync()

    @callback
    def _on_mqtt_status(self, connected: bool) -> None:
        """Re-announce on every (re)connect: after an unclean disconnect the
        broker has published the will ("offline") and only we can undo it. A
        restarted broker may also have lost retained messages."""
        if connected and not self._stopped:
            self.hass.async_create_task(self._reannounce())

    async def _reannounce(self) -> None:
        await self._announce_online()
        await self.async_resync(force=True)

    # --------------------------------------------------------------- commands
    def _command_handler(self, unit: str) -> Callable[[ReceiveMessage], Any]:
        async def handle(msg: ReceiveMessage) -> None:
            await self.async_handle_command(unit, msg.topic, msg.payload, msg.retain)

        return handle

    async def async_handle_command(
        self, unit: str, topic: str, payload: Any, retained: bool = False
    ) -> list[adapters.ServiceCall]:
        """Validate one command and run it. Returns the calls made (for tests)."""
        child = self.children.get(unit)
        if child is None or retained or not isinstance(payload, str):
            return []  # a retained command is never executed
        entity_id = parse_command_topic(topic, unit)
        if entity_id is None or domain_of(entity_id) not in COMMAND_DOMAINS:
            return []
        facts = snapshot.entity_facts(self.hass, entity_id)
        if facts is None or not is_exposed(facts, child, self.policy, snapshot.floor_areas(self.hass)):
            _LOGGER.debug("Ignoring command from %s for %s: not exposed", unit, entity_id)
            return []
        calls = adapters.command_calls(facts, payload)
        async with self.command_slots:
            for call in calls:
                try:
                    await self.hass.services.async_call(
                        call.domain,
                        call.service,
                        {"entity_id": call.entity_id, **call.data},
                        blocking=True,
                    )
                except Exception:  # noqa: BLE001 - one bad command must not stop the rest
                    _LOGGER.exception(
                        "Command from %s failed: %s.%s on %s", unit, call.domain, call.service, call.entity_id
                    )
        return calls

    # ------------------------------------------------------------------ status
    def status(self) -> list[ChildStatus]:
        return [
            ChildStatus(
                unit=unit,
                exposed=tuple(sorted(self.exposed.get(unit, {}))),
                climate=tuple(sorted(obj for (u, obj) in self.climate.granted if u == unit)),
            )
            for unit in self.children
        ]


def parse_store(stored: dict[str, Any] | None) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, float]]:
    """Read the persisted state. 0.2.x stored just ``{unit: [entity ids]}``."""
    stored = stored or {}
    if any(k in stored for k in ("entities", "climate", "expiry")):
        return stored.get("entities") or {}, stored.get("climate") or {}, stored.get("expiry") or {}
    return {u: v for u, v in stored.items() if isinstance(v, list)}, {}, {}


async def async_remove_hub_data(hass: HomeAssistant, status_topic: str) -> None:
    """Called when the hub entry is deleted: clear everything we published."""
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.published")
    entities, climate_published, _ = parse_store(await store.async_load())
    if (entities or climate_published) and await mqtt.async_wait_for_mqtt_client(hass):
        for unit, ids in entities.items():
            for entity_id in ids:
                base = entity_base(unit, entity_id)
                for topic in (discovery_topic(unit, entity_id), f"{base}/state", f"{base}/availability"):
                    await mqtt.async_publish(hass, topic, "", qos=0, retain=True)
        for unit, objs in climate_published.items():
            for obj in objs:
                base = f"{unit}/climate/{obj}"
                for topic in (climate_discovery_topic(unit, obj), f"{base}/current", f"{base}/target/state",
                              f"{base}/action", f"{base}/availability"):
                    await mqtt.async_publish(hass, topic, "", qos=0, retain=True)
        await mqtt.async_publish(hass, status_topic, "offline", qos=0, retain=True)
    await store.async_remove()
