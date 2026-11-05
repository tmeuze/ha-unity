"""Constants for the Unity integration."""
from __future__ import annotations

from .policy import DEFAULT_BLOCKED_COVER_CLASSES
from .topics import DEFAULT_HUB_STATUS_TOPIC

DOMAIN = "unity"

# Hub config entry data.
CONF_HUB_STATUS_TOPIC = "hub_status_topic"
CONF_COMMON_LABEL = "common_label"
CONF_COMMON_FLOORS = "common_floors"
CONF_BLOCKED_COVER_CLASSES = "blocked_cover_classes"
CONF_AREA_PREFIX = "area_prefix_regex"

# Child (subentry) data.
CONF_UNIT = "unit"
CONF_UNIT_LABEL = "unit_label"
CONF_UNIT_FLOORS = "unit_floors"
SUBENTRY_CHILD = "child"

DEFAULT_HUB_STATUS = DEFAULT_HUB_STATUS_TOPIC
DEFAULT_BLOCKED = sorted(DEFAULT_BLOCKED_COVER_CLASSES)
COVER_CLASSES = [
    "awning", "blind", "curtain", "damper", "door", "garage",
    "gate", "shade", "shutter", "window",
]

# Registry changes arrive in bursts; wait for quiet before resyncing.
RESYNC_DEBOUNCE_SECONDS = 5
# Safety net only: everything else is event driven.
RESYNC_INTERVAL_HOURS = 6
STORAGE_VERSION = 1
