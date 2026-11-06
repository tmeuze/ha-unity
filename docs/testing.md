# Testing

## Offline

```bash
pyenv local ha-unity                 # see README, Development
pip install -r requirements-test.txt
python -m pytest -q
```

- `tests/test_policy.py`, `test_topics.py`, `test_adapters.py`, `test_climate.py`:
  pure logic, no Home Assistant. They cover fail-closed exposure, every
  per-domain payload, clamps, feature gates, and the thermostat.
- `tests/test_acl_example.py` parses `docs/emqx-acl-example.conf` and evaluates
  it first-match-wins against Unity's topics, so a rule-order mistake is caught.
- `tests/integration/`: Home Assistant's test harness with a mocked MQTT client:
  publishing, revocation, the command path, persistence, expiry, the config flow.

The guards (clamps, policy re-checks, retained-command handling, timer
cancellation, persistence) were also checked by **breaking them on purpose**:
every real break was caught by a test. Do the same when you change one.

Offline tests use a mocked MQTT client and stand-in entities. They cannot prove
how a real broker, a real MQTT child or your devices behave. CI runs them on
Linux; they were developed against a newer Home Assistant than a given install
may run.

## On a real hub and children

1. **Read first.** In Developer Tools, Template, on the hub:
   `{{ label_entities('your_unit_label') }}`, `{{ floor_areas('your_floor') }}`,
   `{{ area_id('light.some_light') }}`. The Unity diagnostics (Settings, Devices
   & services, Unity, Download diagnostics) list what each child may see.
2. **Publishing.** Reload the Unity entry and watch a child (Developer Tools,
   MQTT, listen to `<unit>/#`): live messages for every exposed entity, with the
   hub marker `online`.
3. **Command path, harmless.** From a child, send a command that changes
   nothing (turn off a light that is already off). The hub should run exactly
   one service call.
4. **Negative.** From a child, publish `<unit>/light/<an entity of another
   child>/set`: the hub must make no service call. Publishing into another
   child's topic is denied by the broker ACL.
5. **Revocation.** Remove a label: the entity disappears from the child within
   seconds and a command for it is ignored at once.
6. **Reconnect.** Kick the hub's MQTT client in the broker: the marker flips to
   `offline` (the will) and Unity restores `online` on reconnect.
7. **Lights, fans, covers.** Try one of each kind you own: the child's card
   offers exactly the controls the hub entity has, and what you choose reaches
   the real device. Restore the device afterwards.

## Thermostats

Use one room, a tight clamp and a short release time first.

1. Label the climate entity for the child and give it an area on the child's
   floor, then add the thermostat.
2. The child should show the right current temperature, target and action.
3. Send a setpoint above and below the range from the child: the hub entity
   should receive the clamped value.
4. With an override release configured, confirm the controller returns to its
   schedule after the time (for RoomMind, the `none` preset and a cleared
   `override_until`).
5. Restart Home Assistant while an override is pending: it should still be
   released on time (or at startup if the time passed).
