# Arduino Live Mirror

Arduino live mirror is the action stream that turns captured OS input events into Arduino HID commands during a manual recording.

It is different from two older evidence types:

- `probe_verified`: a deliberate test command reached the Arduino and produced cursor/button evidence.
- `conversion_trace_only`: OS input was converted to Arduino-style deltas after the recording, but no live commands were sent.

## Output Files

Live mirror recordings write:

```text
arduino_action_commands.jsonl
arduino_live_mirror_status.json
arduino_live_mirror_summary.json
input_path_integrity_summary.json
arduino_mirror_verification.json
```

`arduino_action_commands.jsonl` is the source of truth for live action commands. Probe commands include `probeCommand: true`; live mirror commands include `liveMirrorCommand: true` and `probeCommand: false`.

## Classifications

- `arduino_probe_verified_clean`: probe command, ack, and cursor delta matched within tolerance.
- `arduino_probe_verified_noisy`: probe command and ack worked, but cursor movement was noisy or outside tolerance.
- `conversion_trace_only`: offline conversion only; no live non-probe commands.
- `arduino_mirror_active`: non-probe live commands were sent.
- `arduino_mirror_verified`: non-probe live commands correlated with observed cursor/button input.
- `mixed_input_path`: mirror commands and unrelated OS input both appeared.
- `arduino_mirror_failed`: mirror was requested but no live command proof exists.
- `live_mirror_click_storm`: unsafe repeated click output was detected.
- `live_mirror_rate_limited`: live mirror safety caps throttled commands.
- `live_mirror_panic_stopped`: the mirror stopped itself after the panic threshold.
- `live_mirror_feedback_suspected`: Arduino output appears to have fed back into polling input.

## Clean Probe

Use quiet-window probing when possible:

```powershell
python telemetry-viewer\arduino_mirror_verifier.py --probe --port COM6 --move 50 0 --observe-ms 750 --quiet-window
```

The verifier observes the cursor before the command. If the pointer is already moving, the probe is marked noisy instead of clean.

The earlier `12,0 -> -465,178` result is now `arduino_probe_verified_noisy`: the command wrapper and ack worked, but the observed movement was not a clean match.

## Live Mirror Recording

Enable live mirror only for a deliberate validation run. The recorder listens to `input_events.jsonl`, converts movement/click events, sends Arduino commands, and marks command rows with the source input event sequence.

The analyzer ignores the source input event when correlating a live command, then looks for follow-up cursor/button evidence.

The mirror starts disarmed. The recorder arms it only after the recording folder, telemetry/input preflights, Arduino probe, and input capture are ready. This avoids mirroring the UI button click that started the run.

## Mirror Profiles

- `observe_only`: record OS input and compute mappings, but send no live Arduino commands.
- `click_only`: mirror button/click events only.
- `move_only`: mirror movement only.
- `full_live_mirror`: mirror movement and clicks.
- `validation_menu_row`: conservative menu-row validation mode.

Use `validation_menu_row` for menu-row validation. It disables movement mirroring by default, uses mapping-only clicks by default, enables echo suppression, clears queued input after the selected menu action, and auto-pauses after the menu selection or a plane change.

Mapping-only clicks mean the recorder still writes the Arduino-style conversion/mapping evidence, but it does not send a live Arduino `CLICK`. This is the safe default because polling input can observe a normal OS click but cannot consume or suppress it.

Full movement mirroring remains available in Advanced settings, but it is experimental for gameplay validation because Arduino output can be seen again by OS polling and fed back into the mirror.

## Echo Suppression And Auto-Pause

`--mirror-echo-suppression` tracks expected Arduino output after MOVE and CLICK commands. Matching OS polling events inside the echo window are counted as Arduino echo and are not mirrored again.

Useful summary fields:

- `echoSuppressedMoveCount`
- `echoSuppressedClickCount`
- `feedbackLoopSuspected`
- `staleCommandsDropped`
- `queueClearedOnMenuSelectionCount`
- `queueClearedOnGameActionCount`
- `queueClearedOnPlaneChangeCount`
- `mirrorAutoPaused`
- `autoPauseReason`

For a safe menu-row validation recording, expect `mirrorAutoPaused: true` with `autoPauseReason: menu_selection` or `plane_change`, and no post-action movement commands.

## Click Safety

The live mirror uses a button state machine:

- `click` mode sends one Arduino CLICK on a clean click release or a standalone polling click.
- `down_up` mode sends one MOUSE_DOWN on the down edge and one MOUSE_UP on the up edge.
- held buttons do not repeat CLICK.
- drag releases do not emit CLICK unless explicitly configured.
- middle-mouse drags are treated as camera input, not game clicks.
- output click feedback is suppressed for a short window.

Safety caps are on by default:

- `--mirror-max-clicks-per-second 4`
- `--mirror-max-button-commands-per-second 8`
- `--mirror-max-move-commands-per-second 120`
- `--mirror-max-total-commands-per-second 150`
- `--mirror-click-cooldown-ms 120`
- `--mirror-same-button-cooldown-ms 80`
- `--mirror-max-burst-commands 50`
- `--mirror-panic-command-threshold 100`
- `--mirror-panic-window-ms 1000`

If the panic threshold is crossed, the mirror enters `panic_stopped` and stops sending commands.

## Click Ownership And Policy

Normal polling input is observational. It sees a mouse click after Windows has already delivered that click to RuneLite. If the mirror also sends an Arduino `CLICK` for that same observed OS click, RuneLite can receive two clicks.

Click policy controls this:

- `off`: send no Arduino click commands and do not write click mappings.
- `map_only`: record OS clicks and Arduino-style click mappings, but send no live Arduino `CLICK`.
- `live_unsuppressed`: send Arduino clicks even though the OS click already happened. This is duplicate-risk and should only be enabled deliberately.
- `live_requires_source_suppression`: send live clicks only when source suppression or Arduino-owned input is verified; otherwise downgrade to `map_only`.
- `arduino_source_only`: intended for runs where Arduino is the actual input device/source.

Analyzer ownership labels include:

- `os_click_only`
- `arduino_probe_click`
- `arduino_live_click`
- `arduino_click_echo`
- `duplicate_os_plus_arduino_click`
- `conversion_trace_click_only`
- `arduino_physical_click_source`
- `unknown_click_source`

Use `click_ownership_summary.json`, `input_path_integrity_summary.json`, and the Input Path Integrity section of `schema_gap_report.md` to check `clickPolicyUsed`, `duplicateClickLikelyCount`, `liveClickWithoutSuppressionCount`, `mapOnlyClickCount`, and `clickOwners`.

## Window Filtering

Use foreground/window filtering during validation:

```powershell
--mirror-arm-only-when-runelite-focused --mirror-window-title-allow RuneLite --mirror-exclude-window-title "OSRS Telemetry Control" --mirror-ignore-ui-clicks
```

Dropped events are counted by reason, such as `telemetry_ui_window`, `foreground_window_not_allowed`, `ui_control_event`, `click_cooldown`, `rate_limited`, or `panic_stopped`.

## Tuning

- `--mirror-profile`: selects `observe_only`, `click_only`, `move_only`, `full_live_mirror`, or `validation_menu_row`.
- `--mirror-disable-movement` / `--mirror-disable-clicks`: hard-disable a command family.
- `--mirror-echo-suppression`: suppress likely Arduino echo from OS polling.
- `--mirror-echo-window-ms` / `--mirror-click-echo-window-ms`: echo windows.
- `--mirror-max-queue-size`: maximum pending input/echo queue.
- `--mirror-drop-move-older-than-ms`: drop stale movement instead of flushing it late.
- `--mirror-clear-queue-on-menu-selection` / `--mirror-clear-queue-on-game-action` / `--mirror-clear-queue-on-plane-change`: clear pending input after validation milestones.
- `--mirror-auto-pause-after-menu-selection` / `--mirror-auto-pause-after-plane-change`: stop acting after the intended validation event.
- `--mirror-move-min-px`: filters tiny movement noise.
- `--mirror-max-step-px`: chunks large deltas into safe Arduino MOVE steps.
- `--mirror-send-interval-ms`: delay between chunks.
- `--mirror-scale-x` / `--mirror-scale-y`: calibration scale.
- `--mirror-invert-x` / `--mirror-invert-y`: axis correction.
- `--mirror-button-mode click|down_up`: command style for buttons.
- `--mirror-arm-delay-ms`: delay before the mirror arms.
- `--mirror-arm-mode test_window|recording_persistent|manual`: arming behavior.
- `--mirror-test-duration-sec`: auto-disarm window used only by `test_window`.
- `--mirror-persist-until-stop`: keep mirror armed through a full recording.
- `--mirror-keep-armed-while-recording`: explicit persistent recording flag.
- `--mirror-disarm-on-focus-lost`: disarm instead of just dropping events when
  the allowed foreground window is lost.
- `--mirror-panic-stop-file`: file that immediately panic-stops the mirror.

## Arm Modes

- `test_window`: used by UI live mirror smoke tests. It arms after the arm
  delay and auto-disarms after the configured test duration.
- `recording_persistent`: used by full manual recordings. It arms after the arm
  delay and remains armed until recording stop, panic stop, focus policy,
  explicit cleanup, or error.
- `manual`: reserved for explicit arm/disarm callers.

The previous controlled menu-row recording used a 5-second test window during a
full recording. Movement commands were verified early, but the actual menu
clicks happened after disarm. Persistent recording mode fixes that by keeping
the live mirror armed for the whole manual recording.

The analyzer writes `mirror_action_timing_summary.json` and adds the same block
to `summary.json`. A good mirrored menu recording should have:

- `armMode: recording_persistent`
- `menuSelectionsAfterDisarm: 0`
- `actionClicksAfterDisarm: 0`
- non-probe Arduino click commands for the menu actions
- `finalMirrorRecordingVerdict: PASS`

## Next Validation Command

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-menu_row_validation_no_duplicate_click --latest-session --prefer-active-session --interactive --summary --capture-input --input-backend polling --prefer-polling-input --input-preflight --input-preflight-seconds 5 --capture-mouse --capture-keyboard --capture-window-context --raw-input-device-attribution --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode mirror --arduino-probe --arduino-probe-move 25 0 --arduino-probe-observe-ms 750 --mirror-quiet-probe --arduino-live-mirror --mirror-profile validation_menu_row --mirror-disable-movement --mirror-click-policy map_only --mirror-button-mode click --mirror-echo-suppression --mirror-echo-window-ms 250 --mirror-click-echo-window-ms 300 --mirror-echo-max-error-px 100 --mirror-clear-queue-on-menu-selection --mirror-clear-queue-on-game-action --mirror-clear-queue-on-plane-change --mirror-auto-pause-after-menu-selection --mirror-auto-pause-after-plane-change --mirror-auto-pause-after-target-quality medium --mirror-arm-mode recording_persistent --mirror-persist-until-stop --mirror-keep-armed-while-recording --mirror-max-clicks-per-second 4 --mirror-max-button-commands-per-second 8 --mirror-max-move-commands-per-second 120 --mirror-max-total-commands-per-second 150 --mirror-click-cooldown-ms 120 --mirror-same-button-cooldown-ms 80 --mirror-max-burst-commands 50 --mirror-panic-command-threshold 100 --mirror-panic-window-ms 1000 --mirror-arm-delay-ms 500 --mirror-arm-only-when-runelite-focused --mirror-window-title-allow RuneLite --mirror-exclude-window-title "OSRS Telemetry Control" --mirror-ignore-ui-clicks --arduino-mirror-preflight --input-path-integrity --mirror-correlation-window-ms 250 --mirror-max-move-error-px 100 --vm-mouse-mapping --write-arduino-mapping --telemetry-preflight --telemetry-preflight-seconds 5 --max-telemetry-age-ms 3000 --wait-for-fresh-telemetry --wait-for-fresh-telemetry-timeout 30 --menu-capture-burst --menu-burst-ms 2000 --menu-burst-poll-ms 15
```
