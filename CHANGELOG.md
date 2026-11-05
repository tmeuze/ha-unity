# Changelog

## 0.2.0
- New: Unity is a HACS integration. A hub config entry plus one subentry per
  child replaces the discovery, state and command-bridge automations and the
  heartbeat automation. The integration owns all MQTT.
- Policy, topic scheme, adapters and command validation ported to tested Python
  (`policy.py`, `topics.py`, `adapters.py`), with 120+ automated tests.
- Stale retained topics (deleted entity, removed child) are cleaned up; what
  was published is persisted.
- The hub status marker is re-announced on every MQTT reconnect.
- Retained commands are never executed. Unit names and the hub status topic are
  validated (no wildcards or slashes).
- Diagnostics show what each child may see.
- Not yet in the integration: climate (still a blueprint). Not yet run live.
- Docs: README rewritten, migration guide, blueprints kept as legacy.

## 0.1.0 (unreleased)
- Fix: discovery now uses the device name you set on the hub (name_by_user) instead of the integration's default name (e.g. a Matter device's default). Two new render tests cover it.
- Docs: added docs/architecture.md (planned integration design) and rewrote the README roadmap.
- Verified live: light RGB colour, colour temperature, xy, effects and brightness; fan speed, preset, oscillation and on/off. Docs updated.
- Example hub heartbeat also triggers on the MQTT birth message, so the retained "online" is restored after the broker publishes the will (previously only on HA start).
- Docs: status now states exactly what was verified live; README notes Unity is not ha-propsense and that rules/UI belong to that project. Blueprint headers no longer claim end-to-end verification for everything.
- Renamed the project from PropSense to ha-unity. Blueprints now live in `blueprints/automation/ha_unity/` as `ha_unity_*.yaml`, and the default hub status topic is `ha-unity/hub/status` (was `propsense/hub/status`). If you already deployed: re-import the blueprints, update your broker ACL and the hub heartbeat to the new topic, or set `hub_status_topic` explicitly to keep the old one.
- Blueprints: publish discovery, publish state, command bridge (lights, switches), synthetic climate.
- Whitelist: unit label + unit floors, or common label + common floors; allowlisted domains only.
- Optional `area_prefix_regex` and `hub_status_topic` inputs.
- Climate: optional target sensor, optional mode sensor (hvac action), `temperature_unit` input, optional guest-override expiry with a user-supplied action; runs in parallel mode; state changes filtered from `state_changed`.
- Lights: capabilities advertised from `supported_color_modes`; colour temperature and colour (hs, xy, rgb) in state and commands, clamped and mode-checked. Fixes colour-capable lights that were off being advertised as on/off only.
- Fans (speed, presets, oscillation, direction), scenes, covers (open, close, stop, position; garage/gate/door blocked by default) and light effects; command-only scenes publish no state topic and read as available while `unknown`.
- Offline test suites (policy model and rendered templates); example broker ACL; hub heartbeat example.
