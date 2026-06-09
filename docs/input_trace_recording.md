# Input Trace Recording

`manual_recorder.py` can record OS-level input beside RuneLite telemetry. The
goal is to align what you did with what the client saw.

## Captured Signals

- Mouse movement, button down/up, clicks, double-clicks when available, drags,
  and wheel events.
- Keyboard down/up events for camera/navigation keys, modifiers, Escape, Space,
  Enter, number keys, and function keys.
- Foreground window title, pid, window rect, client coordinates, and a coarse
  region label when Windows APIs are available.
- Nearest telemetry tick/export sequence at input write time.

Typed text is not reconstructed by default. Keyboard capture stores key names
and virtual-key codes only.

## Coordinate Systems

- `screen_x/screen_y`: desktop coordinates from Windows.
- `client_x/client_y`: foreground-window client coordinates when mappable.
- `canvas_x/canvas_y`: telemetry-provided canvas coordinates when available.
- Target-relative click offsets are computed during analysis by comparing the
  click point to target aim/clickbox geometry.

## Recording

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-Tree_cutting_input_trace --latest-session --interactive --summary --capture-input --capture-mouse --capture-keyboard --capture-window-context --join-input-telemetry --camera-behavior
```

For VM/manual route recordings, prefer the polling backend:

```powershell
python telemetry-viewer\manual_recorder.py --label route_with_polling_input --latest-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --join-input-telemetry --camera-behavior
```

The Windows hook backend remains available for explicit testing, but polling is
the default reliable path because it asks Windows for current cursor/button/key
state every sample. Hook capture can start successfully and still see no events
if it is not receiving desktop input callbacks.

## Smoke Test

Before a full recording, run:

```powershell
python telemetry-viewer\input_trace_recorder.py --smoke-test --backend polling --duration 8 --out "%TEMP%\osrs_input_smoke_test" --capture-mouse --capture-keyboard --capture-window-context --json
```

While it runs, move the mouse and click. Success requires at least one
`mouse_move` and one mouse down/click. Keyboard success is reported separately.

If the summary says `hook_backend_no_events`, `polling_backend_no_events`, or
`backend_started_but_no_events`, the recorder process ran but did not observe
real input. Use polling, verify the process is in the same Windows desktop
session, and confirm the UI command preview includes the expected input flags.

Outputs:

- `input_events.jsonl`
- `joined_input_telemetry.jsonl`
- `input_trace_summary.json`
- `input_action_classifications.jsonl`
- `input_action_summary.json`
- `menu_interactions.jsonl`
- `menu_interaction_summary.json`
- `target_match_quality.jsonl`
- `target_match_summary.json`
- `camera_behavior_summary.json`
- `vm_mouse_arduino_mapping.json` when mapping is requested

## Analysis

```powershell
python telemetry-viewer\analyze_manual_recording.py recordings\<folder> --summary --schema-gap --join-input --camera-behavior --human-input-summary --vm-mouse-mapping
```

The analyzer joins input events to nearest telemetry snapshots before and after
each input event. For clicks it reports nearest target, click offset from target
aim point, hover/menu evidence before the click, and result hints such as later
animation or inventory changes.

The joiner now classifies click-like events before target matching. Raw OS
clicks remain visible, but camera drag releases, UI/control clicks, minimap
clicks, and right-click setup are excluded from target-relative object matching.
See `docs/input_action_classification.md` for labels and summary fields.

When `--arduino-live-mirror` is enabled, the input recorder also feeds each
captured event into `arduino_live_mirror.py`. The mirror writes non-probe
Arduino commands with `sourceInputEventSeq`, and the analyzer ignores that
source event when looking for follow-up cursor/button correlation.

For retained target-relative clicks, the analyzer also assigns target match
quality tiers. `strong` and `medium` matches are suitable for action/path
analysis; `weak` and `unmatched` rows should be reviewed before using their
target-relative offsets as clickbox training data.

Right-click/menu workflows also produce normalized menu interaction files. These
separate the menu row click point from the underlying game target/action, which
keeps route reports readable when a human chooses an option from the context
menu.

## Camera Behavior

Camera segments are inferred from camera yaw/pitch changes. When OS input trace
exists, the analyzer labels likely source as middle mouse drag, arrow keys,
mouse wheel, or unknown. It also reports camera-before-click timing and examples
of useful segments.
## Coordinate And Device Metadata

Polling input now records richer window metadata when available: foreground window rect, client rect, client origin, DPI, and `ScreenToClient` coordinates. Raw Input device attribution can be requested with `--raw-input-device-attribution`; when unavailable it is reported honestly and polling capture continues.
