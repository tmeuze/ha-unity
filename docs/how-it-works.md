# How Unity works

Unity runs on the **hub** only. Children need nothing but their ordinary MQTT
integration. Everything below happens inside the integration; there are no
automations.

## What gets published, and when

| When | What |
|---|---|
| Startup, and every MQTT (re)connect | The hub marker `online`, then a full resync (retained data is re-sent in case the broker lost it). |
| A label, area, floor, device or entity changes | After 5 quiet seconds, a resync: revoke what stopped qualifying, publish what now qualifies. |
| An exposed entity (or a thermostat's entity or sensor) changes state | Its retained state and availability, only if the payload changed. |
| Every 6 hours | A safety resync. |

## Topics

```
<unit>/ha/<component>/<object>/config   retained discovery           hub -> child
<unit>/<domain>/<object>/state          retained state               hub -> child
<unit>/<domain>/<object>/availability   retained online/offline      hub -> child
<unit>/<domain>/<object>/set            command                      child -> hub
<unit>/status                           the child's own birth/will
ha-unity/hub/status                     hub marker (configurable)

<unit>/ha/climate/<room>/config         thermostat discovery         hub -> child
<unit>/climate/<room>/current           current temperature          hub -> child
<unit>/climate/<room>/target/state      target temperature           hub -> child
<unit>/climate/<room>/action            heating/cooling/idle/off     hub -> child
<unit>/climate/<room>/target/set        setpoint command             child -> hub
```

One JSON state topic and one JSON command topic per entity (lights, fans,
covers), converted by templates on the child. That keeps commands on a
four-level topic, so the broker ACL needs only `<unit>/+/+/set` and
`<unit>/climate/+/target/set` for a child.

## Availability

A child's entity is available only while **both** the hub marker reads `online`
and its own `.../availability` reads `online`. The broker's will message on the
hub sets the marker to `offline`; Unity sets it back to `online` on every MQTT
reconnect, so a dropped connection cannot leave children stuck unavailable.

## A command, step by step

1. The child publishes to `<unit>/<domain>/<object>/set` (the broker ACL allows
   only that shape).
2. The hub ignores it if it is **retained**, if the topic is malformed, or if
   the unit is unknown.
3. The entity comes from the **topic**. The hub re-reads the registry and
   re-checks the label-and-floor policy **now**, so a revoked entity is ignored
   at once, before any resync.
4. The payload is validated against the entity's current attributes: values are
   clamped, colour models and effects must be advertised, fan and cover features
   must be present. The service is hardcoded per domain.
5. The service call is made. At most 10 run at once.

## Thermostats

A thermostat is a **grant**: one hub climate entity for one child. The child
sees an MQTT climate entity showing the current temperature, the target and the
action, with an `auto`-only mode, and can change **only the target**. The hub
clamps it to the grant's range and the entity's own range and calls
`climate.set_temperature`. If an override release is configured, the hub selects
the `none` preset after N minutes without a newer setpoint (RoomMind's way back
to its schedule). The deadline is persisted across restarts.

## Cleanup

Unity persists what it has published. When an entity stops qualifying, is
deleted, or its child or thermostat is removed, it publishes empty retained
payloads to its discovery, state and availability topics, even if that happened
while Home Assistant was off. Deleting the hub entry clears everything and sets
the marker to `offline`.
