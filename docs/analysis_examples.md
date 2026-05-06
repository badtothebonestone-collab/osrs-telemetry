# Analysis Examples

The export tool writes generated summaries under the selected session:

```text
exports\session_index.json
exports\tick_summary.jsonl
exports\event_summary.jsonl
exports\frame_index_summary.jsonl
```

`telemetry-viewer\replay_viewer.py` is a local browser-based replay viewer for
already-collected telemetry. It is read-only and uses `telemetry_paths.py` for
segmented canonical sessions and legacy flat fallback where applicable:

```text
python telemetry-viewer\replay_viewer.py
python telemetry-viewer\replay_viewer.py --session "C:\path\to\session"
python telemetry-viewer\replay_viewer.py --sessions-dir "C:\path\to\sessions"
python telemetry-viewer\replay_viewer.py --port 8765
```

To build a derived perception dataset for the newest session:

```text
python telemetry-viewer\build_perception_dataset.py
```

To initialize a new session from a reusable calibration profile:

```text
python telemetry-viewer\build_perception_dataset.py --calibration-profile "C:\path\to\screen_regions_profile.json"
```

`perception\tick_bundles.jsonl` contains one derived record per tick. Each
bundle joins the authoritative tick JSON with nearby event context, the
session-relative frame path, frame existence at build time, and frame-index
timing when available. `perception\screen_regions.json` is the session-local
normalized region map for review tooling; it does not crop or edit frame
images.

Screen-region profiles are tab-aware. `baseRegions` are always-valid areas such
as the full frame, game viewport, minimap, chatbox, side panel, tabs, compass,
and orb area. `tabProfiles` are side-tab-specific areas. Inventory, equipment,
prayer, magic, and other side-panel tabs need separate crop profiles because the
same side-panel pixels represent different widgets depending on which tab is
open.

When `screen_regions.json` must be created, profile loading uses this order:

1. Existing session `perception\screen_regions.json`
2. `--calibration-profile "C:\path\to\profile.json"`
3. `telemetry-viewer\calibration_profiles\default_screen_regions.json`
4. Built-in approximate fallback regions

The perception dataset is read-only derived data from existing telemetry and
performs no automation, clicking, input hooks, overlays, or client-state
mutation.

## Simplified Calibration And Dataset Flow

1. Calibrate from the launcher:

   ```text
   python telemetry-viewer\telemetry_launcher.py
   ```

   Click **Start Calibration Mode**, edit regions, then click **Save Default
   Profile** for future sessions or **Save Session Profile** for this session
   only.

2. Label active tab ranges with `telemetry-viewer\tab_labels.json`, or use the
   replay labeling UI if present.

3. Build perception:

   ```text
   python telemetry-viewer\build_perception_dataset.py
   ```

4. Generate disposable test crops from the launcher with **Generate Test
   Crops**. Test crops are preview/verification data, not the final training
   dataset. They are written under:

   ```text
   perception\test_crops\<run_id>\
   ```

   Prior test crop runs are preserved unless explicitly cleared.

5. Build persistent training data:

   ```text
   python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
   python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
   ```

   Training crops are durable derived data under:

   ```text
   training_data\crops\
   ```

   The builder is non-destructive by default. It appends new examples or skips
   duplicate keys. Only `--rebuild` clears `training_data` before rebuilding.

6. Inspect status:

   ```text
   python telemetry-viewer\dataset_status.py
   ```

7. Review generated training examples in a local browser:

   ```text
   python telemetry-viewer\training_dataset_inspector.py
   python telemetry-viewer\training_dataset_inspector.py --port 8790
   python telemetry-viewer\training_dataset_inspector.py --session "C:\path\to\session"
   ```

   Open `http://127.0.0.1:8790/`. The inspector shows summary cards,
   filters, crop thumbnails, labels, telemetry summaries, source frames when
   available, and a detail panel for the selected example. You are not
   expected to manually review every crop. Use **Review Queue**, start with
   **Balanced by regionProfile**, size `100`, and `cropExists=true`, then mark
   examples **Good**, **Bad Crop**, **Wrong Label**, or **Unsure**. If training
   data is missing, it shows: `Run python
   telemetry-viewer\build_training_dataset.py first.`

   QA review buttons append local metadata to:

   ```text
   training_data\review_labels.jsonl
   ```

   `review_labels.jsonl` is append-only QA metadata. It does not modify raw
   telemetry, does not overwrite `training_manifest.jsonl`, does not alter
   `training_index.json`, and does not modify crops.

   Missing crop diagnostics separate manifest examples from actual crop files.
   If many crops are missing, filter to `cropExists=true`. Only use
   `--rebuild` when you intentionally want to replace persistent
   `training_data`. `--include-missing-crops` is for diagnostics only, not
   normal training data.

8. Export a curated manifest for later model/training experiments:

   ```text
   python telemetry-viewer\export_curated_training_dataset.py
   python telemetry-viewer\export_curated_training_dataset.py --reviewed-only
   python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
   ```

   You do not need to review every crop. By default, review labels act as
   vetoes: unreviewed examples with existing crops are included, `good`
   examples are included, and latest `bad_crop`, `wrong_label`, or `unsure`
   reviews are excluded. `--reviewed-only` is the strict mode that includes
   only latest `good` reviews.

   Curated output is written to:

   ```text
   training_data\curated\curated_manifest.jsonl
   training_data\curated\curated_index.json
   ```

   `curated_manifest.jsonl` is the clean selected list for later experiments.
   Exporting it does not copy crops, delete crops, modify
   `training_manifest.jsonl`, or modify `review_labels.jsonl`.

9. Inspect derived UI/world target geometry alignment:

   ```text
   python telemetry-viewer\target_geometry_inspector.py
   ```

   Open `http://127.0.0.1:8800/`. The inspector overlays existing
   `interaction_geometry\ui_targets.jsonl` and
   `interaction_geometry\world_targets.jsonl` records on retained frame images.
   It is a local QA viewer only. It does not send input, perform mouse actions,
   interact with RuneLite, or modify telemetry, geometry, crops, or frame
   images. Missing retained frames are shown as a placeholder with the geometry
   still available for inspection.

   If target records exist but no frame image appears, run:

   ```text
   python telemetry-viewer\dataset_status.py
   ```

   Compare the geometry target tick range in the inspector with the retained
   frame tick range in status output. Geometry files may reference older frame
   paths after retention has kept only newer JPGs. Rebuild the derived UI/world
   geometry for currently retained frames:

   ```text
   python telemetry-viewer\build_world_target_geometry.py --target-type npc --only-on-screen --latest-with-frames 100
   python telemetry-viewer\build_ui_target_geometry.py --include-base-regions --latest-with-frames 100
   python telemetry-viewer\target_geometry_inspector.py
   ```

   `--latest-with-frames` checks actual frame files under `frames\` instead of
   trusting stale derived `frame.exists` metadata. If no retained frame files
   remain, collect a fresh session or raise the RuneLite config storage caps
   before collecting a longer QA run.

   World target records include conservative derived labels such as
   `targetRole`, `targetCategory`, and `targetTags`. Examples include
   interactable targets such as banks, doors, trees, ladders, furnaces, ranges,
   and altars; entity targets such as NPCs and players; item targets for ground
   items; and obstacle/navigation geometry such as walls, fences, counters,
   building pieces, and tiles. Obstacle and wall geometry is kept because it can
   be useful for navigation/pathing analysis, but the inspector can hide it with
   role/category/tag filters without deleting any records.

   If a useful object is visible but appears as a fallback label such as
   `SceneObject[1276]`, add a derived label override in:

   ```text
   telemetry-viewer\target_name_overrides.json
   ```

   Example:

   ```json
   {
     "sceneObjects": {
       "1276": {
         "name": "Tree",
         "role": "interactable",
         "category": "tree",
         "tags": ["tree", "clickable_candidate"],
         "notes": "Known visible tree object in this session."
       }
     },
     "groundItems": {},
     "npcs": {}
   }
   ```

   Overrides affect only derived names, roles, categories, and tags when
   `build_world_target_geometry.py` is rerun. They do not change raw telemetry,
   geometry, frame images, RuneLite state, or actions. If scenarios or ranked
   candidates should use new overrides, rerun world geometry, candidate
   selection, and then the scenario builder. To identify candidate object IDs
   for overrides:

   ```text
   python telemetry-viewer\inspect_target_geometry.py --target-type sceneObject --fallback-only --limit 50
   python telemetry-viewer\inspect_target_geometry.py --target-type sceneObject --unclassified --limit 50
   python telemetry-viewer\inspect_target_geometry.py --unclassified-scene-objects --large-only --top-ids --limit 20
   ```

   The `--unclassified-scene-objects` shortcut focuses on visible fallback
   scene objects with usable geometry and unknown/decorative classification.
   `--large-only --top-ids` groups the largest repeated IDs and prints a
   suggested override snippet. Some visible tree-shaped objects are decorative
   or non-interactable; only add confirmed useful IDs to the override file.

   The target geometry inspector and scenario inspector also include an
   **Add/Edit Override** panel. Select an unlabeled scene object, NPC, or ground
   item row, adjust the suggested name/role/category/tags, and click **Save
   Override**. The local API writes only
   `telemetry-viewer\target_name_overrides.json`; it does not modify raw
   telemetry, frame images, geometry files, RuneLite state, or actions. After
   saving, copy the rebuild commands shown in the panel so the derived world
   geometry, target candidates, and scenario dataset pick up the new label.
   The target geometry inspector also has **Copy Override Snippet** for quickly
   copying a selected scene-object ID into the JSON file by hand.

## From Raw Capture To Useful Target Candidates

The target pipeline has three read-only layers with different jobs:

- `interaction_geometry\world_targets.jsonl` is the broad world geometry layer.
  Use it when you want to inspect coverage, visibility, raw projected objects,
  walls, tiles, NPCs, ground items, and unclassified scene objects.
- `interaction_geometry\ui_targets.jsonl` is the calibrated UI geometry layer.
  It describes inventory/equipment/prayer/magic/base UI regions as geometry,
  not actions.
- `interaction_geometry\target_candidates.jsonl` is the filtered and scored
  candidate layer. It is useful for QA and downstream analysis, but it may be
  intentionally much smaller than `world_targets.jsonl` because profiles,
  semantic filters, dedupe, UI-blocked exclusion, and limits can reduce it.

Reusable target classes live in:

```text
telemetry-viewer\target_library.json
```

The library uses schema `target_library.v1`. Each class can match target types,
roles, categories, object IDs, target names, actions, and tags. Initial classes
include trees, oak/willow trees, rocks, fishing spots, doors, walls, NPCs,
players, ground items, bank-related targets, navigation tiles, unknown scene
objects, and unclassified scene objects. This is a labeling and scoring aid
only; it does not change raw capture, geometry, client state, or input.

Reusable profiles live in:

```text
telemetry-viewer\target_profiles.json
```

The profile file uses schema `target_profiles.v1`. Profiles define which target
classes/types/roles/categories to include, whether targets must be on-screen or
have geometry, whether UI-blocked candidates should be excluded, the default
limit, and scoring weights. Current profiles are:

- `broad_qa`: broad visual/debug QA with minimal semantic filtering.
- `woodcutting`: tree/oak/willow candidate QA.
- `navigation_qa`: walls, doors, obstacles, and tile geometry QA.
- `npc_qa`: NPC/player geometry QA.
- `ground_item_qa`: ground item geometry QA.
- `ui_qa`: UI region/slot/spell/prayer geometry QA.

Candidate records now include a stable packet-style surface for future batch or
live-feed consumers: `recordSchema`, `tick`, `source`, `targetKey`/`objectKey`,
`classId`, target type/name/id/hash, role/category/tags, world/scene/local
coordinates, `preferredGeometryType`, `aimPoint`, `geometrySummary`,
`distanceTiles`, `uiBlocked`, `blockingUiRegions`, `qualityScore`,
`qualityTier`, `positiveSignals`, `negativeSignals`, `rejectReasons`,
`profileId`, and `selectedByProfile`. Unknown values are omitted or null.

`qualityTier` is derived from the candidate quality score:

- `excellent`
- `good`
- `questionable`
- `poor`

Positive signals include things like `onScreen`, `geometryAvailable`,
`hasClickbox`, `hasConvexHull`, `hasCanvasTilePolygon`,
`knownTargetClass`, `preferredGeometryAvailable`, `nearPlayer`, and
`profileMatch`. Negative signals include `offScreen`, `missingGeometry`,
`missingClickbox`, `fallbackName`, `unclassified`, `duplicateCandidate`,
`uiBlocked`, `mostlyOffFrame`, and `notProfileMatch` when applicable.

UI-blocked detection uses existing `ui_targets.jsonl` geometry. When a world
candidate aim point lands inside major UI regions such as the minimap, chatbox,
side panel, tabs, inventory, equipment, prayer, or magic regions, the candidate
gets `uiBlocked=true`, `blockingUiRegions`, and `blockedReason`. This does not
remove the candidate unless a profile or CLI flag asks for it. Use
`--exclude-ui-blocked` when you want a clean candidate list for visual QA.

Example commands:

```text
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest 25 --profile broad_qa --limit 2000
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest-with-frames 25 --profile woodcutting --exclude-ui-blocked --limit 500 --open-inspector
python telemetry-viewer\run_target_geometry_pipeline.py --session "<session>" --range 155 179 --profile woodcutting --exclude-ui-blocked --limit 500
python telemetry-viewer\build_world_target_geometry.py --session "<session>" --latest 25 --target-type all
python telemetry-viewer\build_ui_target_geometry.py --session "<session>" --latest-with-frames 25 --include-base-regions --include-all-tab-profiles
python telemetry-viewer\select_target_candidates.py --session "<session>" --latest 25 --target-type all --profile broad_qa --limit 2000 --summary
python telemetry-viewer\select_target_candidates.py --session "<session>" --latest 25 --target-type all --profile woodcutting --exclude-ui-blocked --limit 500 --summary
python telemetry-viewer\summarize_candidate_quality.py --session "<session>" --latest 25 --profile woodcutting
python telemetry-viewer\target_geometry_inspector.py --session "<session>"
```

`run_target_geometry_pipeline.py` is the one-command orchestrator. It prints the
selected session and every command before running it, stops on the first failed
step, and prints a concise step summary. Pass `--session` for an explicit
session, or pass `--latest-session` when you intentionally want the newest
available session. Use `--dry-run` to inspect the command plan without writing
derived geometry files. The pipeline order is world targets, UI targets, ranked
candidates, coverage diagnostic, candidate quality summary, and finally the
local inspector if `--open-inspector` is set.

`broad_qa` is for visual inspection. Task profiles such as `woodcutting` are
read-only task candidate QA profiles, not automation. The project still does not
generate mouse movement, clicks, keyboard input, menus, overlays, or gameplay
actions.

## Target Coverage Diagnostics

`telemetry-viewer\diagnose_target_coverage.py` is a read-only coverage report
for understanding where world targets disappear across the pipeline:

```text
raw tick snapshot -> world targets -> target candidates -> scenario dataset -> inspector filters
```

It does not generate actions, mouse movement, clicks, keyboard input, menu
actions, overlays, or RuneLite state changes. It reads existing telemetry and
derived JSONL files, then reports raw counts, derived target counts, candidate
and scenario counts, tick/frame alignment, viewport-sector coverage, best-effort
identity matching, trace filters, and source-code hints for caps or filters.

Visible objects may be absent from overlays for several different reasons:

- Java capture may be capped before all scene objects are written.
- The scene scan may be radius-limited around the local player.
- Some object layers or background scenery may not be captured.
- Projection geometry may be unavailable for a record.
- `onScreen` or projection filters may remove otherwise captured objects.
- Candidate or scenario rules may intentionally narrow the queue.
- Retained frame files may no longer overlap older geometry ticks.
- Inspector filters or max-draw settings may hide records already in the file.
- Some visible scenery may be non-`TileObject` background/model/paint data.

If `interaction_geometry\world_targets.jsonl` is missing, the derived world
geometry builder has not been run for that session, failed, or wrote to a
different session. Build it before blaming the inspector:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --range 30 30 --target-type all
python telemetry-viewer\select_target_candidates.py --session "C:\path\to\session" --tick 30 --target-type all --limit 500 --summary
```

Use `world_targets.jsonl` as the broad geometry/visibility layer. It should
preserve captured NPC/player/object/tile geometry for QA. Use
`target_candidates.jsonl` as the filtered candidate layer. A large
`worldTargets -> targetCandidates` drop is expected when `--limit`, semantic
filters, or scenario rules are active; it does not by itself mean broad capture
is sparse. For broad QA, inspect world targets first. For task-specific QA,
inspect candidates.

Tick selection examples:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --all-ticks --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --latest 25 --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --range 32 56 --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --latest-with-frames 25 --target-type all
```

The builder summary prints `selected by`, `selection source`, selected tick
count, selected tick range, retained frame tick range, and selected frame tick
count. `--latest-with-frames` selects retained-frame ticks joined back to raw
ticks; the other selectors use raw tick records.

For visual QA against retained frames, prefer a frame-aware slice:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --target-type all --latest-with-frames 100
python telemetry-viewer\select_target_candidates.py --session "C:\path\to\session" --latest 100 --target-type all --only-on-screen --geometry-available --limit 500 --summary
```

Newer raw ticks may include `sceneCaptureSummary`, a diagnostic-only counter
object. It reports scene capture mode, bounded scan radius or full-plane scan,
configured max scene object cap, scanned tile bounds, how many scene objects
were seen versus captured, and how many game/wall/decorative/ground objects
were skipped by the cap. For example, it lets the diagnostic say that Java saw
600 scene objects but emitted only 250 because the payload cap was hit, or that
`FULL_CURRENT_PLANE_DIAGNOSTIC` saw and captured all scene objects in the
selected scan. The summary is counters only; it does not add overlays, input,
clicks, menu actions, automation, or client-state mutation.

Example commands:

```text
python telemetry-viewer\diagnose_target_coverage.py --latest 25
python telemetry-viewer\diagnose_target_coverage.py --all-ticks
python telemetry-viewer\diagnose_target_coverage.py --tick 201
python telemetry-viewer\diagnose_target_coverage.py --tick 201 --json
python telemetry-viewer\diagnose_target_coverage.py --latest 25 --scenario tree_cutting
python telemetry-viewer\diagnose_target_coverage.py --object-id 1276
python telemetry-viewer\diagnose_target_coverage.py --near 3200 3200 5
python telemetry-viewer\diagnose_target_coverage.py --project-root C:\Users\stone\osrs-telemetry\example-plugin --latest 5
```

If Java capture is proven to be the bottleneck, prefer debug-only expanded
capture, explicit skip counters, or event-maintained object inventories before
considering full-scene every-tick scans. Full-scene scanning is intentionally a
last resort because it can be expensive and noisy.

## Scene Capture Raw-Force Diagnostic Modes

The RuneLite plugin has a **Scene capture mode** config for short read-only dev
captures:

- `LOCAL_DEFAULT`: preserves the old behavior. It scans the current plane around
  the local player with radius `12` and caps scene objects at `250` per tick.
- `WIDE_DIAGNOSTIC`: scans radius `32` on the current plane and raises the scene
  object cap to `10000`. Use it for short diagnostic sessions when local capture
  is capped.
- `FULL_CURRENT_PLANE_DIAGNOSTIC`: scans every tile on the current plane and
  raises the scene object cap to `25000`. Use it only for short raw-force
  coverage checks.
- `STATIC_SCENE_INDEX_DIAGNOSTIC`: builds a read-only current-plane scene object
  memory/index, maintains it with object spawn/despawn events where available,
  and emits compact `sceneObjectDeltas` plus projected `visibleSceneObjectRefs`
  instead of repeating every unchanged static object in `sceneObjects` each tick.
  This is the preferred long diagnostic mode after raw-force completeness has
  been validated.

The heavy modes stay read-only, but they can produce much larger JSONL tick
files. Expect higher disk usage, slower derived builders, more inspector load,
and greater writer queue pressure. Later optimization should use static scene
indexing or deduplication instead of repeating unchanged scenery every tick.

Raw ticks include `sceneCaptureSummary` when scene capture runs. Check:

- `sceneCaptureMode`
- `fullCurrentPlaneScan`
- `configuredRadius`
- `configuredMaxSceneObjects`
- `scanWidth` / `scanHeight`
- `sceneObjectsSeen`
- `sceneObjectsCaptured`
- `sceneObjectsSkippedByCap`
- `sceneObjectCapHit`
- `captureRatio`
- layer counts such as `gameObjectsSeen`, `gameObjectsCaptured`, and
  `gameObjectsSkippedByCap`
- performance pressure fields such as `sceneCaptureDurationMillis`,
  `snapshotBuildDurationMillis`, `writerQueueSize`, and `writerDroppedRecords`

In `STATIC_SCENE_INDEX_DIAGNOSTIC`, also check `sceneIndexSummary` and
`sceneProjectionSummary`: index/present object counts, new/updated/despawned
counts, full-resync ticks, resync reason, projection refresh mode, objects
updated/reused, visible refs, and projection duration. `objectKey` identifies a
scene object by plane, world/scene tile, layer, id, hash when available, and
orientation, so same-id objects at different locations stay distinct.

`build_world_target_geometry.py` supports both legacy/full snapshot
`sceneObjects` and static-index ticks. In static mode it uses
`visibleSceneObjectRefs` for per-tick world targets and writes
`interaction_geometry\scene_static_index.jsonl` with one compact record per
unique scene object. The world geometry index reports `sourceSchema`,
`objectKeySupport`, static index counts, and per-tick projected object counts.

Example raw-force QA workflow:

1. Set **Scene capture mode** to `WIDE_DIAGNOSTIC` or
   `FULL_CURRENT_PLANE_DIAGNOSTIC` in the plugin config.
2. Collect a short session.
3. Build world targets:

   ```text
   python telemetry-viewer\build_world_target_geometry.py --session "<session>" --latest 25 --target-type all
   ```

   Use `--all-ticks` instead of `--latest 25` for a whole-session build, or
   `--range START END` for a fixed slice. If the summary says only one tick was
   selected, the raw session or selector only provided one tick. If you need
   retained-frame overlap for visual QA, use `--latest-with-frames N`.

4. Select ranked candidates:

   ```text
   python telemetry-viewer\select_target_candidates.py --session "<session>" --target-type all --limit 500 --summary
   ```

   Candidate selection deduplicates same-tick same-object/same-aim records by
   default before applying `--limit`. When `objectKey` is present, it is the
   primary identity key, so same-id objects at different locations remain
   separate. Use `--limit 0` or `--no-limit` for unlimited output after dedupe.
   Use `--no-dedupe` only when debugging raw duplicate inputs.

5. Suggest manual label/category overrides for fallback objects:

   ```text
   python telemetry-viewer\suggest_target_overrides.py --session "<session>" --limit 25
   ```

   This read-only helper prints fallback/unclassified scene object IDs, sample
   locations/object keys, and manual `target_name_overrides.json` skeletons. It
   does not edit override files automatically.

6. Diagnose capture and pipeline coverage:

   ```text
   python telemetry-viewer\diagnose_target_coverage.py --session "<session>" --latest 25 --performance --project-root "C:\Users\stone\osrs-telemetry\example-plugin"
   ```

7. Verify cap-hit ticks, objects seen/captured/skipped by cap, capture ratio,
   world target count, and candidate count.

10. Select ranked target candidates from existing geometry:

   ```text
   python telemetry-viewer\select_target_candidates.py --category bank --only-on-screen --geometry-available
   ```

   This writes:

   ```text
   interaction_geometry\target_candidates.jsonl
   interaction_geometry\target_candidates_index.json
   ```

   Candidate selection ranks existing UI/world geometry records and preserves the
   best available aim geometry, preferring clickboxes, then hulls, tile polygons,
   UI boxes, and points. The output is a read-only handoff/analysis layer: it
   does not send mouse input, create click commands, invoke menus, interact with
   RuneLite, or modify raw telemetry or frame images.
   When player and target world positions are available, the ranker also records
   Chebyshev/Manhattan tile distances and prefers closer entity/NPC candidates;
   distance is a moderate tie-breaker for interactable world objects.
   The ranker collapses duplicate-looking records with the same tick, target
   type, id, world location, and aim point before applying `--limit`; the summary
   reports matching targets before dedupe, duplicates removed, candidates before
   limit, and final candidate count.
   `target_geometry_inspector.py` can load these candidate files and draw ranked
   aim points/preferred geometry alongside the raw UI/world overlays.

11. Export a read-only target handoff:

   ```text
   python telemetry-viewer\export_target_handoff.py --category bank --limit 10
   ```

   This writes:

   ```text
   interaction_geometry\handoff\latest_candidates.json
   interaction_geometry\handoff\latest_candidates.jsonl
   interaction_geometry\handoff\handoff_index.json
   ```

   The handoff files contain ranked candidate geometry for external analysis or
   private-server experiments. They preserve aim points, preferred geometry, and
   scoring reasons, but they do not contain mouse movement, click commands,
   keyboard input, menu actions, or automation instructions.

12. Build a read-only scenario dataset:

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario bank_area
   ```

   Scenarios group useful ranked target candidates by purpose. The first
   template, `bank_area`, selects visible bank-related candidates such as bank
   booths, deposit boxes, bankers, and deposit targets, then preserves nearby
   obstacle/navigation context when available.

   A second template, `goblin_area`, selects visible Goblin NPC candidates and
   preserves nearby obstacle/navigation context:

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario goblin_area
   python telemetry-viewer\scenario_inspector.py --scenario goblin_area --port 8810
   ```

   A third template, `tree_cutting`, selects visible tree scene-object
   candidates and preserves nearby obstacle/navigation context. It is for
   read-only tree target geometry QA only; it does not chop trees, click, send
   input, or generate actions.

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario tree_cutting
   python telemetry-viewer\scenario_inspector.py --scenario tree_cutting --port 8810
   ```

   Scenario output is written to:

   ```text
   scenario_datasets\bank_area.jsonl
   scenario_datasets\scenario_index.json
   ```

   Scenario records are geometry/context only. They do not generate mouse
   movement, clicks, keyboard input, menu actions, client-state mutation, or
   automation. Target candidates are ranked geometry records, not commands.
   The scenario builder de-duplicates selected candidates per tick before
   applying `limitPerTick`, so repeated records for the same visible object are
   collapsed into one scenario candidate.

   To visually QA a scenario dataset, run:

   ```text
   python telemetry-viewer\scenario_inspector.py --scenario bank_area
   ```

   Open `http://127.0.0.1:8810/`. The scenario inspector overlays selected
   candidates and optional obstacle/navigation context on retained frame images.
   Context overlays are quiet by default, and the inspector can hide tile
   context or limit context target types so selected targets remain readable.
   It is read-only scenario QA: browser clicks only select rows or overlay
   details inside the local page, and the tool does not interact with RuneLite,
   modify telemetry, modify frame images, or generate input/actions.

Save behavior:

- **Save Default Profile** writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json` and
  initializes future sessions.
- **Save Session Profile** writes
  `sessions\<session_id>\perception\screen_regions.json` and affects only that
  session.
- Existing sessions keep their own session-local profile unless explicitly
  overwritten.

To prepare tick-aligned visual review records from the derived perception
dataset:

```text
python telemetry-viewer\prepare_visual_perception.py
```

The default mode writes `perception\visual_perception_index.json` and
`perception\visual_tick_records.jsonl` with normalized and pixel screen-region
metadata only. It uses Python standard library code and does not crop images.

To attempt derived crop files for a small sample:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --limit 25
```

By default, visual prep includes `baseRegions` only unless you provide an active
tab or ask for all tab profiles. Use an explicit tab when you know which side
tab is visible in the selected frames:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab auto
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab prayer
```

`--active-tab auto` uses the active side-tab inference generated into
`perception\tick_bundles.jsonl`. Detection priority is manual override, widget
inference, event inference, visual fallback if implemented, then `unknown`.
Review inference output with:

```text
python telemetry-viewer\inspect_tab_detection.py --limit 25
```

Manual overrides such as `--active-tab prayer` apply that profile to every
selected tick and mark the active-tab source as `manual`. When auto inference
returns `unknown`, visual prep includes `baseRegions` only and records skipped
tab profiles unless `--include-all-tab-profiles` is set.

To include every tab profile in one derived pass:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
```

For sessions where old frame images may have been deleted by retention, select
newer or existing-frame records:

```text
python telemetry-viewer\prepare_visual_perception.py --latest 25 --only-existing-frames
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames
python telemetry-viewer\prepare_visual_perception.py --generate-crops --generate-grid-slots --latest 25 --only-existing-frames
```

`--latest` selects the newest matching ticks. `--only-existing-frames` is useful
when older tick bundles still exist but their source frame images were already
removed by retention. Crop mode now prefers existing-frame ticks by default
when possible so `--generate-crops --limit N` is less likely to spend the whole
sample on missing retained frames. `--generate-grid-slots` derives slot crops
for grid regions, such as inventory, only when explicitly requested.

Profile-aware test crops are grouped by run id and profile name:

```text
perception\test_crops\<run_id>\tick-XXXXXXXX\base\chatbox.jpg
perception\test_crops\<run_id>\tick-XXXXXXXX\inventory\inventoryGrid.jpg
perception\test_crops\<run_id>\tick-XXXXXXXX\prayer\prayerGrid.jpg
```

Crop mode requires Pillow to already be available. The script does not install
dependencies. If Pillow is unavailable, it prints a warning and continues in
metadata-only mode. Generated visual-prep crops are disposable test crops under
`perception\test_crops\<run_id>\`; source frame images and raw telemetry files
are not modified. The visual prep tool is read-only derived analysis data and
performs no automation, clicking, input hooks, overlays, menu actions, or
client-state mutation.

Screen regions may use typed records:

- `rect`: normalized rectangle box.
- `circle`: normalized center plus radius.
- `ellipse`: normalized center plus X/Y radii and optional rotation metadata.
- `grid`: normalized outer box plus rows, columns, and slot count.

Old-style `{ "x": ..., "y": ..., "w": ..., "h": ... }` regions are still read
as rectangles. Inventory grids should use `rows=7`, `cols=4`, and
`slotCount=28` under `tabProfiles.inventory`; visual prep derives the 28 slot
boxes from the calibrated grid geometry. Equipment and prayer can use their own
grid or slot regions under `tabProfiles.equipment` and `tabProfiles.prayer`.

If crop boxes look off, render region-calibration previews:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame
```

Open the generated overlay and contact sheet under:

```text
perception\region_calibration\
```

Adjust a region with a pixel nudge:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --nudge inventory 20 -10 0 15 --output-calibrated
```

Or set normalized values directly:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --set-region inventory 0.700 0.300 0.250 0.400 --output-calibrated
```

Calibration is a read-only preview unless `--write-screen-regions` is used. It
writes proposed values to
`perception\region_calibration\calibrated_screen_regions.json`, and only
overwrites the derived `perception\screen_regions.json` when explicitly asked:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --nudge inventory 20 -10 0 15 --write-screen-regions
```

After accepting calibrated regions, rerun visual perception crops:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames
```

For a local browser calibration UI:

```text
python telemetry-viewer\calibrate_screen_regions.py --interactive --latest-existing-frame
```

Open:

```text
http://127.0.0.1:8770/
```

The simplest workflow is to start from the launcher:

1. Run `python telemetry-viewer\telemetry_launcher.py`.
2. Click **Start Core Stack**.
3. Log into RuneLite.
4. Click **Start Calibration Mode**.
5. If needed, click **Refresh to newest frame**.
6. Calibrate base regions and the active tab profile, such as inventory,
   prayer, or equipment.
7. Use **Save Session Profile** for this session only, or **Save Default
   Profile** for future sessions.
8. Click **Generate Test Crops**.
9. Inspect the latest test crop run under the current session perception
   folder.

The launcher Calibration section includes **Start Calibration Mode**, **Open
Calibration UI**, **Generate Test Crops**, **Open Latest Test Crops**, and
**Open Calibration Profile Folder**. The Dataset section includes **Build
Training Dataset**, **Build Training Dataset Rebuild**, and **Open Training
Data Folder**.

The UI lets you refresh to the newest frame, use the latest existing frame,
drag boxes on the captured frame, edit pixel `x/y/w/h` values, switch region
type between `rect`, `circle`, `ellipse`, and `grid`, select **Base regions**
or a tab profile, show base and active-tab regions with distinct styling, add
custom tab profiles, add new region categories, rename or duplicate regions,
delete regions, and edit tags. Adding a region while the Inventory profile is
selected adds it under `tabProfiles.inventory`.

Persistence behavior:

- **Save Session Profile** writes only the selected session's
  `sessions\<session_id>\perception\screen_regions.json`.
- **Save Default Profile** writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json`.
- Future sessions initialize from the default profile when
  `build_perception_dataset.py` creates their first `screen_regions.json`.
- Existing sessions keep their own session-local calibration unless explicitly
  overwritten.
- **Load default profile** loads that profile into the UI without overwriting
  the session file.

Test crops are disposable previews under
`sessions\<session_id>\perception\test_crops\<run_id>\`. They are not the final
dataset. Persistent training data lives under
`sessions\<session_id>\training_data\`, and training crops live under
`sessions\<session_id>\training_data\crops\`. Normal training builds skip
duplicates and do not wipe previous training data; only `--rebuild` clears and
rebuilds `training_data`.

For smaller reviewable datasets, prefer:

```text
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
```

The `review` preset drops broad low-value base crops such as full frame,
viewport, side panel, and tabs. The `focused-ui` preset concentrates on side-tab
profiles plus useful base context such as chatbox and minimap.

Use **Save calibrated copy** for
`perception\region_calibration\calibrated_screen_regions.json`, and only click
**Write screen_regions.json** when you are ready to update the derived session
`perception\screen_regions.json`.

For inventory calibration, add or select a grid region and set:

```text
rows=7
cols=4
slotCount=28
```

Then rerun visual perception crops:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --generate-grid-slots --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
```

Mouse clicks in the calibration UI are local to the browser page. The UI does
not interact with RuneLite or the game client. It does not modify raw telemetry
or source frame images, and it only edits the derived
`perception\screen_regions.json` after the explicit write button is clicked.

The calibration tool uses existing frame images only. It does not modify raw
telemetry or source frame images, and Pillow must already be available because
the tool does not install dependencies.

The replay viewer includes a read-only **Analysis** panel derived from the
existing tick, event, frame, and frame-index telemetry. It does not collect new
gameplay data and does not add overlays, input hooks, clicking, menu
manipulation, automation, recommendations, or client-state mutation.

The Analysis panel provides:

- Summary cards for session, tick, event, and frame statistics, including frame
  write-delay diagnostics when available.
- A compact per-tick timeline that can jump the main replay view to the selected
  tick without reloading the page.
- Timeline filters for event category, `eventType` text, ticks with events, and
  frame/capture issues.
- Combat Events, Inventory/Skilling Events, and UI/Menu Events quick panels for
  inspection and replay review only.
- Internally scrolling tables so the frame display and replay controls remain
  usable while reviewing longer sessions.

The right side of the replay viewer is organized into State, Analysis, Events,
and Raw tabs. State shows the selected tick and frame timing, Analysis shows the
derived session/timeline view, Events shows nearby event records, and Raw keeps
tick/event JSON collapsed until opened.

Keyboard shortcuts are local to the replay page and are ignored while typing in
search or jump inputs:

- `ArrowLeft` / `ArrowRight`: previous or next tick.
- `Space`: play or pause replay.
- `S`, `A`, `E`, `R`: switch to State, Analysis, Events, or Raw.

Example questions:

- When did HP drop?
  Compare `hpBoosted` across consecutive tick summaries.

- What NPC was I interacting with?
  Inspect `interactingTarget` on tick summaries, or `InteractingChanged` events.

- What item container changed?
  Filter event summaries where `eventType == "ItemContainerChanged"`.

- What menu options were available?
  Filter event summaries where `eventType == "MenuOpened"` and inspect the
  compact summary or the source event payload.

- What prayers were active?
  Read `activePrayerNames` from tick summaries.

- What was nearby when an event happened?
  Join `event_summary.tickId` to `tick_summary.tickId`, then inspect nearby
  entity/object counts or the original tick record.

- Is there a screenshot for a tick?
  Read `framePath`, `frameExists`, `framePending`,
  `frameExpiredOrMissing`, `frameCaptureStatus`, and `frameCaptureSource` from
  `exports\tick_summary.jsonl`. Missing files with a historical `framePath`
  usually mean frame retention has expired the image.
  If `frameCaptureSource` is `SCREEN_RECTANGLE`, check `frameCaptureWarning`
  because overlapping windows may appear in that frame.

- How long did the frame write take?
  Read `frameWritten`, `frameWriteDelayMs`, `frameTotalLatencyMs`, and
  `frameIndexStatus` from `exports\tick_summary.jsonl` when available. For
  earlier pipeline timing, read `frameCaptureLatencyMs` and
  `frameQueueLatencyMs`. For the full lifecycle record, inspect
  `exports\frame_index_summary.jsonl` or the raw source sidecar at
  `frames\frame_index.jsonl`.

- Why does `frameExists` briefly show false?
  Frame writes are asynchronous. `frameCaptureStatus == "QUEUED"` means the
  frame capture/write was requested. For the newest active tick, `framePending
  == true` means the image may still be arriving inside the shared freshness
  grace window. For older ticks, a missing frame is reported as
  `frameExpiredOrMissing == true`.

- Why do deleted or expired frames appear in validation?
  `validate_session.py` reports deleted/expired frame-index counts so retention
  behavior is visible. Those records are informational by themselves; the
  original tick remains valid unless there is a real JSON/schema/required-field
  problem.

## Rolling live target processor

The batch target pipeline remains the best path for durable debug sessions,
training data, and reproducible QA:

```text
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest 25 --profile broad_qa --limit 2000
```

For local live QA, use `live_target_processor.py`. It tails the selected
session's raw tick JSONL files, keeps a rolling in-memory tick window, converts
the current window to world target geometry and ranked candidate packets, and
writes rolling derived files under:

```text
sessions\<session_id>\interaction_geometry\live\
```

The live processor does not click, send input, invoke menus, route actions, or
mutate client state. It also does not permanently archive every tick by default;
the live files represent the current rolling window and are safe to overwrite.
Full snapshot debug capture modes remain available for short diagnostic runs.

Primary outputs:

- `live_world_targets.jsonl`: rolling world target geometry records.
- `live_ui_targets.jsonl`: optional copied UI target records for the window.
- `live_candidates.jsonl`: rolling ranked candidate packets.
- `live_tick_summary.jsonl`: per-tick live processing summaries.
- `live_status.json`: current processor status, counts, warnings, and latest
  tick.
- `live_index.json`: paths and metadata for the rolling live files.

Example one-shot live QA pass:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile broad_qa --window-ticks 100 --once --summary
```

Example follow-mode woodcutting QA pass:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --window-ticks 100 --follow --exclude-ui-blocked --limit 500 --summary
```

`--exclude-ui-blocked` automatically includes existing `ui_targets.jsonl` records
for the rolling window when they are available, because UI blockers are derived
from calibrated UI geometry.

Open the inspector against rolling live output:

```text
python telemetry-viewer\target_geometry_inspector.py --session "<session>" --live
```

Use the batch pipeline when you need stable files for analysis or training. Use
the rolling live processor when you want the latest targets/candidates to update
while a short read-only telemetry session is being collected.

## Fast live context layer

The live processor is candidate/profile-first by default. Future consumers
should prefer the small live context files:

- `live_baseline_state.json`: latest compact player/camera/frame/scene/cache
  and candidate summary.
- `live_context_index.json`: query-ready best/nearest candidates by class,
  candidate counts, and live file paths.
- `live_candidates.jsonl`: selected read-only target context packets.
- `live_navigation_summary.json`: current navigation/collision placeholder.
- `live_status.json`: timing, output byte counts, source cap status, and live
  health.

`live_world_targets.jsonl` is no longer the primary live output. Full broad
world target output can be huge in full current-plane sessions because it
contains every captured object/tile target. Suppressing or limiting this file
does not mean source scene knowledge is lost: raw ticks and scene summaries are
still read, source cap status is reported, and cached/profile-filtered world
records still feed candidate selection.

World target output policy:

- `--emit-world-targets none`: do not write `live_world_targets.jsonl`.
- `--emit-world-targets candidates`: write only world records backing selected
  candidates. This is the recommended default.
- `--emit-world-targets profile`: write profile-matching world targets before
  final candidate limit.
- `--emit-world-targets visible`: write on-screen world targets, capped by
  `--world-target-output-limit`.
- `--emit-world-targets full`: write the broad world layer. This is debug-only
  and can be very large.

Startup/follow behavior:

- `--startup-backfill-ticks N` limits initial catch-up. Default is `10`.
- `--no-startup-backfill` starts from the current end of the tick files and
  only processes newly appended records.
- `--process-existing` processes the current rolling window before following.
- Follow mode reuses cached per-tick work for older ticks and processes only
  newly appended ticks unless `--force-window-rebuild` is passed.

`live_status.json` includes timing buckets and byte counts: tail read, JSON
parse, raw tick ingest, baseline, world target build/filter, candidate
selection, context index, UI target load, output write, total duration, output
bytes, `budgetExceeded`, and `warningUpdateExceeded`.

Fast one-shot woodcutting context test:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --once --startup-backfill-ticks 5 --window-ticks 5 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Fast follow mode:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --follow --no-startup-backfill --window-ticks 50 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

With UI blocking:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --follow --startup-backfill-ticks 25 --window-ticks 100 --limit 500 --include-ui-targets --exclude-ui-blocked --emit-world-targets candidates --summary --benchmark
```

Debug full world output:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile broad_qa --once --window-ticks 5 --limit 2000 --emit-world-targets full --summary --benchmark
```

Live inspector:

```text
python telemetry-viewer\target_geometry_inspector.py --session "<session>" --live
```

Read-only query helper:

```text
python telemetry-viewer\live_context_query.py --session "<session>" --summary
python telemetry-viewer\live_context_query.py --session "<session>" --nearest tree --json
python telemetry-viewer\live_context_query.py --session "<session>" --nearest oak_tree --max-distance 30 --json
python telemetry-viewer\live_context_query.py --session "<session>" --baseline --json
```

The query helper only reads live files. It does not click, send input, invoke
menus, perform routing, or generate actions.

## Live Context QA

`telemetry-viewer\live_context_query.py` is a read-only mock context oracle for
the rolling live files. It validates whether the current telemetry can answer
brain-facing context questions such as where the player is, which useful target
candidates are nearby, whether a candidate is on screen, whether it has an aim
point telemetry field, whether that point is UI-blocked, whether the live feed
is fresh, and whether source scene knowledge appears complete.

It does not execute actions, choose clicks, send mouse or keyboard input,
manipulate menus, interact with RuneLite, or mutate telemetry. Screen
coordinates and aim points are reported only as read-only telemetry fields for
QA and future consumers.

Response schemas are versioned:

- `live_context_summary.v1` for `--summary`
- `live_context_answer.v1` for `--nearest` and `--best`
- `live_task_context.v1` for `--task woodcutting`
- `live_context_self_test.v1` for `--self-test`

Use `--summary` to inspect baseline state, freshness, player location,
candidate counts, source cap status, processor budget status, and live file
warnings. Use `--nearest tree` or `--best tree` to inspect candidate quality.
Use `--task woodcutting` to check whether the live files can answer the core
woodcutting context questions without implying any action. Use `--self-test`
before longer experiments to confirm the baseline/status/context/candidate
files are readable, fresh, and source capture is not capped.

Example commands:

```text
python telemetry-viewer\live_context_query.py --latest-session --summary
python telemetry-viewer\live_context_query.py --latest-session --nearest tree --json
python telemetry-viewer\live_context_query.py --latest-session --best tree --json
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --json
python telemetry-viewer\live_context_query.py --latest-session --self-test
python telemetry-viewer\live_context_query.py --latest-session --watch --nearest tree --interval 1
```

Navigation readiness is intentionally a placeholder unless a future read-only
collision summary is available. When collision data is missing, the task report
returns navigation readiness as `unknown` and warns that reachability questions
cannot be answered yet.

## Realtime live mode versus complete live mode

`live_target_processor.py` now has two latency modes:

- `--latency-mode realtime` prioritizes the latest useful context. If raw tick
  backlog grows, it can coalesce intermediate ticks and process only the newest
  tick or newest small batch for live output.
- `--latency-mode complete` processes every selected tick in order. Use it for
  QA/debug runs where completeness of the derived rolling output matters more
  than latency.

Coalescing live output is not capture limiting. The raw tick files and static
scene index summaries can remain complete while realtime output writes only the
latest profile-specific candidates. `live_status.json` reports
`sourceSceneKnowledgeComplete`, `sourceCapHit`, `sceneObjectsSeen`,
`sceneObjectsCaptured`, and `sceneObjectsSkippedByCap` so you can tell whether
source coverage is intact.

Realtime mode also applies early profile prefiltering before building full world
target packets. For example, the woodcutting profile cheaply rejects non-tree
scene objects before detailed candidate geometry is built. This keeps the active
profile fast without reducing the Java static scene index or raw capture.

Important status fields:

- `latencyMode`
- `candidateOutputWindow`
- `coalescedBacklogTicks`
- `processedTickIds`
- `latestRawTickSeen`
- `latestTickProcessed`
- `worldTargetSourceRecordsConsidered`
- `worldTargetsPrefilteredOut`
- `classificationCacheHits` / `classificationCacheMisses`
- `candidateTickCacheHits` / `candidateTickCacheMisses`
- `timingMode`, currently `nested`

Fast realtime woodcutting:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --follow --latency-mode realtime --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 50 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Complete QA processing:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile woodcutting --once --latency-mode complete --startup-backfill-ticks 25 --window-ticks 25 --limit 500 --emit-world-targets candidates --summary --benchmark
```

Debug broad world:

```text
python telemetry-viewer\live_target_processor.py --latest-session --profile broad_qa --once --latency-mode complete --window-ticks 5 --limit 2000 --emit-world-targets full --summary --benchmark
```
