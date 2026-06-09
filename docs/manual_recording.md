# Manual Telemetry Recording

Manual recordings are explicit debug/audit artifacts for discovering which live
RuneLite and Python-side fields are useful during a human-performed action. They
do not replace the compact live context shape used by the sidecar, daemon, or
MCP tools.

## Recorder Purpose

`telemetry-viewer\manual_recorder.py` watches the existing live telemetry files
for a session, records bounded snapshots when those files change, extracts
high-value fields when present, and writes an analyzer-ready folder:

```text
recordings\YYYYMMDD_HHMMSS_LABEL\
  manifest.json
  events.jsonl
  summary.json
  schema_gap_report.md
```

The default discovery logic uses the newest live session when
`--latest-session` is supplied, then watches
`interaction_geometry\live\live_baseline_state.json`,
`live_status.json`, `live_context_index.json`, `live_candidates.jsonl`,
`live_activity_state.json`, `live_navigation_summary.json`,
`live_watch_values.json`, `overlay_debug_state.json`,
`last_action_trace.json`, and `input_integrity_status.json`. Use `--sources`
for an explicit comma-separated override. Entries may be either `path` or
`name=path`.

## Record An Action

```powershell
python telemetry-viewer\manual_recorder.py --label chop_tree --description "walk to the tree cluster, hover a tree, then click chop" --duration 12 --latest-session --summary
```

With an explicit session:

```powershell
python telemetry-viewer\manual_recorder.py --label open_bank --duration 20 --session "%USERPROFILE%\.osrs-telemetry\sessions\<session_id>" --summary
```

With explicit sources:

```powershell
python telemetry-viewer\manual_recorder.py --label hover_tree --duration 8 --sources "status=%USERPROFILE%\.osrs-telemetry\sessions\<session_id>\interaction_geometry\live\live_status.json,candidates=%USERPROFILE%\.osrs-telemetry\sessions\<session_id>\interaction_geometry\live\live_candidates.jsonl"
```

To record until a UI or another helper requests stop:

```powershell
python telemetry-viewer\manual_recorder.py --label bank_sequence --description "demonstrate the intended bank open/deposit/close flow" --latest-session --until-stopped --stop-file "%USERPROFILE%\.osrs-telemetry\ui_control\bank_sequence.stop" --marker-file "%USERPROFILE%\.osrs-telemetry\ui_control\bank_sequence.markers"
```

Blank, missing, or `0` duration is treated as until-stopped mode.
Descriptions are optional, but useful when the recording is meant to teach the
system intent that is not obvious from telemetry alone.

## Desktop UI Workflow

The desktop UI opens the simple Human Recording Console:

```powershell
python telemetry-viewer\telemetry_ui.py
```

Normal use is:

1. Start Game.
2. Start Telemetry.
3. Start Recording.
4. Do the task normally.
5. Stop Recording.
6. Read the automatic analysis summary.
7. Open Output Folder if you need the files.

The UI always uses the internal `record_everything_default` profile for normal
recording. It records enough evidence for the analyzer to decide afterward
whether the session was route/traversal, woodcutting, banking, menu interaction,
input/camera sample, or generic telemetry. You do not need to pick that before
recording.

When combat happens during a task, Record Everything preserves `combat_state`
when the bridge provides it. The analyzer can then create
`interruption_lifecycle.json` and `combat_damage_summary.json` with damage
taken/dealt, primary opponent, HP change, actor death, and task-resume evidence.

The UI writes stop and marker control files under
`%USERPROFILE%\.osrs-telemetry\ui_control\` and keeps recordings under the
selected output folder, which defaults to the repo-local `recordings` directory.
Each UI-started recording also writes
`ui_recording_session_manifest.json` under the UI control folder. That file is
for debugging the UI workflow and links the label, output folder, recorder
command, analyzer command, final verdict, and report path.

`Diagnostics / Settings` still exposes route templates, route monitor controls,
Arduino probe/mapping, live mirror settings, command previews, and logs for
troubleshooting. The simple workflow does not require Arduino, does not require
a route template, and does not send live Arduino clicks.

Human click planning is available as a dry-run diagnostic after profiles have
been generated:

```powershell
python telemetry-viewer\execute_next_action.py --dry-run-click-plan --json
```

This produces `human_click_plan.v1` with a center point, profile-informed point,
offset, readiness blockers, and warnings. It is advisory only; it does not send
input and does not replace live readiness or hover/menu proof.

## Markers

Interactive mode reads marker lines from the terminal:

```powershell
python telemetry-viewer\manual_recorder.py --label bank_sequence --duration 60 --latest-session --interactive
```

Press Enter to add an unlabeled marker, type text then Enter to label a marker,
or type `q` then Enter to stop early. Markers are written as JSONL events and
the analyzer links each marker to nearby source snapshots.

When `--marker-file` is supplied, each new non-empty line appended to that file
is also recorded as a marker event.

## Analyze A Recording

The recorder runs the analyzer at the end. You can rerun it:

```powershell
python telemetry-viewer\analyze_manual_recording.py recordings\20260602_190000_chop_tree --summary --json
python telemetry-viewer\analyze_manual_recording.py recordings\20260602_190000_chop_tree --schema-gap --markdown
python telemetry-viewer\analyze_manual_recording.py recordings\20260602_190000_chop_tree --print-events --max-events 10
```

For tree-cutting recordings, include the woodcutting lifecycle view:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260602_223444_manual_action-Tree_cutting" --summary --schema-gap --woodcutting-lifecycle
```

This adds `woodcutting_lifecycle` to `summary.json` and writes
`woodcutting_lifecycle.json`. The lifecycle phase summarizes whether the run is
at `tree_available`, `chop_clicked`, `chopping`, `log_gained`,
`target_depleted`, or `inventory_full`.

For bank, deposit-box, deposit, or withdraw recordings, include the banking
lifecycle view:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --banking-lifecycle --print-banking-lifecycle
```

This writes `banking_lifecycle.json`. A `WARN` banking result can still be a
useful recording: it means the action was inferred from inventory/menu evidence
but direct bank-open, widget, or container telemetry was missing.

Record Everything now also preserves the bridge `bank_ui` live-cache packet when
the plugin snapshot endpoint exposes it. The recorder stores that packet in
`events.jsonl` on `bank_ui` source entries, alongside freshness, tick, and source
metadata. Missing `bank_ui` does not stop a generic recording; it only means
banking analysis falls back to inferred menu and inventory evidence.

## Reading `schema_gap_report.md`

The report groups fields into:

- `present`: observed in at least one snapshot and usable for compact context.
- `missing`: not observed and not yet categorized.
- `computable_in_sidecar`: can likely be derived from existing telemetry.
- `requires_bridge_export`: likely needs a targeted RuneLite/plugin export.
- `needs_manual_review`: requires more recordings or source inspection.

Convert a gap into work by choosing one `requires_bridge_export` item, adding the
smallest read-only Java export, compiling it, then exposing that field through
the Python schema scanner and context response. Re-record the action and confirm
the field moves from missing to present.

## Compact Context

Manual recordings can include raw snippets with `--include-raw`, but raw
recordings are debug/audit/training artifacts only. The sidecar context should
stay compact: request only sections such as `baseline`, `inventory`, `hover`,
`menu`, `nearby_objects`, or `best:object:tree`, and let the response report
missing capabilities instead of dumping raw source files.

## MCP Fit

The MCP adapter wraps the same context layer with read-only tools:
`get_context`, `get_capabilities`, `list_recordings`, `summarize_recording`, and
`get_schema_gap_report`. MCP should not duplicate recorder/analyzer logic or add
live input behavior.

## Input And Arduino Trace Recordings

For human input trace evidence, add:

```powershell
--capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --join-input-telemetry --camera-behavior
```

For Arduino status/path evidence, add:

```powershell
--arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode bridge --vm-mouse-mapping --write-arduino-mapping
```

These options add `input_events.jsonl`, `joined_input_telemetry.jsonl`,
`input_trace_summary.json`, `input_action_summary.json`,
`input_action_classifications.jsonl`, `target_match_summary.json`,
`target_match_quality.jsonl`, `camera_behavior_summary.json`,
`arduino_events.jsonl`, `arduino_status.json`, and
`vm_mouse_arduino_mapping.json` when the matching sources are available.

For a deliberate Arduino command-path probe, run:

```powershell
python telemetry-viewer\arduino_mirror_verifier.py --probe --port COM6 --move 12 0 --observe-ms 500
```

or add these recording flags:

```powershell
--arduino-probe --arduino-probe-move 12 0 --arduino-probe-observe-ms 500 --input-path-integrity
```

`arduino_probe_verified` proves the deliberate probe command worked.
`arduino_mirror_verified` requires non-probe action commands during the
recording to correlate with observed input. `conversion_trace_only` means OS
input was converted to Arduino-style deltas after the fact.

For menu-row validation with live mirror enabled, use mapping-only clicks:

```powershell
--arduino-live-mirror --mirror-profile validation_menu_row --mirror-disable-movement --mirror-click-policy map_only
```

Polling input can observe a normal OS/manual click, but it cannot consume or
suppress that click. Sending a live Arduino `CLICK` for the same observed OS
click creates duplicate-click risk. `map_only` keeps the Arduino-style mapping
and diagnostics without sending the second click. The analyzer writes
`click_ownership_summary.json` and reports `duplicateClickLikelyCount`,
`liveClickWithoutSuppressionCount`, `mapOnlyClickCount`, and `clickOwners`.

For live mirror validation, add:

```powershell
--arduino-passthrough-mode mirror --arduino-probe --arduino-probe-move 25 0 --arduino-probe-observe-ms 750 --mirror-quiet-probe --arduino-live-mirror --mirror-profile validation_menu_row --mirror-disable-movement --mirror-button-mode click --mirror-echo-suppression --mirror-clear-queue-on-menu-selection --mirror-clear-queue-on-game-action --mirror-clear-queue-on-plane-change --mirror-auto-pause-after-menu-selection --mirror-auto-pause-after-plane-change --mirror-auto-pause-after-target-quality medium --mirror-arm-mode recording_persistent --mirror-persist-until-stop --mirror-keep-armed-while-recording --mirror-max-clicks-per-second 4 --mirror-click-cooldown-ms 120 --mirror-panic-command-threshold 100 --mirror-arm-delay-ms 500 --mirror-arm-only-when-runelite-focused --mirror-window-title-allow RuneLite --mirror-exclude-window-title "OSRS Telemetry Control" --mirror-ignore-ui-clicks --input-path-integrity
```

`arduino_live_mirror_summary.json` reports whether non-probe commands were sent.
`input_path_integrity_summary.json` reports whether they correlated with
observed cursor/button events.

Live mirror starts disarmed and arms only after input capture is running.
`--mirror-arm-mode test_window` is for smoke tests only; it auto-disarms after
`--mirror-test-duration-sec`. Full manual recordings should use
`--mirror-arm-mode recording_persistent` plus `--mirror-persist-until-stop`, so
the mirror stays armed until Stop Recording, the stop file, Panic Stop Mirror,
or cleanup. Use `--mirror-panic-stop-file` for long or interactive runs so
another process can stop mirror output without terminating the whole recorder.

The analyzer writes `mirror_action_timing_summary.json`. If it reports
`menuSelectionsAfterDisarm` or `actionClicksAfterDisarm`, the recording is not
valid proof of mirrored menu/action clicks even if early movement commands were
verified. If it reports `live_mirror_click_storm`,
`live_mirror_feedback_suspected`, `live_mirror_rate_limited`, or
`live_mirror_panic_stopped`, discard the run for gameplay validation and tune
the mirror before recording again.

For menu-row validation, prefer `--mirror-profile validation_menu_row`. It is
click-only by default, suppresses Arduino echo, clears queued input after the
selected menu action, and auto-pauses after the menu selection or plane change.
Use unrestricted `full_live_mirror` only as an advanced experiment.

Input action classification separates raw OS clicks from useful game-action
clicks. Middle-button camera drag releases, UI/control clicks, minimap clicks,
and right-click menu setup remain in the report, but they are excluded from
target-relative object matching.

Target match quality scores the retained target-relative clicks. Use strong and
medium matches for route/action analysis; treat weak and unmatched rows as
review items before using their click offsets for clickbox or aim training.

Right-click/menu selections add `menu_interactions.jsonl` and
`menu_interaction_summary.json`. These files keep menu row geometry separate
from the linked game target/action, so a click on an `Open Door` row is not
mistaken for a direct door clickbox sample.

For one-action menu-row validation, enable the menu burst flags so the recorder
keeps menu snapshots through the first selection:

```powershell
--menu-capture-burst --menu-burst-until-selection --menu-burst-tail-ms 500 --menu-burst-max-ms 4000 --menu-burst-ms 2000 --menu-burst-poll-ms 15
```

The analyzer pairs `right_click -> menu snapshot(s) -> left-click selection`.
When the right click itself is missing because the recording starts with an
already-open menu, the first left click still gets an implicit menu session and
can recover row bounds from matching snapshots in the retention window. Use
`--menu-row-diagnostics --print-menu-row-diagnostics` to see the selected
snapshot and candidate snapshots for each menu selection.

Use `--arduino-required` only when the recording should fail if the Arduino is
not connected.

Input preflight runs a short smoke test before the full recording. Add
`--fail-if-input-preflight-fails` when you want the recorder to stop before
creating the real recording folder if mouse/click evidence is not captured. If
that flag is omitted, the recorder continues and writes the failed preflight
result into `manifest.json` and `summary.json`.

For the next woodcutting-area-to-bank route with polling input and Arduino
evidence:

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-woodcuting_area_to_bank_polling_input --latest-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode bridge --vm-mouse-mapping --write-arduino-mapping
```
## Mirror-Verified Menu Recordings

For menu-row validation with Arduino mirror evidence, record with polling input, window context, Raw Input attribution, input-path integrity, Arduino probe, Arduino mirror preflight, and menu burst capture. If `--require-arduino-probe-verified` is set, the recorder stops before the action recording loop when the deliberate probe cannot prove the command path. If `--require-arduino-mirror-verified` is set, the recorder requires probe/action-path proof rather than a passive status check.

Use the UI preset `Live Mirror Menu Row Validation` for the same flag set
without typing the full command. The preset keeps mirror persistent during the
recording, keeps the 5-second arm window reserved for `Run Live Mirror Test`,
uses `validation_menu_row`, disables movement mirroring, and auto-pauses after
the validation action.

## Traversal Lifecycle Recording

Route recordings are allowed to contain multiple clicks, menu selections, and
plane transitions. Add `--traversal-lifecycle` during analysis to write
`traversal_lifecycle.json` and a `Traversal Lifecycle` section in
`schema_gap_report.md`.

The route/traversal preset records polling input, menu burst snapshots,
coordinate alignment, target quality, camera behavior, and input-path integrity.
Use it for bank-to-woodcutting or woodcutting-to-bank movement captures. Missing
menu row geometry is a warning, not a route failure, when target quality and
movement or plane-change postconditions are strong.

After a route records cleanly, extract a reusable route template:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --extract-route-template --route-template-out route_templates
```

Compare a later route attempt to that template:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-template-comparison
```

For Record Everything or mixed route recordings, prefer automatic template
matching:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --auto-route-template --print-route-template-comparison
```

Auto matching chooses by detected route name first, then by start/end area.
This keeps a `woodcutting_area_to_bank` route from being judged against the
one-way `Bank_to_Woodcutting_area` template. If no matching template exists,
the analyzer reports an untemplated route and suggests extracting one.

Templates are based on `routeSegments`, not raw click rows, so normal extra
menu/input evidence remains available for debugging without becoming required
route progress.

To write persistent route-history artifacts from an analyzed recording, add:

```powershell
--route-history --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json"
```

This writes `route_session_state.json`, `route_session_events.jsonl`,
`route_progress_timeline.jsonl`, and `route_history_summary.json` into the
recording folder. Use live `route_monitor.py --follow` when you want the same
state machine to track a route session over time.

## Banking Recordings

Record Everything preserves `bank_ui` when the live bridge provides it, so a
bank/deposit recording can prove both UI state and item movement after analysis.
The analyzer writes `banking_lifecycle.json` and includes a Banking Lifecycle
section in `schema_gap_report.md` when `--banking-lifecycle` is enabled.

For the strongest banking proof:

1. Start Recording from the Simple Mode UI.
2. Open the bank or deposit box.
3. Deposit or withdraw the item.
4. Close the bank or deposit box.
5. Stop Recording and let analysis finish.

A direct banking PASS should show `bankOpenSeen` or `depositBoxOpenSeen`,
`bankContainerAvailable`, inventory before/after, deposited or withdrawn items,
and `depositConfirmationLevel=bank_container_delta_confirmed` when bank-side
container deltas are available.
## Combat State Preservation

Record Everything preserves the plugin `combat_state` live-cache packet when the plugin exposes it. Missing `combat_state` is not a failure for ordinary recordings, but interrupted task recordings will remain cause-unknown until this data is present.

The recorder stores combat evidence in `events.jsonl` source snapshots and the analyzer turns it into `interruption_lifecycle.json`.

## Woodcutting Loop Analysis

Record Everything analysis can also write `woodcutting_loop_lifecycle.json`.
This file summarizes the task phase across the existing woodcutting, route,
banking, interruption, combat, and click-profile artifacts.

It is useful even when a recording contains only one phase. For example:

- full inventory woodcutting: next `route_to_bank`
- route to bank: next `banking_deposit`
- deposit complete: next `route_to_woodcutting_area`
- route back to trees: next `resume_cutting`
- resumed combat interruption: next `continue_current_phase`
