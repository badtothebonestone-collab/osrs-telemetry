# Recording Analysis: Lower Stairs To Woodcutting Area

## Recording Inspected

- Folder: `C:\Users\badto\osrs-telemetry\recordings\20260602_215341_manual_action-Lower_stairs_to_tree_cutting_area`
- Label: `manual_action-Lower stairs to tree cutting area`
- Session: `C:\Users\badto\.osrs-telemetry\sessions\2026-06-02_21-52-15`
- Recorder mode: until stopped, UI stop file
- Raw included: yes
- Poll interval: 20 ms

This recording is a human route demonstration: the player started near the
lower-stairs area, clicked destination tiles, moved the camera normally, and
walked toward the desired woodcutting area. It is not evidence of clicking or
using a staircase object.

## Analyzer Commands Run

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260602_215341_manual_action-Lower_stairs_to_tree_cutting_area" --summary --schema-gap
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260602_215341_manual_action-Lower_stairs_to_tree_cutting_area" --json --out "C:\Users\badto\osrs-telemetry\recordings\20260602_215341_manual_action-Lower_stairs_to_tree_cutting_area\analysis_full.json"
```

Analyzer succeeded. It refreshed `summary.json`, `schema_gap_report.md`, and
created `analysis_full.json`.

## Timeline

| Elapsed | Tick | Export seq | Player world point | Plane | Pose | Run energy | Notes |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 2.516s | 121 | 1210 | 3205,3228 | 0 | 808 | 100.0 | Start near lower-stairs area. Route census saw Staircase at 3204,3229 distance 1. |
| 4.985s | 124 | 1240 | 3205,3228 | 0 | 808 | 100.0 | `Walk here` click recorded at mouse 77,63. |
| 7.500s | 128 | 1280 | 3213,3228 | 0 | 824 | 97.9 | Movement begins; camera unchanged. |
| 9.813s | 133 | 1330 | 3215,3218 | 0 | 824 | 93.7 | Moving south; route objects include doors/trapdoor/staircase. |
| 13.188s | 137 | 1370 | 3214,3211 | 0 | 824 | 90.9 | Camera moved: yaw 2045 -> 2, pitch 240 -> 149. |
| 17.407s | 143 | 1430 | 3205,3214 | 0 | 822 | 87.4 | Camera moved again: yaw 193, pitch 188. |
| 21.672s | 150 | 1493 | 3198,3222 | 0 | 824 | 82.5 | Heading north-west toward trees. |
| 25.797s | 156 | 1560 | 3195,3236 | 0 | 824 | 78.3 | Woodcutting-area trees close; Tree distance 3 in compact nearby objects. |
| 29.203s | 163 | 1630 | 3196,3239 | 0 | 819 | 77.65 | Later `Walk here` click recorded at game tick 161. |
| 32.391s | 169 | 1690 | 3194,3242 | 0 | 808 | 77.85 | End near tree cluster; recorder stopped via stop file. |

Start event: `2026-06-03T02:53:41.775142Z`.
Stop event: `2026-06-03T02:54:14.203218Z`.
Duration: `32.422` seconds.
Snapshots: `10`.
Events: `13`.
Markers: none.

No plane change occurred. No explicit destination field was captured. Movement
is inferable from world-position deltas, pose changes, run-energy drop, and
`Walk here` click samples, but the analyzer did not classify moving/stopped
transitions because a direct movement/destination field was absent.

## Source Freshness And Parse Quality

Most live files were fresh and parseable throughout. Latest observed source ages
were roughly 0.3-0.8 seconds for `baseline`, `context`, `status`, `activity`,
`events`, `navigation`, `watchValues`, and `overlayDebug`.

Parse failures: none.

Stale observations:

- `candidates`: 1 stale observation, initial age about 9.58 seconds.
- `overlayDebug`: 1 stale observation, initial age about 9.59 seconds.

Missing sources:

- `lastActionTrace`: missing.
- `inputIntegrity`: missing.

The analyzer reported no warnings, but raw `live_status.json` carried
`pluginSnapshotStatus: WARN` on every snapshot. Repeated live-status warnings
included projection refs capped, missing cached `inventory_delta`, missing
cached `watch_values`, and plugin snapshot limitations. That is a useful
analyzer/reporting gap.

## Useful Fields Found

| Category | Present? | Quality | Evidence | Useful for traversal? | Notes |
| --- | --- | --- | --- | --- | --- |
| meta/freshness/export sequence | Yes | Good | Export seq 1210 -> 1690; source ages recorded. | Yes | Good liveness proof; raw status warnings need better surfacing. |
| tick/game tick | Yes | Good | Tick 121 -> 169 over 10 snapshots. | Yes | Enough to order movement and object observations. |
| player world point | Yes | Good | 3205,3228 -> 3194,3242. | Yes | Best success signal for route example. |
| player plane | Yes | Good | Plane stayed 0. | Yes | Confirms no floor transition happened. |
| destination | No | Missing | No `destination` hits in raw status/high-value fields. | Yes, missing | Needed to know intended clicked tile directly. |
| movement state | Indirect | Weak | World position, pose, run energy changed. | Yes | Analyzer did not emit moving/stopped transitions. |
| animation/pose | Yes | Medium | Animation -1; pose 808/824/822/819. | Some | Pose hints movement; animation alone not useful. |
| nearby objects | Yes | Mixed | Compact nearby objects mostly Tree/Oak/Tree patch. | Yes | Useful at destination, weak for route objects. |
| nearby object raw id | Yes | Good | Tree ids 1276/1278; route raw ids in status. | Yes | Present in both compact and raw route census. |
| nearby object effective id | Yes | Good | Compact effectiveId equals id for trees; route ids in status. | Yes | Needs route normalization in Python. |
| nearby object effective name | Yes | Good raw, medium compact | Raw route census had Staircase/Ladder/Trapdoor/Door; compact nearby had mostly trees. | Yes | The important route names are hidden in raw status. |
| nearby object effective actions | Yes raw, weak compact | Raw route census had `Climb-up`, `Top-floor`, `Climb-down`, `Open`, `Close`; compact nearby tree objects often had missing `effectiveActions`. | Yes | Highest-value normalization gap. |
| stable object refs | Yes | Good | `objectKey` present for route and tree objects. | Yes | Good for dedupe and tracking. |
| object distance | Yes | Good | Staircase distance 1 at start; trees distance 2-4 near end. | Yes | Excellent route/destination context. |
| object screen/click geometry | Yes partial | Medium | Compact tree candidates had aim points; route census had world/local points but not exposed in high-value route summary. | Yes | Route object projection should be normalized if present. |
| hover entries | Yes | Medium | `clientTickHot.hoverMenu` present. | Limited | Mostly `Walk here`/`Cancel`; not a staircase hover. |
| open menu state | Yes | Good | `menuOpen: false` throughout. | Some | Confirms no right-click menu interaction. |
| open menu entries | Yes | Medium | `Walk here`, `Cancel`, occasional Examine Bush/Hanging meat/Table. | Limited | Did not identify traversal object. |
| selected item/widget state | No | Missing | Schema gap requires bridge export. | No | Not relevant to this recording. |
| top-level widget/interface state | No | Missing | Widgets reported missing. | No | Not relevant here. |
| inventory/container state | Yes inventory, no bank | Medium | Inventory known; bank missing. | No | Useful only to confirm not a bank/container interaction. |
| context service status | Yes raw | Medium | `live_status.v1`, profile woodcutting, inputSource plugin-snapshot, status WARN. | Yes | Status warnings should be summarized. |
| source freshness/staleness | Yes | Good | Per-source age/stale/parse status present. | Yes | Good recording QA. |
| analyzer/schema gap quality | Partial | Medium | Correctly found many fields, but missed route-object usefulness and movement transition. | Yes | Needs traversal-aware analysis. |

## Traversal-Specific Verdict

Did telemetry show the player approached stairs?

- It showed the player started beside a staircase: at tick 121 the player was
  at `3205,3228,0`; raw `worldModelRouteObjectCensus` had `Staircase` id
  `56230` at `3204,3229,0`, distance `1`.
- It did not show the player using stairs. Plane stayed `0`, and the user
  clarified the action was walking from that start area to the woodcutting
  area.

Did telemetry show a stairs/ladder/climbable object nearby?

- Yes. Raw status snapshots repeatedly contained route objects:
  `Staircase` id `56230` actions `Climb-up`, `Top-floor`;
  `Trapdoor` id `14880` action `Climb-down`;
  `Ladder` id `16683` action `Climb-up`;
  plus doors with `Open`/`Close`.

Did it capture object name, id/effective id, and actions?

- Yes in raw `live_status.json` under `worldModelRouteObjectCensus`.
- No in the recorder's compact `high_value_fields.nearby_objects` for the
  route-relevant objects. That compact list was dominated by woodcutting
  targets and lost the traversal objects.

Did hover entries or open menu entries appear?

- Hover entries appeared, but they mostly represented `Walk here` and `Cancel`.
  Some unrelated `Examine` entries appeared for Bush, Hanging meat, and Table.
- No open menu appeared; `menuOpen` was false throughout.
- No hover/menu evidence identified a staircase/ladder/trapdoor action in this
  recording.

Did plane, region/base, or world position change?

- Plane stayed `0`.
- World position changed substantially from `3205,3228` to `3194,3242`.
- Region/base fields were not surfaced in the recorder summary. Camera metadata
  was captured in `cameraViewport`, including camera X/Y/Z, yaw, pitch, canvas
  and viewport dimensions.

Could a future traversal module verify success from this captured data?

- Yes for the route demonstration: it can verify start near lower-stairs route
  objects, movement along a path, and arrival near the woodcutting tree cluster.
- No for explicit destination intent: no client destination/world destination
  field was captured.
- No for stair interaction success: the recording did not perform a stair
  interaction and plane did not change.

## Schema Gap Summary

Analyzer categories:

- `present`: tick, export_sequence, state_freshness, game_state,
  player_world_point, player_local_point, plane, player_animation,
  player_pose_animation, run_energy, inventory, inventory_slots, equipment,
  nearby_objects, nearby_npcs, effective object/NPC names/actions, stable refs,
  distances, canvas/clickbox/aim geometry, hover entries, menu state/entries,
  allowlisted widgets, camera/canvas/window metadata.
- `missing`: equipment_slots, bank_state, top_level_interface_widget_state.
- `computable_in_sidecar`: none.
- `requires_bridge_export`: destination, bank_container,
  selected_item_spell_widget_state.
- `needs_manual_review`: none.

The schema gap categories are technically correct at a broad level, but weak for
this traversal recording. Route object identity/actions are already exported in
raw status and should be classified as `already_present` plus
`present_but_weak` for Python/context normalization.

## Missing Or Weak Fields By Category

- `already_present`: tick/export sequence, source freshness, player world
  point, plane, camera viewport metadata, raw route object census, route object
  ids/names/actions/distances, inventory summary, hover/menu samples.
- `present_but_weak`: compact nearby object list, compact object actions,
  hover/menu target usefulness, movement state, analyzer warning summary,
  route-object timeline.
- `computable_in_python`: movement path summary, route start/end summary,
  nearest traversal-object timeline, arrival-near-woodcutting-area summary,
  camera movement summary, route object de-duplication.
- `needs_bridge_export`: client destination/local destination, explicit clicked
  destination world tile, selected item/spell/widget state, bank container.
- `needs_another_recording`: actual hover/click on a staircase/ladder/trapdoor
  if we want proof of object interaction, plus a marked route recording with
  "start", "clicked destination", and "arrived" markers.
- `analyzer_bug_or_gap`: movement transitions stayed empty despite position
  changes; raw route object census was not summarized; live status warnings were
  not surfaced; `effective_object_actions` appears present globally while
  compact nearby objects still report missing actions.

## Recommended Next Bridge/API Task

Pick exactly one improvement group:

**Traversal object identity/actions normalization.**

This should be a Python/context API improvement first, not a Java bridge export.
The bridge already provides the needed route-object records through
`WorldModelCache` and `worldModelRouteObjectCensus`.

Exact fields to add or normalize:

- `route_objects.schema_version`
- `route_objects.tick`
- `route_objects.source`
- `route_objects.count`
- `route_objects.cap_hit`
- `route_objects.objects[]`
- `objects[].ref` from `objectKey`
- `objects[].kind`
- `objects[].raw_id` / `objects[].effective_id` from `id`
- `objects[].effective_name` from `objectName` or `name`
- `objects[].effective_actions` from `actions`
- `objects[].world_point` with `worldX`, `worldY`, `plane`
- `objects[].local_point` with `localX`, `localY`, `sceneX`, `sceneY`
- `objects[].distance`
- `objects[].route_object_kind`
- `objects[].route_object_candidate`
- `objects[].on_screen`, `aim_point`, and projection/click geometry when
  present
- `objects[].freshness` or latest tick/export sequence
- `objects[].missing_fields`
- `best_route_object` / `nearest_route_object` helper result for queries such
  as `staircase`, `ladder`, `trapdoor`, `door`, and action queries such as
  `climb`

Where the data probably comes from:

- Java source: `src\main\java\com\osrstelemetry\WorldModelCache.java`
  `objectRecord`, `classifyObject`, `objectCensusPayload`, and `compactObject`.
- Java endpoint shaping: `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java`
  projection ref compaction already preserves object ids/names/actions.
- Python source path: `telemetry-viewer\live_target_processor.py` writes
  `worldModelRouteObjectCensus` into `live_status.json`.

Python modules that should consume it:

- `telemetry-viewer\telemetry_schema.py`: normalize route objects from
  `worldModelRouteObjectCensus.objects` and `payloads.route_object_census.objects`.
- `telemetry-viewer\telemetry_capabilities.py`: report route-object capability
  and missing route fields.
- `telemetry-viewer\context_service.py`: expose compact `route_objects`,
  `best:route:<query>`, and `nearest:route:<query>` or equivalent callable
  sections.
- `telemetry-viewer\analyze_manual_recording.py`: summarize traversal-object
  timeline and avoid treating raw route fields as missing.

Context API fields to expose:

- `routeObjects`
- `bestRouteObjects`
- `nearestRouteObjects`
- `movementSummary` only if computed from the same recording/context pass
- `missingCapabilities` entries for absent route object id/name/actions/distance

Validation plan:

1. Record the same route again with markers: `start lower stairs`, `clicked
   destination`, `arrived woodcutting area`.
2. Confirm the analyzer shows the nearest route object at start as Staircase id
   `56230` action `Climb-up`.
3. Confirm the compact context can answer `nearest:route:staircase` and
   `nearest:route:climb`.
4. Confirm the end state reports nearby Tree objects and arrival near the target
   tile/area.
5. If testing a real stair interaction later, confirm plane changes or expected
   post-transition world point changes.

Checks to run:

```powershell
python -m py_compile telemetry-viewer\telemetry_schema.py telemetry-viewer\telemetry_capabilities.py telemetry-viewer\context_service.py telemetry-viewer\analyze_manual_recording.py
python telemetry-viewer\tests\test_manual_telemetry_discovery.py
python telemetry-viewer\tests\test_context_service.py
python telemetry-viewer\tests\test_telemetry_ui.py
```

Run Java/Gradle checks only if Java bridge files are changed. This recommended
task should not require Java changes unless route-object projection geometry is
missing after Python normalization.

## Exact Follow-Up Codex Prompt

```text
@Computer

You are working in C:\Users\badto\osrs-telemetry.

Implement one focused telemetry/API improvement: normalize traversal route object identity/actions from existing worldModelRouteObjectCensus data into the Python telemetry schema, analyzer, and context API. Do not change Java unless the existing raw route-object fields are insufficient.

Use the recording analysis in docs/recording_analysis_manual_action_stairs_to_woodcutting_area.md as the source of truth. The raw live_status.json snapshots already include route_object_census.v1 records such as Staircase id 56230 with actions ["Climb-up", "Top-floor"], Ladder id 16683 with ["Climb-up"], Trapdoor id 14880 with ["Climb-down"], world point, plane, objectKey, kind, and distanceToPlayer. The current weak point is that compact high_value_fields.nearby_objects and context target helpers do not surface those route objects.

Deliverables:
1. Update telemetry-viewer/telemetry_schema.py to normalize route objects from worldModelRouteObjectCensus.objects and payloads.route_object_census.objects into compact route object candidates with ref, kind, raw_id/effective_id, effective_name, effective_actions, world_point, local_point, distance, route_object_kind, on_screen/aim geometry when present, freshness, confidence/reasons, and missing_fields.
2. Update telemetry-viewer/telemetry_capabilities.py so route object identity/actions/distance are reported as available when present.
3. Update telemetry-viewer/analyze_manual_recording.py so manual recording summaries include a route-object/traversal timeline, nearest traversal objects, and do not hide route object fields inside raw status.
4. Update telemetry-viewer/context_service.py to expose a compact route_objects section and a best/nearest route helper, such as best:route:staircase and nearest:route:climb, without dumping raw status.
5. Add focused tests using synthetic route_object_census.v1 records for Staircase/Ladder/Trapdoor.
6. Update docs/context_api.md and docs/manual_recording.md only as needed.

Run:
python -m py_compile telemetry-viewer\telemetry_schema.py telemetry-viewer\telemetry_capabilities.py telemetry-viewer\context_service.py telemetry-viewer\analyze_manual_recording.py
python telemetry-viewer\tests\test_manual_telemetry_discovery.py
python telemetry-viewer\tests\test_context_service.py
python telemetry-viewer\tests\test_telemetry_ui.py

Final report: changed files, commands run, tests passed/failed, and how to validate with a new manual route recording.
```

