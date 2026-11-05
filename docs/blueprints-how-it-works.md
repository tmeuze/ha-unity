# How Unity works

> These notes describe the legacy blueprints. The integration is described in [architecture.md](architecture.md).

## Roles
- **Hub:** the Home Assistant instance that owns devices and integrations.
- **Unit:** a Home Assistant instance that only receives what the hub publishes.
- **Broker:** one MQTT broker all instances connect to.

## Topics (per unit, prefix `<unit>`)
```
<unit>/ha/<component>/<object_id>/config   retained discovery (unit discovery prefix: <unit>/ha)
<unit>/<domain>/<object_id>/state          retained state
<unit>/<domain>/<object_id>/availability   online / offline per entity
<unit>/<domain>/<object_id>/set            commands from the unit
<unit>/climate/<room>/target/set           climate setpoint (climate blueprint)
<unit>/status                              the unit's own birth and will
ha-unity/hub/status                       retained hub marker (input: hub_status_topic)
```
Entities are available on a unit only while the entity's own availability is
`online` **and** the hub marker reads `online`.

## What runs when (no polling)
| Blueprint | Triggers | Does |
|---|---|---|
| publish discovery | HA start; any entity, device, area, floor or label registry change (5 s debounce); every 6 h | Publishes discovery for exposed entities; clears it for entities that stopped qualifying |
| publish state | each `state_changed` of an exposed entity; the same registry events; every 6 h | Publishes retained state and availability |
| command bridge | MQTT message on `<unit>/+/+/set` | Re-checks the whitelist, then calls a hardcoded light or switch service |
| climate | registry events, state changes of the two entities, and its command topic | Publishes a synthetic thermostat; clamps and applies setpoints |

## Lights
The unit's light mirrors what the hub light can do, because the discovery
payload carries the hub light's `supported_color_modes`:

| Hub light modes | Unit gets |
|---|---|
| `onoff` (or none reported) | on/off |
| `brightness`, or only `white` | brightness |
| `color_temp` | colour temperature in kelvin, with the light's own min/max |
| `hs`, `xy` | that colour model |
| `rgb`, `rgbw`, `rgbww` | rgb (and brightness) |

State carries brightness, `color_mode`, colour temperature and colour. Commands
are clamped (brightness 1..255, colour temperature to the light's range, colour
to its valid range) and a colour or colour temperature is only accepted if the
hub light supports that model. A light that is currently off still advertises
its capabilities (they come from the entity, not from its current state).

## Fans, scenes, covers, light effects
All use one JSON state topic and one JSON command topic (`.../state`, `.../set`);
the unit converts with templates, so commands stay on the four-level topic the
ACL already allows.

| Domain | Unit gets | Commands (all feature-checked and clamped on the hub) |
|---|---|---|
| Fan | on/off, plus speed, preset modes, oscillation, direction only if the hub fan supports them | `state`, `percentage` 0 to 100, `preset_mode` (must be in the hub's list), `oscillating` (boolean), `direction` (forward or reverse) |
| Scene | a command-only entity (no state) | `ON` activates the scene |
| Cover | open, close, stop (null hides the button), position if supported; device class | `{"command": "open"|"close"|"stop"}`, `{"position": 0..100}`; tilt is not carried |
| Light effects | `effect_list` from the hub light | `effect` must be in the light's own list, otherwise it is dropped |

A scene shows as available while its state is `unknown` (never used yet).
Covers of the classes in `blocked_cover_classes` (default garage, gate, door)
and covers that are unavailable are never exposed.

## Climate (synthetic thermostat)
`ha_unity_climate` publishes one MQTT climate entity per room per unit, built
from hub entities, so a guest sees what your controller is actually doing and
not a thermostat's own display:

| Input | Becomes |
|---|---|
| Hub climate entity | The entity guest setpoints are applied to (`climate.set_temperature`); must pass the whitelist |
| Current temperature sensor | `current_temperature` |
| Target sensor (optional) | The shown target; if empty, the climate entity's `temperature` attribute |
| Mode sensor (optional) | The card's action: `heating`, `cooling`, `idle` or `off`; other states are not published |
| Min / max, unit | The guest range and `temperature_unit` (hub and unit must use the same system) |

**Expiry (optional).** With `expire_after_minutes` above 0, each guest setpoint
starts a countdown. A newer command restarts it; on timeout the `expire_action`
you supply runs (typically a service call that clears your controller's
override). The blueprint runs in parallel mode so the wait never blocks state
publishing. Pending countdowns are lost on a Home Assistant restart, and a
malformed newer command also cancels the previous countdown.

## The whitelist
Evaluated identically in all blueprints (keep the `exposed` template in sync):

```
exposed(unit) = domain in {light, switch, sensor, binary_sensor} and
  ( unit label   and area on one of the unit's floors
  or common label and area on one of the common floors )
```
An entity's area is its own area, or its device's area if it has none.
`tests/policy_model.py` is the reference model of this rule.

## Rooms
Each discovery payload includes a `device` block (identifier scoped to the
unit) and `suggested_area`. A unit adopts an existing area of that name, or
creates it (floorless) if missing. If `area_prefix_regex` matches the hub's
area name, the prefix is stripped. Areas on the common floors send their own
name unchanged. Anything else sends no suggested area.

## Names
MQTT entities that belong to a device are named "<device> <entity name>". To
avoid doubled names, the entity name is sent as `null` when the entity is the
device, and otherwise as its friendly name minus the device-name prefix.
