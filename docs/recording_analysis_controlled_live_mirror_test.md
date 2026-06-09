# Controlled Live Mirror Test Analysis

## Folder Inspected

`C:\Users\badto\.osrs-telemetry\ui_control\arduino_live_mirror_test\20260603_074911_ui_live_mirror_test`

Files inspected:

- `arduino_action_commands.jsonl`
- `arduino_live_mirror_status.json`
- `arduino_live_mirror_summary.json`
- `arduino_mirror_verification.json`
- `input_path_integrity_summary.json`
- `input_events.jsonl`
- `summary.json`

## Verdict

**PASS: the click-storm fix worked.**

The controlled live mirror test is safe enough to proceed to a real controlled menu-row validation recording, with the same safety settings and Panic Stop available.

## Summary

| Field | Result |
| --- | --- |
| Live mirror state | `stopped` after clean stop; active during armed window |
| Total Arduino commands | `2` |
| Probe commands | `1` |
| Non-probe commands | `1` |
| MOVE commands | `1` total, `0` non-probe |
| CLICK commands | `1` total, `1` non-probe |
| MOUSE_DOWN commands | `0` |
| MOUSE_UP commands | `0` |
| Max commands/sec | `1` |
| Max click commands/sec | `1` |
| Dropped command/input attempts | `110` |
| Throttled commands | `0` |
| Duplicate inputs ignored | `0` |
| Panic stops | `0` |
| UI/window-filtered events | `7` telemetry UI events |
| Foreground-filtered events | `0` |
| Pre-arm/disarmed drops | `103` |
| liveMirrorActive | `true` |
| liveMirrorVerified | `true` |
| possibleDoubleInput | `false` |
| Final classification | `arduino_mirror_verified` |
| Safety classifications | none |

## Click Sequence

The only live mirror action command was:

- Source input event: `113`
- Source kind: `mouse_up`
- Source window: `RuneLite - KCLBolus`
- Source region: `viewport`
- Arduino command: `CLICK left`
- Ack: `OK CLICK`
- Ack latency: `94 ms`

The input trace then observed the Arduino-produced click feedback as events `115`, `116`, and `117`, but the mirror did not send another Arduino CLICK. That means the feedback suppression and click state machine worked.

## Explicit Answers

- Did the click storm happen again? **No.** The previous bad run had `63` CLICK commands and max `14` click commands/sec. This controlled run had `1` CLICK command and max `1` click command/sec.
- Did rate limiting or panic stop activate? **No.** There was nothing unsafe to throttle. `throttledCommandCount` was `0` and `panicStopCount` was `0`.
- Did one OS click produce only one Arduino click command? **Yes.** Source event `113` produced one Arduino CLICK. The feedback click was observed but not mirrored again.
- Did UI/control clicks get ignored? **Yes.** `7` telemetry UI-window events were dropped, and `103` pre-arm events were dropped while the mirror was disarmed.
- Is it safe to do the controlled menu-row validation recording? **Yes, with the same controlled flags and Panic Stop ready.**

## Notes

The probe remains `arduino_probe_verified_noisy`, so it should not be used for movement calibration yet. That does not block this click-storm validation result: the live mirror click path behaved safely and did not loop.

## Next Recording Command

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-menu_row_validation_live_mirror_controlled --latest-session --prefer-active-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --raw-input-device-attribution --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode mirror --arduino-probe --arduino-probe-move 25 0 --arduino-probe-observe-ms 750 --mirror-quiet-probe --arduino-live-mirror --mirror-move-min-px 1 --mirror-max-step-px 25 --mirror-send-interval-ms 5 --mirror-button-mode click --mirror-max-clicks-per-second 4 --mirror-max-button-commands-per-second 8 --mirror-max-move-commands-per-second 120 --mirror-max-total-commands-per-second 150 --mirror-click-cooldown-ms 120 --mirror-same-button-cooldown-ms 80 --mirror-max-burst-commands 50 --mirror-panic-command-threshold 100 --mirror-panic-window-ms 1000 --mirror-arm-delay-ms 500 --mirror-arm-only-when-runelite-focused --mirror-window-title-allow RuneLite --mirror-exclude-window-title "OSRS Telemetry Control" --mirror-ignore-ui-clicks --arduino-mirror-preflight --input-path-integrity --mirror-correlation-window-ms 250 --mirror-max-move-error-px 100 --vm-mouse-mapping --write-arduino-mapping --telemetry-preflight --telemetry-preflight-seconds 5 --max-telemetry-age-ms 3000 --wait-for-fresh-telemetry --wait-for-fresh-telemetry-timeout 30 --menu-capture-burst --menu-burst-ms 2000 --menu-burst-poll-ms 15
```
