"""Unity: grant selected entities to child Home Assistant instances over MQTT.

The hub owns the whole MQTT side: configure it and its children in the UI and
the integration publishes, revokes and takes commands. See docs/architecture.md.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.components import mqtt

from .const import CONF_HUB_STATUS_TOPIC, DEFAULT_HUB_STATUS
from .manager import UnityHub, async_remove_hub_data

type UnityConfigEntry = ConfigEntry[UnityHub]


async def async_setup_entry(hass: HomeAssistant, entry: UnityConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("The MQTT integration is not connected")
    hub = UnityHub(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    # Subentries (children) and options changes reload the entry.
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UnityConfigEntry) -> bool:
    return True


async def async_remove_entry(hass: HomeAssistant, entry: UnityConfigEntry) -> None:
    """Deleting the hub clears everything it published to the children."""
    await async_remove_hub_data(hass, entry.data.get(CONF_HUB_STATUS_TOPIC) or DEFAULT_HUB_STATUS)


async def _reload(hass: HomeAssistant, entry: UnityConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
