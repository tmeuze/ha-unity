# Handoff: ha-unity (for the next Claude Code session)

Read `README.md` first (what it is and how users install it), then this file.
Everything here was checked against the repo and real Home Assistant behaviour
unless marked **UNVERIFIED**. Do not claim a feature works "live"
unless it is in the verified-live list below.

## What this is
Naming: the project is called **Unity** in prose and display names; `ha-unity` is only the repo slug, file names, URLs and the MQTT topic prefix.

A HACS integration (0.3.0, hub role; the earlier blueprints were removed)
that publishes selected entities from one **hub** instance to other instances
(**children**, also called units) over MQTT discovery, driven by labels,
floors and areas set in the hub's UI, and takes validated commands back. The
integration owns all MQTT; users create no automations. Fail closed:
an entity is exposed only if (unit label AND area on a unit floor) OR (common
label AND area on a common floor), and its domain is on a fixed list.

- Repo: `github.com/tmeuze/ha-unity`. The integration is merged to `main`
  (HACS pulls the default branch). CI (hassfest, HACS, tests) is green.
- Formerly named **PropSense**. See "Rename" below for what that changed.
- Version `0.3.0` (tags `v0.2.0` exist; 0.2.1 is on `main` without a tag; 0.3.0
  is not yet merged or tagged when this was written: check `git log`).
- License: MIT. Git identity is the owner's; commits end with the
  `Co-Authored-By: Claude ...` trailer the session reminder specifies. **Only
  push when the user asks.**

## Layout
```
custom_components/unity/   the HACS integration (0.3.0)
  policy.py topics.py adapters.py climate.py   pure Python, no HA imports
  snapshot.py manager.py climate_runtime.py    registries -> facts; the MQTT plane
  config_flow.py __init__.py diagnostics.py const.py strings.json translations/ brand/
docs/            architecture, how-it-works, security-model, testing, migration, emqx-acl-example.conf
tests/           test_policy/topics/adapters/climate/acl_example (pure) + integration/ (HA harness)
hacs.json  .github/workflows/validate.yml  requirements-test.txt  pyproject.toml
```
The earlier blueprints, their script-style tests and the heartbeat example were
removed in 0.3.0 (history has them).

## Run the tests (pyenv virtualenv `ha-unity`, see ~/.claude/CLAUDE.md)
```bash
pyenv local ha-unity                 # .python-version is gitignored
pip install -r requirements-test.txt
python -m pytest -q                  # 230+ tests: pure logic + HA integration
```
The harness installs a newer Home Assistant than the test instances run;
`hacs.json` says 2026.9.0, which is not tested.

**Mutation habit (important):** every time you change a guard in `policy.py`,
`topics.py`, `adapters.py`, `climate.py`, `manager.py` or `climate_runtime.py`,
deliberately break it (drop a clamp, remove a policy re-check, delete the
retained-command check) in a temp edit and confirm a test fails, then restore.
Two survivors are known and equivalent: a redundant domain check in
`manager.py` and the overdue-deadline branch in `climate_runtime.py`.

**Honesty rule:** keep README "Status" accurate about what has run live.
**Read live state over REST, not a browser tab's cached `hass.states`**: a tab
whose websocket dropped showed hours-old data once and led to a wrong restore.

## Design decisions (do not re-propose without a reason)
- **Whitelist, strict label/floor pairs.** A child label on a common-floor
  entity, or the common label on a child floor, exposes nothing. The domain list
  is hardcoded and deliberately not configuration (except blocked cover classes).
  There is one implementation: `policy.py`.
- **Commands are validated on the hub every time**: the entity comes from the
  topic, never the payload; services are hardcoded per domain; values are
  clamped; features are checked against the entity's *current* attributes; the
  policy is re-read from live registries per command; **retained commands are
  never executed**.
- **One JSON state topic and one JSON command topic per entity** (fans, covers,
  lights), converted by templates on the child. Commands stay on the four-level
  topic `<unit>/<domain>/<object>/set` so the broker ACL needs only
  `<unit>/+/+/set` and `<unit>/climate/+/target/set`.
- **The integration owns all MQTT** (decided 2026-11-05). Entities stay on
  standard MQTT discovery, so a child works with no Unity component. A
  child-side component with last-known-good fallback is a later phase
  (`docs/architecture.md`).
- **Thermostats are an explicit grant, not an exposed domain.** One hub climate
  entity to one child, as a config subentry. Everything derives from the entity's
  attributes (RoomMind's room entity provides them; its `_override` entity is
  deprecated upstream). The guest changes only the target. Release of the
  override is an enum (`none` / `preset_none`), not a free-form action.
- **Rejected:** `mqtt_statestream`/`eventstream` (single base topic, no
  validated command path), `remote_homeassistant` (broad credential on the
  parent), per-device renaming for access control (names carry no policy; area
  and label do).
- **PropSense is a distinct project** for single-instance users. Current
  recommendation: Unity depends on it for selection through its extension API,
  hub side only (a child must not need it). Undecided; see the architecture doc.
- **Excluded on purpose:** locks, alarm panels, cameras, scripts. Media players
  have no MQTT platform, so they are unsupported, not merely unbuilt.
- **Scenes are a privilege**: their contents are not checked and may touch
  other children's devices. Documented in `docs/security-model.md`.

## What is verified live vs not
Verified live on a real hub and two real children (EMQX), by the author:
- **Blueprint era (HA 2026.9.x, Oct 2026), ported into the adapters:** discovery,
  state, availability, common-floor entities reaching both children; light RGB
  colour, colour temperature, xy, effects and brightness (about 1 s round trip,
  restored afterwards); fan percentage, preset, oscillation, on/off and several
  `*_state_topic` keys sharing one topic; negative tests (another child's entity
  ignored by the hub, cross-topic publish denied by the ACL); the hub marker as
  the availability gate; a custom status topic and area prefix regex.
- **Integration 0.2 (HA 2026.11.0, Nov 2026):** the hub entry with two children
  published live to both (counts matched exactly: three topics per entity, one
  marker); a no-op command from a child produced exactly one service call on the
  hub; a child's command for another child's entity produced none; deleting the
  old automations and blueprints left both children stable.

**UNVERIFIED on real HA/MQTT (offline tests only):**
- Thermostats (0.3): the child card with `modes: ["auto"]` and no mode topics,
  the setpoint round trip, and the `none`-preset release on RoomMind.
- Scenes and covers (including whether MQTT covers accept `null` payloads to hide
  buttons). The test hub has none.
- Recovery after an unclean MQTT disconnect (will message, then re-announce on
  reconnect). Covered by tests with a mocked client, not by a broker kick.

## Pitfalls already paid for (read before editing)
- **HA restores the old entity id** when a deleted MQTT entity returns with the
  same unique id. Rename ids by hand; removing the device alone does not fix it.
- **Area names are unique per instance.** A hub holding several children cannot
  use bare "Kitchen" twice, hence the optional prefix-stripping regex (trimmed,
  so a missing trailing space is harmless).
- **MQTT entities with a device are named "device + entity name"**, which
  doubled names. Discovery sends `name: null` when the entity is the device,
  else the friendly name minus the device-name prefix. The user-set device name
  (`name_by_user`) wins over the integration's default.
- **A scene's state is `unknown` until first used**, which is available. MQTT
  scenes accept no `state_topic`.
- **HA has one birth topic.** Z2M-style consumers need `homeassistant/status`,
  so Unity keeps the hub marker itself and the broker will supplies `offline`.
- **Broker ACL denials of publishes are silent** (`deny_action = ignore`); test
  denials explicitly. Children also need publish on their own `<unit>/status`.
  A unit name that is not in the ACL publishes into the void: a child unit
  entered as `x-name` while the children and ACL used `name` showed no error.
- **The label picker shows a red "Unknown item"** under a selected label in
  config dialogs (and in HA's own editors). Cosmetic; the stored id is right.
- **Test harness:** `async_fire_time_changed` is relative to the real clock, not
  cumulative, so "20 minutes then 40 more" fires at 40. MQTT's subscription
  batching leaves a timer behind (`expected_lingering_timers` is overridden in
  `tests/integration/conftest.py`), so Unity's own timers are asserted explicitly.
- **Browser automation:** a tab whose websocket dropped showed hours-old state.
  Read live state over REST (`callApi('GET','states/...')`), and screenshot a
  menu before clicking in it (a blind chained click once hit "Delete").

## Adding a domain (checklist)
1. `policy.py`: add the domain to `EXPOSED_DOMAINS` (and `COMMAND_DOMAINS` if it
   takes commands).
2. `adapters.py`: a discovery builder (documented MQTT keys only; a wrong key
   silently rejects the entity), the state payload, and a validated command
   handler with hardcoded services, clamps and feature checks. Register them in
   `_DISCOVERY`, `state_payload` and `_COMMANDS`.
3. `tests/test_adapters.py` and `tests/test_policy.py`: payloads, clamps,
   feature gates, unparseable input; `tests/integration/` for the end to end path.
4. Mutation-test each new guard (see above).
5. Docs: README limits, `docs/how-it-works.md`, `docs/security-model.md` if it
   widens the attack surface, CHANGELOG.

## Open items, suggested order
1. **Run thermostats live** on one room (`docs/testing.md`, Thermostats), then
   move them from UNVERIFIED. Likewise a scene and a cover if the hub gains one.
2. **Recovery after an unclean disconnect:** kick the hub's MQTT client in the
   broker and confirm `offline` then `online`.
3. Spikes for dashboards and HACS resources, then the child-side component
   (`docs/architecture.md`, phases 3 to 5).
4. Decide the PropSense relationship.
5. Known gaps: light transitions; cover tilt; a per-child command quota (there is
   only a concurrency cap); sensors chosen for a thermostat are not themselves
   policy-checked (they are published as plain temperatures).

## Conventions
- No private names in this repo. Fixtures use `alpha`/`beta`/`shared`/`hub`.
  Before every push, scan tracked files and history for the owner's instance
  names, hostnames, IP addresses, device and entity names and e-mail. Ask the
  user for the list; **do not write it into this repo** (a list of private names
  is itself a leak). The owner's GitHub name in URLs is expected. `git grep`
  skips untracked files: grep new files directly.
- Releases and tags are outward-facing; do them only when the user asks.
- Do not weaken fail-closed defaults to make a demo work.
