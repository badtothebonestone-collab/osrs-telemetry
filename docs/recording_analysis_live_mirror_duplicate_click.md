# Live Mirror Duplicate Click Analysis

## Recording Inspected

`C:\Users\badto\osrs-telemetry\recordings\20260605_194851_validation_menu_row`

This was the newest recording after the live mirror safety hardening. It had good polling input and menu/target telemetry, but it still emitted one live Arduino click for a normal OS click.

## Verdict

`WARN`: the recording proves the Arduino command path can send a click, but it is not clean proof of a safe mirrored menu action. One duplicate OS-plus-Arduino click is likely.

## Counts

| Field | Value |
|---|---:|
| OS click events | 5 |
| Arduino live CLICK commands | 1 |
| Arduino probe clicks | 0 |
| Click echoes suppressed | 0 |
| Duplicate click candidates | 1 |
| Duplicate clicks likely | 1 |
| Map-only clicks | 0 |
| Arduino physical clicks | 0 |

## Timeline Evidence

- OS mouse down: event `3`, elapsed `2.610s`, left button, RuneLite viewport.
- OS mouse up: event `4`, elapsed `2.906s`, left button, RuneLite viewport.
- Arduino live `CLICK`: command `cmd_f2158eccf994`, source input event `4`, elapsed `2.906s`, ack `OK CLICK`, ack latency `94ms`.
- OS click event: event `5`, elapsed `2.922s`, left button, RuneLite viewport.
- Analyzer ownership delta: Arduino click command time was `16ms` before the synthesized OS click event.
- A later nearby click-like event `8` was classified as `arduino_click_echo`, not a second duplicate command.

## Root Cause

The previous `validation_menu_row` mirror profile disabled movement and added echo/queue/pause protections, but it still allowed live Arduino click output. With polling input, the original manual OS click was already delivered to RuneLite. The live mirror then sent an Arduino `CLICK` for the same gesture, so RuneLite could receive a second click.

This is not a click storm and not a delayed movement queue issue. It is the expected duplicate risk of `live_unsuppressed` click mirroring without source suppression.

## Code Paths Fixed

- `telemetry-viewer\arduino_live_mirror.py`
  - Added click ownership policy.
  - Defaulted `validation_menu_row` to `map_only` unless unsuppressed live clicks are explicitly allowed.
  - Added `live_requires_source_suppression` and `arduino_source_only` safeguards.
  - Added map-only virtual click records and duplicate-risk summary fields.

- `telemetry-viewer\manual_recorder.py`
  - Added CLI flags for click policy and source-suppression requirements.

- `telemetry-viewer\telemetry_ui.py`
  - The Live Mirror Menu Row Validation preset now passes `--mirror-click-policy map_only`.
  - The Arduino/Mirror tab exposes click policy and duplicate-risk controls.

- `telemetry-viewer\input_trace_joiner.py`
  - Added `click_ownership_summary.json`.
  - Added duplicate OS-plus-Arduino click diagnostics to `input_path_integrity_summary.json`.

- `telemetry-viewer\analyze_manual_recording.py`
  - Added a Click Ownership section to `schema_gap_report.md`.

## Validity

The recording is valid evidence for telemetry/menu/target analysis and for proving the Arduino click command path. It is invalid as clean proof of no-duplicate mirrored gameplay clicks, because the live click policy was effectively `live_unsuppressed`.

## Expected Next Recording

Use the no-duplicate command or the UI Live Mirror Menu Row Validation preset. PASS criteria:

- `clickPolicyUsed: map_only`
- `totalArduinoLiveClickCommands: 0`
- `mapOnlyClickCount` greater than `0`
- `duplicateClickLikelyCount: 0`
- `liveClickWithoutSuppressionCount: 0`
- menu selection and target quality still PASS
