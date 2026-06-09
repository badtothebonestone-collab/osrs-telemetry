# Coordinate Alignment And Mirror Validation: Menu Row Validation V2

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260603_031002_manual_action-menu_row_validation_V2`

Analyzer command run:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_031002_manual_action-menu_row_validation_V2" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --print-menu-interactions --print-target-match-quality --print-coordinate-alignment --print-input-path-integrity
```

## Verdict

PASS for coordinate alignment and menu-row proof. WARN for Arduino mirror proof.

The V2 recording now proves the selected RuneLite menu row with normalized coordinates. The raw OS client click was outside the menu bounds, but the coordinate-alignment layer found an inverse-DPI-style transform that placed the click inside the correct menu row. The linked game target remains a strong match.

Arduino mirror mode was requested, and the bridge connected on COM6, but the recording did not include per-action Arduino movement/click commands or acknowledgements. The input path is therefore classified as `arduino_mirror_failed`, meaning mirror was requested but not proven.

## Recording Summary

| Field | Value |
|---|---|
| Duration | 25.328s from input trace |
| Tick range | 236 -> 274 |
| Snapshots | 29 |
| Input backend requested/used | polling / polling |
| Input events | 88 total, 86 real |
| OS clicks | 2 |
| Mouse moves | 76 |
| Keyboard events | 4 |
| Parse failures | none |

## Raw Click Vs Menu Bounds

The selected click evidence was event sequence `37`.

| Item | Value |
|---|---|
| Raw client click | `x=570, y=349` |
| Raw screen click | `x=1286, y=403` |
| Menu bounds | `x=369, y=206, width=103, height=52` |
| Raw hit test | outside menu row bounds |
| Raw row selected | none |

The raw point could not prove a row hit because it was outside the exported menu bounds.

## Coordinate Alignment

| Item | Value |
|---|---|
| Alignment status | PASS |
| Direct DPI metadata in this recording | not captured |
| Detected DPI scale | null |
| Chosen transform | `client_inverse_dpi_1_4` |
| Normalized menu point | `x=407.143, y=249.286` |
| Normalized row hit count | 1 |
| Raw row hit count | 0 |

Candidate transforms included raw client coordinates, inverse common DPI scales, and a target-row anchor fallback. A pure 1.5 inverse scale placed the click inside the menu, but on the `Climb-up Staircase` row. The selected transform was chosen because it both hit a row and matched the telemetry-observed action/target: `Climb Staircase`.

## Menu Row Before And After

| Evidence | Before normalization | After normalization |
|---|---|---|
| Row geometry present | yes | yes |
| Click inside menu bounds | no | yes |
| Click inside row bounds | no | yes |
| Selected row index | none | 4 |
| Selected option | none | Climb |
| Selected target | none | Staircase |
| Row bounds | unavailable for raw hit | `x=369, y=240.667, width=103, height=8.667` |
| Row center | unavailable for raw hit | `x=420.5, y=245.0` |
| Distance to row center | unavailable for raw hit | 14.028 px |

## Linked Game Target

| Field | Value |
|---|---|
| Target kind | object |
| Target name/action | Staircase / Climb |
| Raw/effective object id | 16672 / 16672 |
| Stable ref | `1:3204:3207:52:47:GAME_OBJECT:16672:17482012596:1536` |
| World point | `3204,3207,1` |

The menu row links cleanly to the game object. The selected row provides the action and target, and object clickbox proximity is not required for a menu-row selection.

## Target Quality

| Metric | Value |
|---|---|
| Target-relative clicks | 1 |
| Strong matches | 1 |
| Medium matches | 0 |
| Weak matches | 0 |
| Unmatched | 0 |
| Event 37 quality | strong |
| Event 37 score | 1.0 |

Strongest evidence:

- Menu row geometry confirmed using `client_inverse_dpi_1_4`.
- Menu row option and target were present: `Climb Staircase`.
- Underlying object identity was present: object id `16672`.
- Target action was confirmed by menu/action telemetry.
- Hover/menu evidence confirmed the target.
- Post-click expected outcome was confirmed.

## Input Path Integrity

| Field | Value |
|---|---|
| Requested Arduino mode | mirror |
| Actual detected mode | mirror |
| Input path classification | `arduino_mirror_failed` |
| Mirror verification status | failed |
| Arduino port | COM6 |
| Arduino connected | true |
| Mirror active | false |
| Mirror verified | false |
| Arduino command count | 0 |
| Arduino movement command count | 0 |
| Arduino click command count | 0 |
| Arduino ack count | 0 |
| Observed cursor movement count | 76 |
| Observed click count | 6 |
| Command-to-movement correlations | 0 |
| Command-to-click correlations | 0 |
| Possible double input | false |
| Raw Input attribution | unavailable |

Warnings:

- Mirror requested but not proven. Recording contains Arduino status/connection evidence but no per-action mirror command stream.
- Observed OS polling input exists without matching Arduino mirror commands.

This means the menu-row result is valid for OS polling input, but the recording does not prove that the physical action path traveled through Arduino mirror mode.

## Retroactive Mirror Proof Limit

V2 has no `arduino_action_commands.jsonl` file and no action-path Arduino MOVE/CLICK command records. Its `arduino_events.jsonl` contains only connect/error/disconnect, and `arduino_status.json` reports COM6 was locked by another `osrs-telemetry` process during startup.

Because there are zero movement commands, zero click commands, and zero command acknowledgements, V2 cannot prove Arduino mirror mode retroactively. The new probe/action-command path can verify future recordings, but it cannot create missing historical Arduino command evidence.

## How To Read Future Input Path Classifications

| Classification | Meaning |
|---|---|
| `os_polling_only` | Windows polling captured real input and no Arduino evidence was involved. |
| `arduino_status_only` | Arduino health/status was captured, but no action commands were seen. |
| `arduino_bridge_connected` | Bridge connected, but action-path evidence is incomplete. |
| `arduino_probe_verified` | A deliberate Arduino probe command produced observed cursor/button evidence. |
| `arduino_mirror_requested` | Mirror mode was requested but proof is not available yet. |
| `arduino_mirror_active` | Mirror action commands were seen, but not correlated to observed input. |
| `arduino_mirror_verified` | Arduino commands correlate with observed cursor/button input. |
| `arduino_mirror_failed` | Mirror was requested, but per-action mirror proof is missing or failed. |
| `conversion_trace_only` | OS input was converted to Arduino-style deltas offline; it was not sent live. |
| `mixed_input_path` | Arduino commands and unrelated OS input both appear. |
| `unknown_input_path` | Not enough evidence to classify. |

## Files Generated Or Updated For This Fixture

- `summary.json`
- `schema_gap_report.md`
- `joined_input_telemetry.jsonl`
- `input_action_classifications.jsonl`
- `input_action_summary.json`
- `target_match_quality.jsonl`
- `target_match_summary.json`
- `menu_interactions.jsonl`
- `menu_interaction_summary.json`
- `coordinate_alignment_summary.json`
- `arduino_mirror_verification.json`
- `input_path_integrity_summary.json`
- `camera_behavior_summary.json`
- `vm_mouse_arduino_mapping.json`

## Remaining Gaps

- This V2 recording does not contain direct DPI/window client metadata because it was captured before the richer recorder metadata was added. Future recordings should include DPI, client rect, client origin, and coordinate capture method.
- Raw Input device attribution is only represented as an optional/unavailable evidence channel for now. Polling remains the reliable input-count backend.
- Arduino mirror verification cannot pass until the bridge/firmware/session emits per-action movement/click commands or acknowledgements that can be correlated with observed cursor/button events.
- The right-click menu open itself was not classified as a separate open event in this fixture, but the left-click selection and menu snapshot were enough to resolve the row and linked target.

## Next Recommended Recording

Record one more menu-row validation with mirror mode enabled and mirror preflight on. The goal is to produce `arduino_mirror_verified` with at least one Arduino movement/click command correlated to the observed Windows input and RuneLite menu selection.

Recommended UI steps:

1. Start the telemetry stack.
2. Set Arduino passthrough mode to `mirror`.
3. Enable Arduino auto-start/check, Mirror Preflight, Input Path Integrity, polling input, window context, and Raw Input attribution.
4. Leave `Require Arduino Mirror Verified` off for diagnosis, or turn it on for the strict pass after mirror proof works.
5. Start recording, right-click the target, select the intended menu row, stop recording, then run Analyze Latest Recording.
6. Open `coordinate_alignment_summary.json`, `input_path_integrity_summary.json`, and `arduino_mirror_verification.json`.

Recommended CLI command:

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-menu_row_validation_mirror_verified --latest-session --prefer-active-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --raw-input-device-attribution --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode mirror --arduino-mirror-preflight --input-path-integrity --mirror-correlation-window-ms 250 --mirror-max-move-error-px 5 --vm-mouse-mapping --write-arduino-mapping --telemetry-preflight --telemetry-preflight-seconds 5 --max-telemetry-age-ms 3000 --wait-for-fresh-telemetry --wait-for-fresh-telemetry-timeout 30 --menu-capture-burst --menu-burst-ms 2000 --menu-burst-poll-ms 15
```

Strict version:

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-menu_row_validation_mirror_required --latest-session --prefer-active-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --raw-input-device-attribution --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode mirror --arduino-mirror-preflight --require-arduino-mirror-verified --input-path-integrity --mirror-correlation-window-ms 250 --mirror-max-move-error-px 5 --vm-mouse-mapping --write-arduino-mapping --telemetry-preflight --telemetry-preflight-seconds 5 --max-telemetry-age-ms 3000 --wait-for-fresh-telemetry --wait-for-fresh-telemetry-timeout 30 --menu-capture-burst --menu-burst-ms 2000 --menu-burst-poll-ms 15
```
