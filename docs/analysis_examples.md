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
   `target_geometry_inspector.py` can load these candidate files and draw ranked
   aim points/preferred geometry alongside the raw UI/world overlays.

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
