# Live Mirror Click Storm Recording Analysis

## Recording/Test Folder

`C:\Users\badto\.osrs-telemetry\ui_control\arduino_live_mirror_test\20260603_070208_ui_live_mirror_test`

Analyzer command rerun:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\.osrs-telemetry\ui_control\arduino_live_mirror_test\20260603_070208_ui_live_mirror_test" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification
```

## Counts

| Metric | Value |
| --- | ---: |
| Duration | `8.032s` |
| Input events | `252` |
| Real input events | `250` |
| OS clicks | `63` |
| Mouse down/up | `63` / `63` |
| Mouse moves | `44` |
| Arduino commands | `70` |
| Probe commands | `1` |
| Non-probe mirror commands | `69` |
| MOVE commands | `7` total, `6` non-probe |
| CLICK commands | `63` |
| MOUSE_DOWN / MOUSE_UP | `0` / `0` |
| Ack count | `70` |
| Max Arduino commands/sec | `16` |
| Max click commands/sec | `14` |

## Diagnosis

The test generated one Arduino CLICK for each OS click event. The dangerous part is that the Arduino-generated click was then observed by the polling input recorder as another OS click. The mirror saw that as fresh input and sent another Arduino CLICK.

This was a feedback loop, not a legitimate sequence of human actions.

The old analyzer marked the run as mirror-verified because the repeated Arduino clicks correlated with repeated observed OS clicks. The updated analyzer now treats command-rate safety as part of verification and reports:

- `inputPathClassification`: `live_mirror_click_storm`
- `mirrorVerificationStatus`: `live_mirror_click_storm`
- `mirrorQuality`: `unsafe_click_storm`
- `liveMirrorVerified`: `false`
- safety: `live_mirror_click_storm`, `live_mirror_feedback_suspected`, `live_mirror_rate_limited`

## Would The New Throttles Have Prevented It?

Yes. The new live mirror path suppresses button feedback, emits only one CLICK per clean gesture, applies a same-button cooldown, caps click commands per second, and panic-stops if the command stream exceeds the configured threshold.

The default controlled validation settings are:

- max clicks/sec: `4`
- max button commands/sec: `8`
- max move commands/sec: `120`
- max total commands/sec: `150`
- click cooldown: `120ms`
- same-button cooldown: `80ms`
- max burst commands: `50`
- panic threshold: `100` commands / `1000ms`
- RuneLite foreground required
- telemetry UI clicks ignored

## Final Classification

`live_mirror_click_storm`

This recording should not be used as proof that live mirror is safe or gameplay-ready.

## Next Validation Command

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-menu_row_validation_live_mirror_controlled --latest-session --prefer-active-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --raw-input-device-attribution --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode mirror --arduino-probe --arduino-probe-move 25 0 --arduino-probe-observe-ms 750 --mirror-quiet-probe --arduino-live-mirror --mirror-move-min-px 1 --mirror-max-step-px 25 --mirror-send-interval-ms 5 --mirror-button-mode click --mirror-max-clicks-per-second 4 --mirror-max-button-commands-per-second 8 --mirror-max-move-commands-per-second 120 --mirror-max-total-commands-per-second 150 --mirror-click-cooldown-ms 120 --mirror-same-button-cooldown-ms 80 --mirror-max-burst-commands 50 --mirror-panic-command-threshold 100 --mirror-panic-window-ms 1000 --mirror-arm-delay-ms 500 --mirror-arm-only-when-runelite-focused --mirror-window-title-allow RuneLite --mirror-exclude-window-title "OSRS Telemetry Control" --mirror-ignore-ui-clicks --arduino-mirror-preflight --input-path-integrity --mirror-correlation-window-ms 250 --mirror-max-move-error-px 100 --vm-mouse-mapping --write-arduino-mapping --telemetry-preflight --telemetry-preflight-seconds 5 --max-telemetry-age-ms 3000 --wait-for-fresh-telemetry --wait-for-fresh-telemetry-timeout 30 --menu-capture-burst --menu-burst-ms 2000 --menu-burst-poll-ms 15
```
