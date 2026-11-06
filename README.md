# Unity

(Repository: `ha-unity`.)

Grant selected Home Assistant entities from one **hub** to other instances
(**children**), safely and automatically, driven by the labels, floors and
areas you already manage in the hub's UI. Unity is a HACS integration: you set
up the hub and its children in the UI and it handles all MQTT for you. There
are no automations to create and no blueprints to import.

Unity was built for a property with several guest-facing Home Assistant
instances that must see and control only their own devices, while everything
privileged (radios, locks, cloud accounts, controllers) stays on one hub.
Children need nothing installed except their ordinary MQTT integration.

> **Status: 0.3, young.** Lights, switches, sensors, fans and the hub status
> marker have run live on a hub with two children: publishing, the command path
> and a cross-child negative test. Light colour, colour temperature, xy,
> effects and fan controls were verified live in the earlier blueprint form that
> this code was ported from. Over 230 automated tests cover the rest. **Not yet
> run live:** thermostats (new in 0.3), scenes, covers, and recovery after an
> unclean MQTT disconnect. Read [docs/security-model.md](docs/security-model.md)
> before exposing anything that matters.

> **Not ha-propsense.** [ha-propsense](https://github.com/tmeuze/ha-propsense)
> is a separate project: a rule engine for single instances. Unity may use it
> as its engine later or become an extension of it; see
> [docs/architecture.md](docs/architecture.md).

## What it does

- **Whitelist, not blacklist.** An entity reaches a child only if it has the
  child's label **and** sits in an area on one of the child's floors, **or**
  has the common label and sits on a common floor. Unlabelled entities,
  entities with no area, anything on a floor you did not list, and a label on
  the wrong floor all expose nothing.
- **Event driven.** Discovery and state follow label, area, floor, device and
  entity changes and exposed-entity state changes (a 5-second debounce, plus a
  6-hour safety resync).
- **One door for commands.** A child publishes a command; the hub re-checks the
  whitelist against live registry data on **every** command and calls a
  hardcoded service. Nothing about the entity or service is taken from the
  payload, values are clamped, features must be advertised, and **retained
  commands are never executed**.
- **Thermostats.** Give a child a thermostat for one room, built from one hub
  climate entity (RoomMind works out of the box). The guest sees the current
  temperature, the target and what the system is doing, and can change **only
  the target**, inside a range you set. An optional timer ends the override
  afterwards.
- **Clean revocation.** When an entity stops qualifying, is deleted, or its
  child is removed, Unity clears the retained topics. What it published is
  persisted, so this works across restarts.
- **Self-healing availability.** Unity keeps the hub status marker `online`
  and re-announces it (and the retained data) every time MQTT reconnects, so
  the broker's will message can never leave the children stuck unavailable.
- **Rooms follow along.** Each entity carries a `suggested_area`, so the child
  puts it in the matching room (an optional regex strips a hub-side prefix).
  A device you renamed on the hub keeps its name on the child.

## Install

You need Home Assistant 2026.9 or newer on the hub, the MQTT integration set
up there, a broker all instances use, and per-instance broker users (see
[docs/emqx-acl-example.conf](docs/emqx-acl-example.conf)).

1. **HACS** → ⋮ → **Custom repositories** → add
   `https://github.com/tmeuze/ha-unity` as an **Integration** → download
   **Unity** → restart Home Assistant. Install it on the **hub only**.
2. **Settings → Devices & services → Add integration → Unity.** Set the
   common label and floors (optional), the hub status topic, the optional area
   prefix regex, and the blocked cover classes.
3. **Add child instance** (button at the top right of the Unity page) for each
   child: a unit name (lowercase; it becomes the first level of the child's
   MQTT topics, so it must match what the child and the broker use), its label
   and its floors.
4. **On each child**, set up the MQTT integration with discovery prefix
   `<unit>/ha` and birth and will messages on `<unit>/status`. On the **hub**,
   set the MQTT will message to `offline` on the hub status topic, retain on.
5. Label a test light on the hub, give it an area on the child's floor, and it
   appears on the child.
6. **Thermostats (optional).** **Add thermostat** on the Unity page: pick the
   child, the hub climate entity (for RoomMind, the **room** entity
   `climate.roommind_<room>`), the range, and whether to end the override
   after some minutes. The climate entity must carry the child's label and sit
   in an area on its floor, like anything else.

Coming from the blueprints? See [docs/migration.md](docs/migration.md).

## Documentation

- [How it works](docs/how-it-works.md): what is published when, the topics, a command step by step.
- [Security model](docs/security-model.md): the guarantees, and what you must still do.
- [Testing](docs/testing.md): the offline suite, and a checklist for a real hub.
- [Architecture](docs/architecture.md): the design, decisions and phases.
- [Migrating from the blueprints](docs/migration.md).
- [Releasing](RELEASING.md).

## Limits you should know

- Lights (colour, colour temperature, xy, effects), switches, sensors, binary
  sensors, fans, scenes, covers, and thermostats. **No locks, alarm panels,
  cameras or scripts**, on purpose. Media players have no MQTT equivalent.
- Lights carry on/off, brightness, colour temperature, colour (hs, xy, rgb;
  `rgbw`/`rgbww` appear as rgb) and effects. Transitions and white-channel
  control are not carried. Covers carry open, close, stop and position (no
  tilt).
- **A scene is exposed as a button, but its contents are the privilege.**
  Activating it can change any entity the scene touches, including devices
  that belong to other children; Unity cannot check that. Only label scenes
  whose contents are safe for that child.
- Covers of the classes garage, gate and door are blocked by default. A cover
  whose state is unavailable is never exposed.
- A **thermostat** gives the guest a target and nothing else: no mode, no
  preset, no on/off. Values use the hub's temperature unit.
- Command handling is a concurrency cap (10 at a time), not a per-child quota.

## Roadmap

See [docs/architecture.md](docs/architecture.md): a child-side component with
last-known-good fallback, dashboards and their frontend resources, and how
Unity relates to ha-propsense.

## Development

```bash
pyenv virtualenv 3.14.9 ha-unity && pyenv local ha-unity
pip install -r requirements-test.txt
python -m pytest -q
```

`policy.py`, `adapters.py`, `climate.py` and `topics.py` are pure Python with no
Home Assistant imports; `snapshot.py`, `manager.py` and `climate_runtime.py`
are the glue.

## License

MIT. See [LICENSE](LICENSE).
