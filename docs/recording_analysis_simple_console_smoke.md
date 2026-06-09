# Simple Console Smoke Recording

Recording folder inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_181952_manual_recording_20260606_181945
```

UI manifest:

```text
C:\Users\badto\.osrs-telemetry\ui_control\recording_session\ui_recording_session_manifest.json
```

## Result

- Detected activity type: `Route / Traversal`
- Final verdict: `PASS`
- Summary status: `PASS`
- Report path: `C:\Users\badto\osrs-telemetry\recordings\20260606_181952_manual_recording_20260606_181945\schema_gap_report.md`
- Output folder: `C:\Users\badto\osrs-telemetry\recordings`
- Auto Analyze ran: yes
- Open Output Folder: expected to work; the configured output folder exists.
- Useful for data collection: yes.

## Evidence Quality

- Duration: `29.859` seconds
- Snapshots: `11`
- Tick range: `53 -> 96`
- Telemetry freshness during recording: good; observed live sources had stale observations `0` and parse failures `0`.
- Input capture: `PASS`, polling backend, `156` events, `154` real input events.
- Traversal lifecycle: `PASS`, route `Bank_to_Woodcutting_area`, phase `arrived`.
- Route segments: `6` total, `6` successful, `0` partial.
- Plane changes: `1`
- Input path: `os_polling_only`; live mirror was not requested.
- Duplicate click risk: no Arduino live click commands, duplicate likely count `0`.
- Route template comparison after a full rerun: `PASS_BASE_TEMPLATE`, score `1.0`, required segments `5/5`, missing `0`, extra `0`, failed postconditions `0`.
- Route monitor live history: template revision `3`, final state `arrived`, completed `5/5`, remaining `0`, off-route `false`.

Biggest warning shown by the UI manifest:

```text
arduino_events.jsonl is missing or empty
```

This warning is acceptable for Simple Mode because Arduino is optional and was not required for the recording.

## Freeze Diagnosis

The likely visible freeze happened at analysis completion / UI result rendering, not during route execution.

Evidence:

- The recorder and analyzer produced all expected artifacts.
- The manifest contains the analyzer command and final verdict, so Auto Analyze completed.
- The analyzer command was launched by the UI process manager, but the completion callback parsed result artifacts and updated Tk variables directly from the reader thread.
- The UI log drain loop emptied the entire queue in one Tk callback. If the analyzer or helper emitted many lines, the event loop could be occupied long enough to make the window look frozen.
- The Simple Mode auto-analysis command in the manifest did not include route template comparison / route monitor / route history flags, even though the route monitor ran with a valid template. A full rerun with those flags passed, so this was a UI command-building gap rather than a data problem.

Root cause:

```text
telemetry_ui.py allowed analyzer completion/result parsing and unbounded log draining to monopolize or cross the Tk UI thread boundary.
```

Secondary command-building issue:

```text
Analyze Latest built from raw UI variables instead of the selected Simple Mode recording profile, so route analysis defaults could be lost between recording and analysis.
```

Approximate freeze duration:

```text
reported by user as about one minute; no saved UI log with per-frame timestamps was available to measure it directly.
```

## Fix Applied

- Analyzer completion now schedules result parsing back onto the Tk main thread with `after()`.
- Analyzer heartbeat updates the visible status as `Analyzing... Ns`.
- Analyzer timeout handling stops a long-running analyzer and records a WARN instead of hanging.
- Log draining is capped per UI tick and resumes shortly after if more output remains.
- Result parsing is hardened for missing or partially written `summary.json` / report files.
- Main buttons are disabled/enabled while recording or analysis is active without adding new visible controls.
- Analyze Latest now normalizes the current settings through the selected recording profile before building the analyzer command, so Simple Mode includes route template comparison, route monitor, and route history whenever a valid route template is available.

## Trust Decision

The smoke recording is useful for data collection. It proves the simplified workflow can capture a real route/traversal run, complete live route monitoring, and pass the offline route template comparison afterward. The issues found were UI responsiveness and a missing Simple Mode analysis-default handoff, not recording quality.
