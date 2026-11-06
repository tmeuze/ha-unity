# Security model

## What Unity guarantees (by construction and by test)
- **Fail closed.** Exposure needs a label, a matching floor and an allowlisted
  domain. Missing or mismatched anything exposes nothing.
- **Strict label/floor pairs.** A unit label on a common-floor entity, or a
  common label on a unit floor, exposes nothing. A tagging mistake alone never
  leaks an entity.
- **A fixed domain list.** Only light, switch, sensor, binary sensor, fan, scene and cover and, as an explicit grant, a thermostat are handled. Locks, alarm panels, cameras, scripts and automations are not. The domain list is not an input.
- **Safer covers.** Garage, gate and door covers are blocked by default, and an unavailable cover is never exposed.
- **Commands are validated twice.** The broker ACL limits what a unit may
  publish; the hub re-checks the whitelist on every command and uses hardcoded
  services. The entity comes from the topic, never the payload.
- **Thermostats grant one thing: the target temperature.** A thermostat is an
  explicit grant of one hub climate entity to one child, subject to the same
  label-and-floor rule (a labelled thermostat is *not* exposed as an ordinary
  entity). The guest cannot change mode, preset or on/off; the value is clamped
  to your range and to the entity's own; the only service reachable is
  `climate.set_temperature` on that entity; and the climate entity, not the
  payload, decides where it goes.
- **Revocation.** Removing a label, area or floor clears discovery within
  seconds (debounce) and stops commands immediately, because the policy is
  re-checked on every command. Deleting an entity, or removing a child, also
  clears the retained topics (what was published is persisted).
- **Retained commands are never executed**, so a command left on the broker
  cannot fire when the hub restarts.

## What you must still do
1. **Broker ACLs.** Replace the broker's default allow-all rule with
   deny-by-default, one user per instance, unique client IDs. See
   [emqx-acl-example.conf](emqx-acl-example.conf). Units must **not** be able to
   read or write device-bridge topics, discovery prefixes or other units.
2. **Test the denials**, not just the allows: publish a command for another
   unit's entity, and to another unit's topic, and confirm nothing happens
   ([testing.md](testing.md) lists the checks).
3. **Keep privileged integrations on the hub only** (radios, locks, cloud
   accounts, controllers). Units need no token to the hub.
4. **Treat label/area edits on the hub as access control.** Anyone who can
   change them on the hub changes what a unit can reach.
5. **Review before exposing anything that matters.** This is pre-1.0 software.

## Scenes need care
A scene is only as safe as what it does. Exposing a scene lets a unit trigger
every entity the scene touches, and Unity cannot check whether those
belong to that unit. Label only scenes whose contents are safe for that unit.

## Not covered
- A compromised hub exposes everything. A compromised unit can only read its
  own prefix and send validated commands.
- Cameras and locks are out of scope. If you add them, they need their own
  design (stream tokens, unlock auditing).
- Physical or radio-level isolation: units sharing one radio mesh can still
  affect each other's devices at the radio layer.
