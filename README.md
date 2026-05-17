# OSRS Telemetry

Read-only RuneLite telemetry collector.

Telemetry sessions use the segmented layout as the canonical writer output:

```text
C:\Users\stone\.osrs-telemetry\sessions\<session_id>\
  manifest.json
  ticks\ticks-*.jsonl
  events\events-*.jsonl
  frames\frame-tick-XXXXXXXX.jpg
  frames\frame_index.jsonl
  dictionaries\
  latest\
  exports\
```

The viewer tools also support older read-only sessions that used flat
`ticks.jsonl` and `events.jsonl` files.

To view the newest session:

```powershell
python telemetry-viewer\viewer.py
```

## Telemetry Control Center

`telemetry-viewer` is currently the Python telemetry toolchain, not only a
viewer. User-facing scripts remain at the `telemetry-viewer` root for command
compatibility. A future cleanup may rename or split this folder as
`telemetry-tools`.

Launch the local Tkinter control center from the project root:

```powershell
python telemetry-viewer\telemetry_launcher.py
```

Most users should use the launcher. Individual scripts still exist for
debugging and advanced use.

Happy Path:

1. Start Collection
2. Wait for the launcher to lock a fresh active session
3. Calibrate if needed, then Save Default Profile
4. Replay / label tick ranges
5. Build Dataset
6. Inspect Dataset
7. Export Curated
8. Run Doctor / Status

The launcher starts and stops local dev tools and read-only telemetry scripts.
It defaults to:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

You can override the sessions directory in the GUI. The launcher passes that
value to child tools through `OSRS_TELEMETRY_SESSIONS_DIR`.

Simple Mode shows the main workflow buttons:

- Start Collection: launches RuneLite and waits for a fresh active session.
- Stop Collection: stops launcher-started collection processes.
- Open Replay / Label: reviews ticks, frames, events, and tab labels.
- Open Calibration: edits screen regions and tab profiles.
- Build Dataset: runs perception, training review preset, then status.
- Inspect Dataset: opens the training data inspector.
- Export Curated: exports curated train/val/test manifest data.
- Run Doctor / Status: checks profiles, labels, sessions, training data, and
  curated output.

Start Collection intentionally does not open replay, calibration, or other data
consumers right away. It records the launch time, ignores old stale sessions,
and waits until a session has fresh ticks before locking that active session.
Tools that support `--session` then receive the locked active session path, so
they do not silently inspect yesterday's newest folder.

Advanced mode is for debugging and lower-level commands only. It contains
manual RuneLite/latest-state/viewer starts, individual build/export commands,
folder shortcuts, raw session export, validation, path tests, and process
controls.

Key dataset/export commands behind the launcher buttons:

```powershell
python telemetry-viewer\build_perception_dataset.py
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots --rebuild
python telemetry-viewer\export_curated_training_dataset.py
python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
python telemetry-viewer\dataset_status.py
```

The Telemetry Health panel focuses on active-session lock state, active session
path, latest tick age, label/default-profile presence, perception bundles,
training examples, curated examples, missing crops, warning count, and the
latest validation/status result.

Health status colors:

- OK: active session and latest tick age is under 10 seconds.
- Warning: latest tick age is 10-60 seconds.
- Stale: latest tick is over 60 seconds old or no ticks were found.

Health quick actions open existing telemetry files without creating or editing
them:

- Open latest frame file
- Open `latest_status.json`
- Open `manifest.json`
- Open newest tick segment
- Open newest event segment
- Open active/newest session folder

The launcher only manages processes it started. It does not control the game
client or mutate client state.

Frame timing diagnostics are written as line-oriented JSONL at
`frames\frame_index.jsonl`. The tools expose normalized frame timing fields
where available: `frameWritten`, `frameWriteDelayMs`, `frameTotalLatencyMs`,
`frameCaptureLatencyMs`, `frameQueueLatencyMs`, `frameIndexStatus`, and
`latestFrameIndexEvent`. `latest_state.py` writes those fields into the
generated latest-state cache, `replay_viewer.py` shows them for the selected
tick, `telemetry_launcher.py` shows the latest timing and lifecycle counts in
Telemetry Health, and `export_session.py` writes
`exports\frame_index_summary.jsonl` plus session/tick summary fields.
`validate_session.py` reports dropped, failed, and deleted frame counts clearly;
normal expired/deleted frames are not validation failures by themselves.

## Derived Visual Calibration

The perception and visual-prep tools use derived screen-region calibration
files. Session-local calibration lives at `perception\screen_regions.json`.
Reusable defaults live at
`telemetry-viewer\calibration_profiles\default_screen_regions.json`.

Streamlined calibration workflow:

1. Launch the toolchain:

   ```powershell
   python telemetry-viewer\telemetry_launcher.py
   ```

2. Click **Start Collection**.
3. Log into the RuneLite development client and wait for the launcher to lock
   a fresh active session.
4. Click **Open Calibration**.
5. If the displayed frame is stale, click **Refresh to newest frame** or
   **Use latest existing frame** in the calibration UI.
6. Calibrate **Base regions** and the active tab profile, such as
   **Inventory**, **Prayer**, or **Equipment**.
7. Click **Save Default Profile** to initialize future sessions, or **Save
   Session Profile** to update only the current session.
8. Label active side-tab ranges with `telemetry-viewer\tab_labels.json`, or use
   the replay labeling UI if present.
9. Click **Build Dataset** to build perception and persistent training data.
10. Use **Inspect Dataset** to review generated training examples.
11. Use **Export Curated** when you are ready to create the clean split
    manifest.
12. Use **Run Doctor / Status** to inspect health:

    ```powershell
    python telemetry-viewer\dataset_status.py
    ```

Disposable test crops are still available from Advanced > Generate Test Crops
or from the calibration UI when you need quick visual previews.

You can also launch the local browser inspector directly:

```powershell
python telemetry-viewer\training_dataset_inspector.py
```

Optional forms:

```powershell
python telemetry-viewer\training_dataset_inspector.py --port 8790
python telemetry-viewer\training_dataset_inspector.py --session "C:\path\to\session"
```

Open `http://127.0.0.1:8790/`. You are not expected to manually review every
crop. Start with **Review Queue** set to **Balanced by regionProfile**, size
`100`, and `cropExists=true`; then mark examples **Good**, **Bad Crop**,
**Wrong Label**, or **Unsure**. If training data has not been built, the page
shows: `Run python telemetry-viewer\build_training_dataset.py first.`

The launcher Simple Workflow contains:

- Start Collection
- Stop Collection
- Open Replay / Label
- Open Calibration
- Build Dataset
- Inspect Dataset
- Export Curated
- Run Doctor / Status

Show Advanced only when you need lower-level controls such as individual
perception/training/export commands, test crop generation, folder shortcuts,
validation, path tests, or process controls.

The calibration UI includes:

- Refresh to newest frame
- Use latest existing frame
- Save Session Profile
- Save Default Profile
- Load default profile
- Generate Test Crops
- Open perception folder
- Open profile folder

Screen-region profiles are tab-aware. `baseRegions` are always-valid frame
areas such as the viewport, minimap, chatbox, side panel, tabs, or orbs.
`tabProfiles` are side-tab-specific regions. Inventory, equipment, prayer,
magic, and other side-panel tabs need separate crop profiles because the same
screen area contains different widgets depending on which tab is open.

When `build_perception_dataset.py` needs to create a session calibration file,
it loads regions in this order:

1. Existing session `perception\screen_regions.json`
2. `--calibration-profile "C:\path\to\profile.json"`
3. `telemetry-viewer\calibration_profiles\default_screen_regions.json`
4. Built-in approximate fallback regions

Launch the local calibration UI with:

```powershell
python telemetry-viewer\calibrate_screen_regions.py --interactive --latest-existing-frame
```

Open `http://127.0.0.1:8770/`. The UI can add, rename, duplicate, and delete
region categories, set tags, and edit `rect`, `circle`, `ellipse`, and `grid`
regions. Use the profile selector to edit **Base regions** or a side-tab
profile such as **Inventory**, **Equipment**, **Prayer**, or **Magic**. Custom
tab profiles can be added from the same panel. Inventory grids should live
under `tabProfiles.inventory` and use `rows=7`, `cols=4`, and `slotCount=28`.
Use **Save Default Profile** to update the reusable full
`baseRegions`/`tabProfiles` profile. Use **Write screen_regions.json** only
when you explicitly want to update the derived session calibration.

Persistence rules:

- Save Session Profile writes
  `sessions\<session_id>\perception\screen_regions.json`.
- Save Default Profile writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json`.
- Future sessions initialize from the default profile when
  `build_perception_dataset.py` creates their first `screen_regions.json`.
- Existing sessions keep their own session-local calibration unless explicitly
  overwritten.

Test crops and training data are intentionally separate:

- Test crops are disposable preview/verification crops, not the final dataset.
- Test crops are written under
  `sessions\<session_id>\perception\test_crops\<run_id>\`.
- Test crop generation does not wipe previous test crop runs by default.
- Persistent training data is written under
  `sessions\<session_id>\training_data\`.
- Training crops are written under
  `sessions\<session_id>\training_data\crops\`.
- Training data is non-destructive by default: existing examples are detected
  and skipped by key.
- Only `--rebuild` wipes `training_data` before rebuilding.

The training dataset inspector is for local QA/review of generated training
data. It shows summary cards, filters, crop thumbnails, labels, telemetry
summary, source frame images when available, and a detail panel for the
selected example. Use its review queues and quick filters to inspect a
manageable sample instead of working through every manifest row. Missing crop
diagnostics separate manifest examples, examples with crop files, and stale or
missing crop paths; if many crops are missing, filter to `cropExists=true` or
rebuild only when you intentionally want to replace `training_data`.
Review buttons append QA metadata to
`sessions\<session_id>\training_data\review_labels.jsonl`. That sidecar is
append-only: it does not modify raw telemetry, does not overwrite
`training_manifest.jsonl`, does not alter `training_index.json`, and does not
modify crops.

For smaller focused training builds, prefer the presets:

```powershell
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
```

`--include-missing-crops` is for diagnostics only. Normal training builds skip
examples whose crop file cannot be generated, and `--rebuild` is still required
before the builder clears existing persistent training data.

Export a clean curated manifest after QA review:

```powershell
python telemetry-viewer\export_curated_training_dataset.py
python telemetry-viewer\export_curated_training_dataset.py --reviewed-only
python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
```

The training dataset inspector is for QA, and you do not need to review every
crop. By default, review labels act as vetoes: unreviewed crop examples are
included, `good` examples are included, and latest `bad_crop`, `wrong_label`,
or `unsure` reviews are excluded. Use `--reviewed-only` for a strict export
that includes only latest `good` reviews. Curated outputs are written under:

```text
training_data\curated\curated_manifest.jsonl
training_data\curated\curated_index.json
```

`curated_manifest.jsonl` is the clean selected manifest for later
model/training experiments. The export does not copy crops, delete crops,
modify `training_manifest.jsonl`, or modify `review_labels.jsonl`.

After calibration, regenerate visual perception records or crops:

```powershell
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab auto
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab prayer
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
```

`--active-tab auto` uses the active side-tab inference stored in
`perception\tick_bundles.jsonl`. Detection priority is manual override, widget
inference, event inference, visual fallback if implemented, then `unknown`.
Inspect inference decisions with:

```powershell
python telemetry-viewer\inspect_tab_detection.py --limit 25
```

Manual overrides such as `--active-tab prayer` apply one tab profile to every
selected tick and record the source as `manual`. When the inferred active tab
is `unknown`, visual prep uses `baseRegions` only and records the skipped tab
profiles unless `--include-all-tab-profiles` is used.

Build deterministic UI target geometry from structured JSON plus calibration:

```powershell
python telemetry-viewer\build_ui_target_geometry.py
python telemetry-viewer\build_ui_target_geometry.py --include-base-regions --latest-with-frames 100
```

This writes screen-relative records under
`sessions\<session_id>\interaction_geometry\`. Inventory and equipment item IDs
come from raw tick JSON; pixel boxes and centers come from calibrated
`screen_regions.json`. The tool emits geometry records only. It does not use
vision, does not perform mouse actions, does not send input, and does not
modify raw telemetry or frame images.

Build read-only world target geometry from existing projected tick telemetry:

```powershell
python telemetry-viewer\build_world_target_geometry.py
python telemetry-viewer\build_world_target_geometry.py --only-on-screen --target-type sceneObject
python telemetry-viewer\build_world_target_geometry.py --target-type npc --only-on-screen --latest-with-frames 100
```

This writes `interaction_geometry\world_targets.jsonl` and
`interaction_geometry\world_geometry_index.json` for NPCs, players, scene
objects, ground items, and derived tiles when projection fields are present in
raw ticks. It exports canvas points, tile polygons, clickbox bounds/polygons,
visibility flags, and camera/viewport/canvas context. It does not use vision,
does not invent screen positions, does not generate mouse actions, and does not
modify raw telemetry or frame images.

Inspect UI and world target geometry in a local browser:

```powershell
python telemetry-viewer\target_geometry_inspector.py
```

Open `http://127.0.0.1:8800/`. The inspector overlays existing
`interaction_geometry` target records on retained frame images so you can check
alignment. It serves only from `127.0.0.1`, reads existing geometry/frame files,
and is QA-only: it does not interact with RuneLite, send input, generate mouse
actions, or modify telemetry, geometry, or frame files. If old retained frames
were deleted by retention, it shows a missing-frame placeholder while keeping
the geometry available for inspection.

If the inspector reports target records but `Frames available` is zero, compare
the target tick range with the retained frame tick range from
`dataset_status.py`. Geometry can outlive the JPGs it references. This usually
means the derived geometry was built for older ticks and frame retention now
only has newer images. Rebuild UI/world geometry for ticks whose frames are
still retained:

```powershell
python telemetry-viewer\build_world_target_geometry.py --target-type npc --only-on-screen --latest-with-frames 100
python telemetry-viewer\build_ui_target_geometry.py --include-base-regions --latest-with-frames 100
python telemetry-viewer\target_geometry_inspector.py
```

The `--latest-with-frames` option checks actual files under `frames\` instead
of trusting stale derived `frame.exists` flags. If there are no retained frame
files, collect a fresh session or increase the RuneLite config storage caps
(`Max telemetry GB` and `Max frame storage MB`) before collecting a longer QA
run. Retention should stay enabled unless you explicitly choose otherwise.

Test crops are grouped by run id and profile name, for example
`perception\test_crops\<run_id>\tick-XXXXXXXX\base\chatbox.jpg` and
`perception\test_crops\<run_id>\tick-XXXXXXXX\inventory\inventoryGrid.jpg`.
Grid slot test crops are only written when `--generate-grid-slots` is
explicitly added.

These are derived tooling steps only. They do not interact with RuneLite or the
game client, do not add automation, and do not modify raw telemetry or source
frame images. Mouse clicks in the calibration UI affect only the local browser
page.

On Windows, Stop Selected Process and Stop All Started Processes stop the
launcher-started process tree by PID with `taskkill /T /F`. The launcher does
not kill unrelated Java, Gradle, RuneLite, or Python processes by image name.
