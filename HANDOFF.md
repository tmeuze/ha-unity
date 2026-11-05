# Handoff: ha-unity (for the next Claude Code session)

Read `README.md` first (what it is and how users install it), then this file.
Everything here was checked against the repo and real Home Assistant behaviour
on 2026-10-06 unless marked **UNVERIFIED**. Do not claim a feature works "live"
unless it is in the verified-live list below.

## What this is
Naming: the project is called **Unity** in prose and display names; `ha-unity` is only the repo slug, file names, URLs and the MQTT topic prefix.

A HACS integration (0.2.0, hub role built; the earlier blueprints are legacy)
that publishes selected entities from one **hub** instance to other instances
(**children**, also called units) over MQTT discovery, driven by labels,
floors and areas set in the hub's UI, and takes validated commands back. The
integration owns all MQTT; users create no automations. Fail closed:
an entity is exposed only if (unit label AND area on a unit floor) OR (common
label AND area on a common floor), and its domain is on a fixed list.

- Repo: `github.com/tmeuze/ha-unity`. The integration lives on branch
  `feat/integration` until the owner approves merging to `main` (HACS pulls
  the default branch).
- Formerly named **PropSense**. See "Rename" below for what that changed.
- Version: integration `0.2.0`, **not yet run live**; blueprints were `0.1.0`.
  No git tags yet.
- License: MIT. Git identity is the owner's; commits end with the
  `Co-Authored-By: Claude ...` trailer the session reminder specifies. **Only
  push when the user asks.**

## Layout
```
custom_components/unity/   the HACS integration (0.2.0, hub role)
  policy.py topics.py adapters.py   pure Python, no HA imports
  snapshot.py manager.py            registries -> facts; the MQTT plane
  config_flow.py __init__.py diagnostics.py const.py strings.json translations/
blueprints/automation/ha_unity/     LEGACY blueprints (climate still lives here)
docs/            architecture, security-model, migration, blueprints*, emqx-acl-example.conf
tests/           test_policy/topics/adapters (pure) + integration/ (HA harness)
                 policy_model.py + render_blueprints.py = legacy blueprint suites
hacs.json  .github/workflows/validate.yml  requirements-test.txt  pyproject.toml
```

## Run the tests (pyenv virtualenv `ha-unity`, see ~/.claude/CLAUDE.md)
```bash
pyenv local ha-unity                 # .python-version is gitignored
pip install -r requirements-test.txt
python -m pytest -q                  # 120+ tests: pure logic + HA integration
python tests/policy_model.py         # legacy blueprint suites
python tests/render_blueprints.py
```
Note: the harness installs a newer Home Assistant (2026.10.x) than the test
instances run (2026.9.x); `hacs.json` says 2026.9.0, which is not tested.

**Mutation habit (important):** every time you change a guard in `policy.py`, `topics.py`, `adapters.py` or `manager.py` (or a blueprint template), deliberately
break it (drop a clamp, remove a feature check, widen a domain) in a temp copy
and confirm a test fails. This caught real mistakes. The ad-hoc harness is not
committed yet: copy `BP/*.yaml` to a temp dir, apply one `str.replace`, point
`render_blueprints.BP` at it, run the `test_*` functions with stdout silenced,
and count `FAILS`. Adding it as `tests/mutations.py` is an open item.

**Honesty rule:** the template tests use stand-ins for HA's functions
(`label_entities`, `floor_areas`, `area_id`, `state_attr`, ...). They prove the
logic, not HA's behaviour. Keep the "Verified" claims in README and blueprint
headers accurate when you change what has been run live.

## Design decisions (do not re-propose without a reason)
- **Whitelist, strict label/floor pairs.** A unit label on a common-floor
  entity, or the common label on a unit floor, exposes nothing. The domain list
  is hardcoded and deliberately not an input (except `blocked_cover_classes`).
- **The `exposed` template must be identical** in discovery, state and command
  blueprints (domain lists differ: commands exclude sensors). The climate
  blueprint has its own `allowed` template with the same rule. Tests assert
  all agree with `tests/policy_model.py`; change the model first.
- **Commands are validated on the hub every time**: entity comes from the
  topic, never the payload; services are hardcoded; values clamped; features
  checked against the hub entity's `supported_features`/modes.
- **One JSON state topic and one JSON command topic per entity** (fans, covers,
  lights), converted by templates on the unit. This keeps commands on the
  four-level topic `<unit>/<domain>/<object>/set` so the broker ACL needs only
  `<unit>/+/+/set` (plus `<unit>/climate/+/+/set`).
- **Rejected:** `mqtt_statestream`/`eventstream` (single base topic, no
  validated command path), `remote_homeassistant` (broad credential on the
  parent), per-device renaming for access control (names carry no policy; area
  and label do), and a `last_triggered` trick for override expiry (it fires on
  every trigger; the shipped design is `wait_for_trigger` in parallel mode).
- **Direction (decided 2026-11-05, supersedes the earlier "narrow component"
  note): Unity becomes a HACS custom integration in this repo** (hub and child
  roles, MQTT communication plane, child falls back to its last known-good
  config, dashboards and resource manifests later), built once tested. The
  blueprints stay as the lightweight option and the behavioural reference.
  **PropSense is a distinct project** for single-instance users; Unity may use
  it as engine/dependency or become an extension of it, undecided. Full design
  and open questions: `docs/architecture.md`. Do not start building before the
  spikes in that document are done.
- **Excluded on purpose:** locks, alarm panels, cameras, scripts. Media players
  have no MQTT platform, so they are unsupported, not merely unbuilt.
- **Scenes are a privilege**: their contents are not checked and may touch
  other units' devices. Documented in `docs/security-model.md`.

## What is verified live vs not
Verified on a real hub plus two real units (HA 2026.9.x, EMQX 6.x), by the
author, in October 2026:
- Discovery, state and availability reach the right unit only; a common-floor
  entity reaches both and mirrors state; on/off commands travel unit -> hub ->
  real light -> other unit.
- Negative tests: a command for another unit's entity is ignored by the hub
  bridge; a publish to another unit's topic is denied by the broker ACL.
- HA's real template functions behave as assumed: `label_entities`,
  `floor_areas`, `area_id`, `area_name`, `device_id`, `device_attr`,
  `regex_replace`, `match`.
- `suggested_area` makes a unit adopt an existing area or create a floorless one.
- The hub heartbeat (retained `online`) is what makes unit entities available.
- Existing automations can be repointed at these blueprints with identical
  published payloads.

**Also verified live, 2026-10-07** (a unit -> hub -> real devices, HA 2026.9.4):
- Light RGB colour, effect (an RGB light with 216 effects) and brightness, with the new
  state arriving back on the unit in about 1 s; restored afterwards.
- Light colour temperature (3000 K, 5000 K) and xy colour on a colour-temperature
  light, including the colour-mode switch; restored afterwards.
- Fan: percentage, preset mode, oscillation, turn off/on, and the state-topic
  sharing across several `*_state_topic` keys (an air circulator).
- Hub heartbeat retriggers on the MQTT birth message after the will fires.
Lesson: read live state over REST, not a browser tab's cached `hass.states`; a
tab whose websocket dropped showed hours-old data and led to a wrong "restore".

**Not run live (offline tests only): UNVERIFIED on real HA/MQTT**
- Scenes and covers (whether MQTT covers accept `null` payloads to hide
  buttons as documented). The test hub has no scenes or covers.
- The climate blueprint, including target/mode sensors and override expiry
  (needs a safe climate entity and a deployed automation).
- Custom `hub_status_topic` and `area_prefix_regex` inputs are in real use on
  the test hub, and a changed `hub_status_topic` (availability topic) was
  picked up by existing unit entities after rediscovery.

## Pitfalls already paid for (read before editing)
- **Replacing a blueprint file does not change running automations** until
  `automation.reload` (or a restart). Re-importing a blueprint needs the same.
- **HA restores the old entity id** when a deleted MQTT entity returns with the
  same unique id. Rename ids by hand; removing the device alone does not fix it.
- **Area names are unique per instance.** A hub holding several units cannot use
  bare "Kitchen" twice, hence the optional prefix-stripping regex.
- **MQTT entities with a device are named "device + entity name"**, which
  doubled names. Discovery sends `name: null` when the entity is the device,
  else the friendly name minus the device-name prefix.
- **Optional entities cannot be state-trigger inputs** (an empty `entity_id` is
  invalid), so the climate blueprint triggers on `state_changed` and filters.
- **Jinja scoping:** `{% set %}` inside a `for` loop does not escape it; use a
  `namespace`. A `{{ }}` inside a quoted string within `{% %}` is fine.
- **A scene's state is `unknown` until first used**, which is available. MQTT
  scenes accept no `state_topic`, so the shared payload base has none and each
  branch adds it.
- **HA has one birth topic.** Z2M-style consumers need `homeassistant/status`,
  so the retained `online` comes from `examples/hub_heartbeat.yaml` and the
  will message (retain on) supplies `offline`.
- **Broker ACL denials of publishes are silent** (`deny_action = ignore`); test
  denials explicitly. Children also need publish on their own `<unit>/status`
  for birth/will.
- A state blueprint on a busy hub needs a large queue (`max: 200`); a small one
  drops real updates during bursts.

## Adding a domain (checklist)
1. `ha_unity_publish_discovery.yaml`: add to `domains`, add a `choose` branch
   using documented MQTT keys (verify names against HA docs first; a wrong key
   silently rejects the entity).
2. `ha_unity_publish_state.yaml`: add to `domains`; add the state payload branch.
3. `ha_unity_command_bridge.yaml`: add to `domains`; add validated branches with
   hardcoded services, clamps and feature checks.
4. `tests/policy_model.py`: `DOMAINS`, fixture entities, bridge domain set.
5. `tests/render_blueprints.py`: attrs, discovery/state/command checks, then
   mutation-test each new guard.
6. Docs: README limits, `docs/how-it-works.md`, `docs/security-model.md` if it
   widens the attack surface, CHANGELOG.

## Rename (PropSense -> ha-unity) and what it broke
Commit `f2a47b6` ("Rename project from PropSense to ha-unity") is a pure rename
with one behaviour change, confirmed by reading its diff and by both test suites
passing afterwards. The user reported that an unrelated session accidentally
pushed a recent commit to this repo. The rename commit is the most recent and
the likely candidate, but git cannot confirm that (every commit uses the
owner's identity), so ask the user before assuming. Apart from it the history
is five commits from the building session.
- Blueprint directory/files: `propsense/propsense_*.yaml` -> `ha_unity/ha_unity_*.yaml`.
- **Default `hub_status_topic`: `propsense/hub/status` -> `ha-unity/hub/status`**
  (blueprints, `examples/hub_heartbeat.yaml`, `docs/emqx-acl-example.conf`).
  Anyone deployed under the old name must re-import, update the ACL and the
  heartbeat, or set `hub_status_topic` explicitly. Noted in `CHANGELOG.md`.
- `scripts/set_repo.py` default repo is now `ha-unity`; import links and
  `source_url` point at `tmeuze/ha-unity`.
- The only remaining "propsense" in tracked files is the CHANGELOG rename note.
- Imported blueprint paths on a user's HA are derived from the source URL;
  blueprints imported under the old name stay as separate, no-longer-updating
  files and automations still point at them until repointed.

## Open items, suggested order
1. **User re-imports** the discovery, state and command blueprints, reloads, and
   live-checks one light of each kind, one fan, one scene, one cover. Then move
   items from UNVERIFIED to verified and fix whatever real HA disagrees with.
2. **Climate on a real entity** (target sensor, mode sensor, clamp, expiry action).
3. Commit the mutation harness as `tests/mutations.py`; add a CI workflow that
   installs `pyyaml jinja2` and runs both suites.
4. Tag `v0.1.0` once 1 and 2 are done (`RELEASING.md`), remove "unreleased".
5. Known gaps: stale retained discovery when an entity is deleted from the hub
   entirely; cover tilt; light transitions; expiry countdowns lost on HA
   restart; the state blueprint's per-event filtering cost on very large hubs.
6. Follow the phases in `docs/architecture.md` (spikes next, then skeleton).

## Conventions
- No private names in this repo. Fixtures use `alpha`/`beta`/`shared`/`hub`.
  Before every push, scan tracked files for the owner's instance names,
  hostnames, IP addresses, device names and e-mail. Ask the user for the list;
  **do not write it into this repo** (a list of private names is itself a leak).
  The owner's GitHub name in URLs is expected.
- Blueprint headers say "Verified end to end on HA 2026.9.x"; that sentence is
  only true for the verified-live list above. Tighten it if you touch a header.
- Do not weaken fail-closed defaults to make a demo work.
