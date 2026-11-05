"""MQTT topic scheme for Unity. Pure; no Home Assistant imports.

The scheme is the one verified live and the one the broker ACL is written for:

    <unit>/ha/<component>/<object_id>/config   retained discovery (hub -> child)
    <unit>/<domain>/<object_id>/state          retained state     (hub -> child)
    <unit>/<domain>/<object_id>/availability   retained marker    (hub -> child)
    <unit>/<domain>/<object_id>/set            command            (child -> hub)
    <unit>/status                              the child's own birth/will

A child may publish only ``<unit>/+/+/set`` (and ``<unit>/status``), so the
hub's command subscription is exactly that pattern. The entity of a command
comes from the topic, never from the payload.
"""
from __future__ import annotations

import re

DEFAULT_HUB_STATUS_TOPIC = "ha-unity/hub/status"

# A unit name is a single, plain topic level.
_UNIT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_OBJECT_RE = re.compile(r"^[a-z0-9_]+$")
_RESERVED_UNITS = frozenset({"homeassistant", "ha-unity", "zigbee2mqtt"})


def valid_unit(unit: str) -> bool:
    """True for a lowercase, slash-free, wildcard-free topic level."""
    return bool(_UNIT_RE.match(unit)) and unit not in _RESERVED_UNITS


def valid_hub_status_topic(topic: str) -> bool:
    """A concrete topic: no wildcards, no empty levels, no leading slash."""
    return (
        bool(topic)
        and "+" not in topic
        and "#" not in topic
        and not topic.startswith("/")
        and "//" not in topic
        and not topic.endswith("/")
    )


def split_entity(entity_id: str) -> tuple[str, str] | None:
    """``("light", "kitchen")`` for ``light.kitchen``; None if malformed."""
    domain, _, obj = entity_id.partition(".")
    if not domain or not obj or not _OBJECT_RE.match(domain) or not _OBJECT_RE.match(obj):
        return None
    return domain, obj


def entity_base(unit: str, entity_id: str) -> str:
    """``<unit>/<domain>/<object_id>``."""
    parts = split_entity(entity_id)
    if parts is None:
        raise ValueError(f"invalid entity id: {entity_id!r}")
    return f"{unit}/{parts[0]}/{parts[1]}"


def discovery_topic(unit: str, entity_id: str) -> str:
    parts = split_entity(entity_id)
    if parts is None:
        raise ValueError(f"invalid entity id: {entity_id!r}")
    return f"{unit}/ha/{parts[0]}/{parts[1]}/config"


def command_filter(unit: str) -> str:
    """The hub's command subscription for one child."""
    return f"{unit}/+/+/set"


def parse_command_topic(topic: str, unit: str) -> str | None:
    """The entity id a command topic addresses, or None if it is not a valid
    ``<unit>/<domain>/<object_id>/set`` topic for this unit."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != unit or parts[3] != "set":
        return None
    domain, obj = parts[1], parts[2]
    if not _OBJECT_RE.match(domain) or not _OBJECT_RE.match(obj):
        return None
    return f"{domain}.{obj}"
