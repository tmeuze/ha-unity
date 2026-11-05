# Testing

> These notes describe the legacy blueprints. The integration is described in [architecture.md](architecture.md).

## Offline (no Home Assistant needed)
```bash
python tests/policy_model.py        # whitelist model + ACL example, standard library only
python tests/render_blueprints.py   # renders real blueprint templates; needs PyYAML and Jinja2
```
`render_blueprints.py` loads each blueprint, substitutes inputs, and renders the
actual template strings with Jinja2 against **stubbed** Home Assistant
functions. It checks that the three `exposed` templates agree with each other
and the model, that commands are validated and clamped, that discovery names and
`suggested_area` are right, and that the climate path respects the whitelist.
It cannot prove HA's own functions behave like the stubs.

Both suites were also checked by breaking the templates on purpose (dropping a
floor check, adding a domain, removing a clamp); every break was caught.

## Fans, scenes, covers on a real instance
The offline suite checks the payloads and the validation. It cannot tell you
how a real MQTT fan or cover reacts to them, in particular that several
`*_state_topic` keys may share one topic. Try one fan, one scene and one cover:
every control the hub entity has should appear on the unit and work, and a
control the hub entity lacks should not appear.

## Lights on a real instance
Offline tests cover discovery modes, state payloads and command clamping with
stand-ins. On a real hub check one light of each kind you own (on/off only,
dimmable, colour temperature, colour): the unit's card should offer exactly the
controls the hub light has, and a colour or colour temperature chosen on the
unit should reach the real light.

## Climate on a real instance
The offline tests cover the policy, clamp, topics, payloads, event filter and
the expiry wiring. They cannot cover your climate entity. Before relying on it:
call `climate.set_temperature` on the hub climate entity from Developer Tools;
check that the unit shows the right current, target and action; send a setpoint
from the unit and confirm the clamp; if you use expiry, confirm your
`expire_action` clears the override.

## On a real instance, read-only first
In Developer Tools, Template, on the hub (replace ids with yours):
```jinja
{{ label_entities('live_unit_a') }}
{{ floor_areas('unit_a') }}
{{ area_id('light.some_light') }}
{{ area_name('light.some_light') }}
```
Then render the blueprint's `exposed` template with real inputs and confirm it
lists only the entities you expect.

## End to end (use a real but harmless light)
1. Label a light for unit A and give it an area on unit A's floor. It must
   appear on unit A, in the right room, with a clean name and `online`.
2. It must **not** appear on unit B. A light with the common label on the common
   floor must appear on both.
3. From unit A, turn it on and off. The hub's real entity must follow, and the
   other unit must mirror a common entity.
4. **Negative:** from unit A publish `<A>/light/<unit B's entity>/set`: nothing
   happens (the hub ignores it). Publish `<B>/light/<entity>/set`: nothing
   happens (the broker ACL denies it).
5. Remove the label: discovery clears and the entity disappears from the unit.

## Audit for label/floor mismatches
Lists entities whose label does not match their floor (they are not exposed,
but it is usually a tagging mistake). Adjust names to yours.
```jinja
{% set rules = {
  'Live: Unit A': ['Unit A', 'Common'],
  'Live: Unit B': ['Unit B', 'Common']
} %}
{% for lbl, floors in rules.items() %}
  {% set ns = namespace(a=[]) %}
  {% for f in floors %}{% set ns.a = ns.a + floor_areas(f) %}{% endfor %}
  {% for e in label_entities(lbl) %}
    {% if area_id(e) not in ns.a %}{{ lbl }}: {{ e }} (area: {{ area_id(e) }})
{% endif %}
  {% endfor %}
{% endfor %}
```
