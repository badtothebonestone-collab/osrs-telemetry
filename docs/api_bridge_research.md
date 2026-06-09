# API Bridge Research

Local source of truth:

- `src\main\java\com\osrstelemetry\TelemetryPlugin.java`
- `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java`
- `src\main\java\com\osrstelemetry\PluginLiveCache.java`
- `src\main\java\com\osrstelemetry\WorldModelCache.java`
- `telemetry-viewer\context_service.py`
- `telemetry-viewer\telemetry_schema.py`

Official RuneLite API references checked:

- [Client Javadocs](https://static.runelite.net/runelite-api/apidocs/net/runelite/api/Client.html)
- [TileObject Javadocs](https://static.runelite.net/runelite-api/apidocs/net/runelite/api/TileObject.html)
- [GameObject Javadocs](https://static.runelite.net/runelite-api/apidocs/net/runelite/api/GameObject.html)
- [NPCComposition Javadocs](https://static.runelite.net/runelite-api/apidocs/net/runelite/api/NPCComposition.html)
- [Perspective Javadocs](https://static.runelite.net/runelite-api/apidocs/net/runelite/api/Perspective.html)

## Currently Exported

The plugin snapshot endpoint supports cached needs for `baseline`,
`scene_delta`, `projection`, `inventory`, `inventory_delta`, `activity`,
`navigation`, `collision_window`, `bank_ui`, `dialogue_state`, `interaction_hot`,
`client_tick_tail`, world-model censuses, `writer_health`, and `watch_values`.

`PluginLiveCache` tracks packet type, latest tick, export sequence, update time,
age by packet type, payload bytes, and update errors. `ClientTickHotState`
tracks client tick, game tick, mouse canvas position, hover menu, open-menu
state, menu entries, last clicked menu option, session id/path, and sample
latency.

`TelemetryPlugin.hoverMenuPayload()` already exports `menuBounds` from
RuneLite client menu coordinates plus sorted menu entries. Python can compute
per-row bounds from menu bounds, row count, and display order. Older recordings
may miss row geometry if their normalized high-value fields truncated the menu
sample before `menuBounds`/`entries`.

`WorldModelCache` exports loaded-scene metadata, player location, camera and
viewport/canvas metadata, object census records, object names/actions, object
world/local/scene positions, stable object keys, distance to player,
resource/service/route classifications, projection status/aim geometry, actors,
ground items, inventory, collision windows, pathing frontier, and quality flags.

Historical/debug tick records can include raw local player, inventory,
equipment, widgets, bank UI, dialogue, scene objects, NPCs, players, active
prayers, skills, frame metadata, capture errors, and writer pressure. Normal
live mode should prefer the endpoint/cache path instead of raw tick files.

## Already Useful

- Metadata/freshness/export sequence: already available through `/health`,
  `/schema`, `/snapshot`, and `PluginLiveCache`.
- Player location and plane: available from baseline/world-model metadata.
- Inventory summary and deltas: available through compact inventory packets and
  Python activity context.
- Hover/menu proof: available through `interaction_hot` and `client_tick_tail`.
- Nearby object identity/actions: available in world-model object censuses.
- Traversal route object identity/actions: available in the route object census
  and normalized in Python as `route_objects`, `best:route:<query>`, and
  `nearest:route:<query>`.
- Geometry/aim data: available through projection payloads and world-model
  projection fields.
- Bank UI and dialogue state: exported as dedicated compact packet needs.
- Banking lifecycle can infer deposits from inventory/menu evidence, but direct
  bank-open/widget/container fields must be present in the recording to promote
  the proof beyond inferred evidence.
- Source freshness and field presence: now computed in Python by
  `telemetry_capabilities.py`.

## Missing Or Weak

- Local destination can be present in some client APIs but is not consistently
  normalized into sidecar context.
- Selected item, selected spell, and selected widget state need targeted
  bridge export or a confirmed existing widget/menu path.
- The `bank_ui` cache path exports bank-open/UI widget state and Record
  Everything preserves that packet from the plugin snapshot endpoint when it is
  present. The bridge now also exports compact bank container changed-item
  deltas from snapshot diffs, and the Python analyzer can recover the same proof
  from recorded bank snapshots when historical recordings lack the explicit
  delta field.
- The `combat_state` cache path exports compact combat evidence: player/NPC
  interaction, hostile actors, hitsplats with actor and amount, actor deaths,
  chat messages, stat changes, and player health. The current Mugger fixture
  proves Python can compute damage taken/dealt, HP delta, primary opponent, and
  task resume without a Java bridge change.
- NPC action arrays are less consistently exposed than object actions in the
  current normalized context.
- Widget allowlists are present in pieces, but top-level interface state needs
  more manual recordings to decide the compact shape.

## Likely Requires RuneLite/Plugin Export

- Selected item/spell/widget state.
- Bank container item slot-level details if compact changed-item deltas are not
  enough for a future task.
- Bank item-container change event provenance if we need to distinguish
  `ItemContainerChanged` from snapshot-diff proof.
- Explicit local destination normalized with tick/source metadata.
- Effective NPC action arrays when not already available from transformed
  composition or actor/world-model payloads.

Any Java export must remain read-only, bounded, and compile with Gradle.

## Computable In Python

- Source freshness/staleness and parse warnings.
- Field presence and schema gap categories.
- Candidate distance summaries when world/player points are present.
- Best/nearest target scoring from observed object/NPC telemetry.
- Route object scoring from observed stair/ladder/door/trapdoor identities,
  actions, world points, route kinds, and distances.
- Compact context filtering and missing-capability reporting.

## Needs More Manual Recordings

- Bank open/deposit/close flows.
- Selected item/spell/widget interactions.
- Menu open versus hover-only transitions.
- Right-click menu row geometry with `menuBounds` and entries preserved in the
  manual recording high-value fields.
- NPC hover/interact flows.
- Widget/dialogue transitions on route stairs and bank prompts.

## Safe Bridge Field Workflow

1. Record a manual action with `manual_recorder.py`.
2. Analyze `summary.json` and `schema_gap_report.md`.
3. Pick one `requires_bridge_export` gap.
4. Add the smallest read-only Java field group.
5. Add or update schema/capability detection in Python.
6. Expose a compact sidecar context field.
7. Add an MCP wrapper only if the sidecar field is useful to external callers.
8. Re-record and verify the field moves to `present`.

## Validation

Run Python compile/tests for recorder, analyzer, schema, capabilities, context,
and MCP code. If Java changes are made, run the smallest relevant Gradle compile
or test command and keep the field group to one bridge improvement per pass.
## Combat State Live Cache

The plugin exports compact `combat_state.v1` through the plugin snapshot endpoint with need `combat_state`. The payload is bounded and includes local-player combat state, player interacting target, actors targeting the local player, nearby hostile NPC summaries, recent hitsplats, actor deaths, animations, graphics, overhead text, chat messages, stat changes, and player health.

The endpoint maps `combat_state` to `live_combat_state_packet.v1`. Record Everything requests and preserves this need by default.
# API Bridge Research Notes

## Tree / Woodcutting Geometry

The `20260607_190145_Cutting_a_tree_or_two_with_camera_movement` fixture showed
that Tree aim geometry was already exported through existing live candidate
payloads and preserved by Record Everything. The missing piece was Python-side
target geometry recovery: Tree candidates often had names, ids, world/local
points, and aim points but no `effectiveActions`, so `Chop down / Tree` could
fall through to unrelated nearby Gate geometry.

Current status:

- Tree aim geometry: available in preserved object candidates and recovered by
  `target_match_quality.py`
- Gate / Close geometry: rejected for `Chop down / Tree`
- Selected Tree clickbox/tile polygon: still missing in the validated fixture

No Java bridge change was required for Tree aim recovery. A future bridge pass
should focus narrowly on selected object clickbox bounds or canvas tile polygons
for woodcutting targets if RuneLite exposes them reliably.
