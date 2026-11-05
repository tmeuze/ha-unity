# Unity

(Repository: `ha-unity`.)

Publish selected Home Assistant entities from one **hub** instance to other
instances (**units**), safely and automatically, driven by labels, floors and
areas you already manage in the hub's UI.

Unity was built for a property with several guest-facing Home Assistant
instances that must see and control only their own devices, while everything
privileged (radios, locks, cloud accounts, controllers) stays on one hub. It
is a set of blueprints over plain MQTT discovery. It is not a custom
integration, and nothing is installed on the units except their ordinary MQTT
integration.

> **Status: pre-1.0.** Verified live on Home Assistant 2026.9.x with an EMQX
> broker: discovery, state, availability, on/off commands for lights and
> switches, light colour (RGB, colour temperature, xy), effects and brightness, fans (speed, preset,
> oscillation, on/off), a shared common area, and negative tests. Scenes,
> covers and synthetic climate are tested offline only. Read
> [docs/security-model.md](docs/security-model.md) before exposing anything
> that matters.

> **Not ha-propsense.** Unity was briefly named "PropSense". That name now
> belongs to a separate project, ha-propsense, a HACS integration with a UI for
> label-driven rules. Unity stays a narrow component for syncing Home
> Assistant instances and is expected to rely on ha-propsense for tagging and
> rules later.

## What it does

- **Whitelist, not blacklist.** An entity reaches a unit only if it has the
  unit's label **and** sits in an area on one of the unit's floors, **or** has
  the common label and sits on a common floor. Unlabelled entities, entities
  with no area, anything on a floor you did not list, and a label on the wrong
  floor all expose nothing.
- **Event driven.** Discovery and state update when you change a label, area,
  floor, device or entity, or when an exposed entity changes. No polling
  (there is a 6-hour safety resync).
- **One door for commands.** A unit publishes a command; the hub re-checks the
  whitelist on every command and calls a hardcoded service. Nothing about the
  service or entity is taken from the payload.
- **Rooms follow along.** Each entity carries a `suggested_area`, so the unit
  puts it in the matching room (an optional regex strips a hub-side prefix).

## Install

You need: Home Assistant on the hub and each unit, an MQTT broker they all
use, and per-instance broker users (see
[docs/emqx-acl-example.conf](docs/emqx-acl-example.conf)).

1. **Import the blueprints on the hub** (one click each):

   | Blueprint | Import |
   |---|---|
   | Publish discovery | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftmeuze%2Fha-unity%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fha_unity%2Fha_unity_publish_discovery.yaml) |
   | Publish state | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftmeuze%2Fha-unity%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fha_unity%2Fha_unity_publish_state.yaml) |
   | Command bridge (lights, switches) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftmeuze%2Fha-unity%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fha_unity%2Fha_unity_command_bridge.yaml) |
   | Synthetic climate (optional) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftmeuze%2Fha-unity%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fha_unity%2Fha_unity_climate.yaml) |

   Updating later: open the blueprint in Settings, Automations & Scenes,
   Blueprints, and use **Re-import blueprint**. Then reload automations.
   (Replacing a blueprint file does not change running automations until they
   are reloaded.) Pin a release tag in the import URL for stable installs.

2. **On the hub**, create labels (for example `Live: Unit A`, `Live: Unit B`,
   `Live: Common`) and floors/areas, then create **three automations per unit**
   (discovery, state, command) from the blueprints. Pick the unit label and
   floors, and the common label and floors. Add
   [examples/hub_heartbeat.yaml](examples/hub_heartbeat.yaml) once.
3. **On each unit**, set up the MQTT integration with discovery prefix
   `<unit>/ha`, and birth and will messages on `<unit>/status` (not on
   `homeassistant/status`).
4. Label a test light on the hub, give it an area on the unit's floor, and it
   appears on the unit. [docs/testing.md](docs/testing.md) lists the checks.

## Documentation

- [How it works](docs/how-it-works.md): triggers, topics, what runs when.
- [Security model](docs/security-model.md): the guarantees, and what you must still do.
- [Testing](docs/testing.md): offline suite, live checks, negative tests.
- [Architecture (design, not built)](docs/architecture.md): the planned integration.
- [Releasing](RELEASING.md): tags, and how the import buttons get your owner and version.

## Limits you should know

- Lights (with colour and effects), switches, sensors, binary sensors, fans, scenes, covers, and (via the climate blueprint) a synthetic thermostat. **No locks, alarm panels, cameras or scripts**, on purpose. Media players have no MQTT equivalent and are not supported.
- The state blueprint filters every `state_changed` event. Fine for hundreds of
  entities; a different architecture would scale better.
- Deleting an entity from the hub entirely leaves a stale retained discovery
  topic on the broker.
- Lights support on/off, brightness, colour temperature, colour (hs, xy, rgb; `rgbw`/`rgbww` appear as rgb) and effects. Transitions and white-channel control are not carried. Covers carry open, close, stop and position (no tilt).
- **A scene is exposed as a button, but its contents are the privilege.** Activating it can change any entity the scene touches, including devices that belong to other units; Unity cannot check that. Only label scenes whose contents are safe for that unit.
- Covers of the classes garage, gate and door are blocked by default (an input). A cover whose state is unavailable is never exposed.
- Synthetic climate is the least-tested blueprint: its policy, clamp and payload logic are tested offline, but the call into your climate entity is not, and guest-override expiry is lost on a Home Assistant restart.

## Roadmap
The blueprints are the tested baseline. The plan is a HACS custom integration
in this same repository, with a hub role and a child role, a versioned MQTT
communication plane, and a child that falls back to its last known-good
configuration when a sync fails. Dashboards and their frontend resources are a
later phase. PropSense (a separate project for single-instance users) may become
Unity's rules engine, or Unity may become an extension of it; that is undecided.
Nothing of this exists yet. See [docs/architecture.md](docs/architecture.md).

## Tests

```bash
python tests/policy_model.py        # standard library only
python tests/render_blueprints.py   # needs PyYAML and Jinja2
```

The second renders the real blueprint templates against stubbed Home Assistant
functions. It cannot prove HA's own behaviour; see [docs/testing.md](docs/testing.md).

## License

MIT. See [LICENSE](LICENSE).
