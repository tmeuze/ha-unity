# Blueprints (legacy)

The blueprints in `blueprints/automation/ha_unity/` were the first
implementation. The integration supersedes them for lights, switches, sensors,
binary sensors, fans, scenes and covers. They remain because:

- the **synthetic climate** blueprint is not in the integration yet;
- they are the behavioural reference the integration's tests were ported from.

They are no longer the supported way to run Unity. Do not run them alongside the
integration for the same children.

## Install (climate only)

Import the blueprint on the hub:

[![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftmeuze%2Fha-unity%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fha_unity%2Fha_unity_climate.yaml)

Synthetic climate is the least-tested part: its policy, clamp and payload logic
are tested offline, but the call into your climate entity is not run live, and
guest-override expiry is lost on a Home Assistant restart.

## Other docs

- [How the blueprints work](blueprints-how-it-works.md)
- [Testing the blueprints](blueprints-testing.md)
- [EMQX ACL example](emqx-acl-example.conf) (still valid for the integration)
