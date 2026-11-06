# Migrating from the blueprints

The integration replaces the discovery, state and command-bridge automations,
the synthetic climate blueprint and the hub heartbeat automation. Existing
installs are not preserved: migrate once, in this order, so two publishers never
write the same topics.

1. **Disable** (do not delete yet) the discovery, state and command
   automations for every child, and the hub heartbeat automation.
2. **Install Unity through HACS**, restart Home Assistant, and add the
   **Unity** integration. Use the same hub status topic as before (the default
   is `ha-unity/hub/status`), the same common label and floors, and the same
   optional area prefix regex.
3. **Add each child** with the same unit name, label and floors as its
   blueprint automations. The unit name must match the child's topic prefix
   and the broker user's permitted topics.
4. Within seconds Unity republishes retained discovery, state and availability.
   The retained payloads are the same shape the blueprints produced, so the
   children keep their entities and unique IDs. Check each child.
5. **Thermostats.** For each synthetic climate automation, add a thermostat
   (**Add thermostat** on the Unity page) for the same child and room name,
   using the hub climate entity. The thermostat reads the current temperature,
   target and action from the climate entity itself, so the separate
   temperature, target and mode sensors are optional; the expiry action becomes
   a choice (**End the override**, which selects the `none` preset). The room
   name must match the old `object` so the child keeps its entity.
6. **Delete** the old automations, the heartbeat and the imported blueprints.

Things that changed in behaviour:

- **No heartbeat automation.** Unity keeps the hub marker `online` and
  re-announces it on every MQTT reconnect. Keep the hub's MQTT will message on
  the same topic with payload `offline` and retain on.
- **Retained commands are ignored** (the bridge executed them).
- **Stale retained topics are cleaned up**, including after an entity is deleted
  from the hub entirely, and when a child is removed.
- **A child with an invalid unit name** (uppercase, slash, wildcard) is rejected
  by the UI instead of being published.
- **Fan commands** with several keys in one message now run every valid key
  (the blueprint ran only the first match). The discovery payloads Unity
  publishes send one key per message, so children behave the same.

The broker ACL does not change: a child may publish only `<unit>/+/+/set`
(and its own status topic), and read only its own prefix and the hub marker.
