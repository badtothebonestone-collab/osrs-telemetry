# Live Mirror Post-Action Movement Analysis

## Recording Inspected

`C:\Users\badto\osrs-telemetry\recordings\20260605_190806_manual_action-menu_row_validation_live_mirror_controlled_arm`

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260605_190806_manual_action-menu_row_validation_live_mirror_controlled_arm" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --print-input-path-integrity --print-coordinate-alignment --print-menu-interactions --print-target-match-quality
```

## Verdict

`WARN`: the recording is valid for telemetry/menu-row geometry, but invalid as clean proof of a controlled mirrored menu click.

The selected menu row was captured and scored strongly, but the live mirror armed after the selected action and then kept sending movement and right-click commands afterward.

## Timeline

| Event | Elapsed |
| --- | ---: |
| Recording start | `0.000s` |
| Selected menu row / game action | `2.797s` |
| Mirror armed | `3.313s` |
| First post-action Arduino MOVE | `3.500s` |
| First post-action Arduino right CLICK | `5.531s` |
| Last live mirror command | `19.969s` |
| Recording stop | `23.906s` |

## Action Evidence

| Field | Result |
| --- | --- |
| Selected menu row | row `4` |
| Selected option / target | `Climb-down Staircase` |
| Row geometry | present |
| Coordinate transform | `client_inverse_dpi_1_5` |
| Target quality | `strong`, score `1.0` |
| Linked object | `Staircase`, id `56231` |

## Mirror Evidence

| Field | Result |
| --- | --- |
| Arm mode | `recording_persistent` |
| Mirror armed at action | `false` |
| Non-probe Arduino commands | `64` |
| Movement commands | `58` non-probe, `60` total |
| Click commands | `6` |
| Correlated movement / click commands | `44` / `6` |
| Max commands/sec | `6` |
| Max click commands/sec | `1` |
| Panic stops | `0` |
| Echo suppression | not enabled in this recording |
| Auto-pause | not enabled in this recording |

## Post-Action Commands

The analyzer now reports:

- `postActionArduinoCommandCount: 64`
- `postActionMovementCommandCount: 58`
- `postActionClickCommandCount: 6`
- `postActionWeirdMovementSuspected: true`
- `feedbackLoopSuspected: true`

Representative post-action commands:

| Elapsed | Command | Source input event | Source kind | Delta |
| ---: | --- | ---: | --- | --- |
| `3.500s` | `MOVE` | `5` | `mouse_move` | `-3,1` |
| `4.016s` | `MOVE` | `8` | `mouse_move` | `-8,-18` |
| `4.547s` | `MOVE` | `10` | `mouse_move` | repeated chunks |
| `5.531s` | `CLICK right` | `26` | `mouse_up` | n/a |
| `9.391s` | `CLICK right` | `44` | `mouse_up` | n/a |

## Suspected Root Cause

Two issues combined:

1. The full recording still used the UI live-mirror test arm delay, so the mirror armed at `3.313s`, after the real menu-selection action at `2.797s`.
2. The recording used unrestricted movement mirroring. After arming, OS polling saw cursor movement and right-click events, some likely caused by Arduino output, and the mirror converted those into more Arduino commands.

This is consistent with a live mirror feedback/echo path or post-action movement stream. The old mirror had only coarse feedback suppression and no validation auto-pause, so it could keep acting after the validation action was complete.

Code paths fixed:

- `telemetry-viewer\telemetry_ui.py`: recording commands now use recording arm delay, not live-test arm delay; Live Mirror Menu Row Validation now uses `validation_menu_row`.
- `telemetry-viewer\arduino_live_mirror.py`: added profiles, command-aware echo suppression, stale movement drops, queue clearing, and validation auto-pause.
- `telemetry-viewer\manual_recorder.py`: added new mirror flags and feeds telemetry snapshots to the mirror for plane-change auto-pause.
- `telemetry-viewer\input_trace_joiner.py`: added post-action command diagnostics and feedback suspicion.

## Fixed Next Recording Should Show

- `mirrorProfile: validation_menu_row`
- `movementMirroringEnabled: false`
- `echoSuppressionEnabled: true`
- `mirrorArmedAtAction: true`
- `postActionArduinoCommandCount: 0` or only expected cleanup-free commands before auto-pause
- `postActionMovementCommandCount: 0`
- `feedbackLoopSuspected: false`
- `mirrorAutoPaused: true`
- `autoPauseReason: menu_selection` or `plane_change`
- no click storm and no panic stop

