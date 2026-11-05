# Unity architecture (design, not yet built)

Status: **proposal**. Nothing in this document exists yet except where it says
"today". The blueprints in `blueprints/` are the shipped, tested baseline and
stay supported until the integration reproduces what they do.

## Decisions so far

- Unity becomes a **HACS custom integration** built into this repository, after
  it has been tested. The blueprints remain as the lightweight option.
- One integration, two roles: **hub** (publishes) and **child** (receives).
- The hub-to-child **communication plane stays MQTT**. No component on the
  parent holds a credential for a child.
- A failed sync must never leave a child worse off: the child **falls back to
  its last known-good configuration** (see below).
- **PropSense is a distinct project.** It serves single-instance users who
  never need Unity. Unity may use PropSense as its engine or dependency, or
  later become an extension of it. That is undecided; this design keeps the
  boundary clean enough to go either way (see "Relationship to PropSense").

## Goals and non-goals

Goals
- Grant selected hub entities, and later dashboards and their frontend
  resources, to child instances, driven by labels, floors and areas.
- Fail closed: anything not explicitly granted is never published or applied.
- Commands from a child are re-validated on the hub every time.
- A child keeps working from its last good state when the hub or a sync fails.
- Be testable without a real Home Assistant for the policy and mapping logic.

Non-goals
- Ownership of a general label/rule engine or its UI. That is PropSense's job.
- Installing HACS resources on a child automatically (report only, see below).
- Locks, alarm panels, cameras and scripts, which remain excluded.

## Relationship to PropSense

PropSense answers "which entities does this rule apply to, and what action
should happen" for a single instance. Unity answers "make a safe subset of this
instance available on another instance".

Boundary to keep:
- A **grant target** is an input to Unity: a set of entities (and optionally
  dashboards) plus a child identifier. How the set was chosen is not Unity's
  concern.
- Option A, dependency: PropSense selects entities by rule and calls Unity's
  grant as one of its action types.
- Option B, extension: Unity ships inside PropSense as an optional module.
- Until that is settled, Unity keeps its own simple selection (unit label AND
  unit floor, or common label AND common floor), exactly as the blueprints do
  today.

## Roles

| Role | Responsibilities |
|---|---|
| Hub | Select what is granted, map and publish it, validate and execute child commands, publish the sync manifest and the hub status marker. |
| Child | Receive, validate, stage, apply and report. Never trusts a message it cannot validate. |

The same component is configured as either role in its config flow.

## Communication plane

Keep the topic shape that works today (`<unit>/<domain>/<object>/state|set`,
discovery under `<unit>/ha/...`, hub status marker) so the broker ACL stays
narrow: a child may publish only its own `<unit>/+/+/set`,
`<unit>/climate/+/+/set` and `<unit>/status`.

Add a versioned envelope for the new message types:

- `schema` (integer) so a child can refuse a version it does not understand.
- `revision` (monotonic per child) so a child can ignore stale or replayed
  messages.
- `hash` (of the canonical payload) so a child can detect a corrupt or partial
  message.
- Optional authentication of the hub's message, for example an HMAC with a
  per-child secret held in the config entry. **Open question:** whether the
  broker ACL alone is a sufficient trust boundary. It already stops a child
  publishing into another child's topics; it does not stop a compromised hub
  credential, which is out of scope.

Message types: discovery, state, command, manifest (what is granted and which
frontend resources it needs), dashboard bundle, status (child to hub).

## Last known-good fallback

The rule: **a child changes its live configuration only after a new bundle has
been fully validated and applied successfully.** Otherwise it keeps, or returns
to, the previous good one.

Child apply pipeline for a bundle (dashboards, manifest, mappings):

1. **Receive** a retained message. Check `schema`, `revision` greater than the
   last applied, and `hash`.
2. **Validate** against the allowlist and policy (resource URLs, card types,
   entity references). Reject, do not repair.
3. **Stage** the candidate on disk beside the current one. Never edit in place.
4. **Apply** the candidate.
5. **Health check** (it loads, entities referenced exist, resources present).
6. **Commit**: mark the candidate as last known-good, keep the previous N good
   versions.
7. **On any failure at steps 2 to 5**: restore the previous good version, do not
   advance `revision`, and publish a retained status with the failing step and
   reason.

Properties to guarantee and test:
- Restart of the child, with the hub unreachable: the child starts from its
  last known-good copy on disk and needs nothing from the hub.
- A malformed, oversized or mid-update message changes nothing.
- A partial apply is rolled back, not left half-applied.
- Hub outage: granted entities become unavailable through the existing
  availability marker; stored dashboards stay in place. Availability and
  configuration sync are separate concerns.
- The hub can see per-child status (last good revision, last error).

**Entities** are the one place where this is already partly handled: entities
come from standard retained MQTT discovery, so a child with no Unity component
still works, and a bad discovery payload only affects that one entity. The
proposal is to keep entities on standard MQTT discovery and put the strict
stage/validate/commit pipeline on the things MQTT discovery cannot do
(dashboards, resource manifest, ID mapping). **Open question:** whether the
child component should later own entity creation from a validated local cache
for full rollback semantics.

## Dashboards and frontend resources

Unverified against Home Assistant internals; each point needs a spike before
it is relied on.

- Dashboards are stored configuration and are saved through the frontend
  websocket API, which is not callable from a blueprint. The child component
  would apply them through HA's internal Lovelace storage, with the previous
  config kept for rollback.
- **Entity IDs differ between hub and child.** A hub's `fan.example` can become
  a differently named entity on a child. The hub publishes the mapping from hub
  entity to child unique ID, and the child rewrites references before applying.
- **Fail closed:** a card that references an entity not granted to that child is
  removed or blanked, never left pointing at a hub entity.
- **Custom cards run code.** The child accepts resources only from an allowlist
  of URLs, ideally with hashes in the manifest, and rejects anything else.
- **HACS dependencies: report, do not install.** The manifest lists required
  resources; the child reports which are missing so a person installs them.
  Whether HACS offers a supported remote install is unverified.

## Hub policy and adapters

Move the whitelist and the per-domain logic from long Jinja templates into
unit-tested Python:

- One adapter per domain (light, fan, switch, sensor, binary sensor, scene,
  cover, climate), each defining discovery payload, state payload, and the
  validated command path with hardcoded services, clamps and feature checks.
- The `exposed` rule has a single implementation, shared by discovery, state
  and command handling.
- Subscribe only to entities that are granted, not to every state change.
- Cleanup of stale retained discovery when an entity is removed from the hub
  (a known gap today).

## UI

Scope is limited to the Unity concern: configure child instances, see what is
granted to whom, see per-child sync status and the last error, and an audit log.
Label and rule authoring stays with PropSense (or with HA's own label UI until
the boundary above is settled).

## Testing

- Port every behaviour verified live on real instances into automated tests as
  the reference: discovery, availability, commands, negative tests, light
  colour, colour temperature, xy and effects, fans, and the hub status
  marker with its will message and re-announce on reconnect.
- Pure-Python tests for the policy, mapping and rewrite logic with no Home
  Assistant dependency.
- Integration tests with Home Assistant's test helpers for the adapters and the
  child apply pipeline, including failure injection at every pipeline step.
- Live checks on real instances stay a gate before a release, and the docs say
  exactly what has and has not been run live.

## Phases

1. **Baseline** (now): the blueprints, their tests, and live verification of the
   remaining domains. Exit: the "unverified" list is empty or explicitly
   deferred.
2. **Spikes**: confirm how a child can apply a dashboard and register a
   resource, and whether HACS exposes anything for remote install. Exit: each
   claim in "Dashboards and frontend resources" is verified or dropped.
3. **Skeleton**: `custom_components/<domain>/`, `hacs.json`, config flow for
   hub and child, the envelope, and the status message. One domain (lights)
   end to end behind the same behaviour as the blueprint.
4. **Parity**: remaining domains, stale-discovery cleanup, the command path.
5. **Dashboards**: manifest, ID mapping, allowlist, the stage/commit/rollback
   pipeline, status reporting.
6. **Decision on PropSense**: dependency or extension, once both have settled
   interfaces.

## Open questions

- Domain and package name for the component.
- Dependency on PropSense versus extension of it.
- Whether the envelope needs authentication beyond the broker ACL.
- Whether the child component should own entity creation.
- Minimum supported Home Assistant version for the component.
- Where the line sits between the Unity UI and PropSense's UI.
