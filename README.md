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

The launcher starts and stops local dev tools and read-only telemetry scripts.
It defaults to:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

You can override the sessions directory in the GUI. The launcher passes that
value to child tools through `OSRS_TELEMETRY_SESSIONS_DIR`.

Buttons:

- Start Core Stack: starts the RuneLite dev client, latest-state watcher, and
  replay viewer if they are not already running, then opens the replay URL.
- Start RuneLite Dev Client: runs the local Gradle dev client command.
- Start Text Viewer: starts `telemetry-viewer\viewer.py`.
- Start Latest State Watcher: starts `telemetry-viewer\latest_state.py`.
- Start Replay Viewer: starts `telemetry-viewer\replay_viewer.py`.
- Open Replay Viewer in Browser: opens `http://127.0.0.1:8765/`.
- Run Validate Session: runs `telemetry-viewer\validate_session.py`.
- Run Export Session: writes generated summaries with `telemetry-viewer\export_session.py`.
- Prepare Visual Perception: writes derived visual review metadata with
  `telemetry-viewer\prepare_visual_perception.py`.
- Start Calibration Mode: starts or reuses the RuneLite dev client, latest-state
  watcher, and local calibration UI, then opens `http://127.0.0.1:8770/`.
- Open Calibration UI: opens `http://127.0.0.1:8770/` without starting tools.
- Build Perception Dataset: writes derived per-tick perception bundles with
  `telemetry-viewer\build_perception_dataset.py`.
- Prepare Test Crops: runs visual crop prep with crop generation, grid slots,
  latest retained frames, existing-frame filtering, and the inventory profile.
- Open Calibration Profile Folder: opens
  `telemetry-viewer\calibration_profiles`.
- Open Current Session Perception Folder: opens the newest session's
  `perception` folder, or shows a friendly message if no session exists.
- Run Path Regression Tests: runs `telemetry-viewer\tests\test_telemetry_paths.py`.
- Open Sessions Folder: opens the configured sessions directory.
- Open Newest Session Folder: opens the newest discovered session.
- Stop Selected Process: stops a process started by this launcher.
- Stop All Started Processes: stops all processes started by this launcher.
- Clear Log: clears the launcher log panel.

The Telemetry Health panel shows the newest session path, active status, latest
tick id and age, game state, position, HP/prayer/run, tick/event/frame file
counts, latest frame write delay, latest total frame latency, latest frame index
status, FrameWritten/FrameDropped/FrameDeleted counts, perception bundle count
and visual perception record count when built, frame and session sizes, capture
errors, and the last validation result.

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
- Open newest session folder

Safety: the launcher only manages processes it started. It does not perform
game automation, clicking, input hooks, overlays, menu actions, or client-state
mutation.

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

2. Click **Start Core Stack**.
3. Log into the RuneLite development client.
4. Click **Start Calibration Mode**.
5. If the displayed frame is stale, click **Refresh to newest frame** or
   **Use latest existing frame** in the calibration UI.
6. Calibrate **Base regions** and the active tab profile, such as
   **Inventory**, **Prayer**, or **Equipment**.
7. Use **Save session calibration** for this session only, or **Save as
   default profile** to initialize future sessions.
8. Click **Prepare test crops**.
9. Inspect crops under the current session `perception` folder.

The launcher Calibration section contains:

- Start Calibration Mode
- Open Calibration UI
- Build Perception Dataset
- Prepare Test Crops
- Open Calibration Profile Folder
- Open Current Session Perception Folder

The calibration UI includes:

- Refresh to newest frame
- Use latest existing frame
- Save session calibration
- Save as default profile
- Load default profile
- Prepare test crops
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
Use **Save as default profile** to update the reusable full
`baseRegions`/`tabProfiles` profile. Use **Write screen_regions.json** only
when you explicitly want to update the derived session calibration.

Persistence rules:

- Saving session calibration writes
  `sessions\<session_id>\perception\screen_regions.json`.
- Saving as default profile writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json`.
- Future sessions initialize from the default profile when
  `build_perception_dataset.py` creates their first `screen_regions.json`.
- Existing sessions keep their own session-local calibration unless explicitly
  overwritten.

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

Visual crops are grouped by profile name, for example
`perception\crops\tick-XXXXXXXX\base\chatbox.jpg` and
`perception\crops\tick-XXXXXXXX\inventory\inventoryGrid.jpg`. Grid slot crops
are only written when `--generate-grid-slots` is explicitly added.

These are derived tooling steps only. They do not interact with RuneLite or the
game client, do not add automation, and do not modify raw telemetry or source
frame images. Mouse clicks in the calibration UI affect only the local browser
page.

On Windows, Stop Selected Process and Stop All Started Processes stop the
launcher-started process tree by PID with `taskkill /T /F`. The launcher does
not kill unrelated Java, Gradle, RuneLite, or Python processes by image name.
