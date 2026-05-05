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

Profile-aware crops are grouped by profile name:

```text
perception\crops\tick-XXXXXXXX\base\chatbox.jpg
perception\crops\tick-XXXXXXXX\inventory\inventoryGrid.jpg
perception\crops\tick-XXXXXXXX\prayer\prayerGrid.jpg
```

Crop mode requires Pillow to already be available. The script does not install
dependencies. If Pillow is unavailable, it prints a warning and continues in
metadata-only mode. Generated crops, when possible, are derived outputs under
`perception\crops\`; source frame images and raw telemetry files are not
modified. The visual prep tool is read-only derived analysis data and performs
no automation, clicking, input hooks, overlays, menu actions, or client-state
mutation.

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
7. Use **Save session calibration** for this session only, or **Save as default
   profile** for future sessions.
8. Click **Prepare test crops**.
9. Inspect the crops from the current session perception folder.

The launcher Calibration section includes **Start Calibration Mode**, **Open
Calibration UI**, **Build Perception Dataset**, **Prepare Test Crops**, **Open
Calibration Profile Folder**, and **Open Current Session Perception Folder**.

The UI lets you refresh to the newest frame, use the latest existing frame,
drag boxes on the captured frame, edit pixel `x/y/w/h` values, switch region
type between `rect`, `circle`, `ellipse`, and `grid`, select **Base regions**
or a tab profile, show base and active-tab regions with distinct styling, add
custom tab profiles, add new region categories, rename or duplicate regions,
delete regions, and edit tags. Adding a region while the Inventory profile is
selected adds it under `tabProfiles.inventory`.

Persistence behavior:

- **Save session calibration** writes only the selected session's
  `sessions\<session_id>\perception\screen_regions.json`.
- **Save as default profile** writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json`.
- Future sessions initialize from the default profile when
  `build_perception_dataset.py` creates their first `screen_regions.json`.
- Existing sessions keep their own session-local calibration unless explicitly
  overwritten.
- **Load default profile** loads that profile into the UI without overwriting
  the session file.

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
