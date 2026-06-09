# Arduino Mirror Verification

Arduino mirror verification separates normal OS polling evidence from proof that input actually traveled through the Arduino path.

## Classifications

- `os_polling_only`: OS input was captured, no Arduino action path was observed.
- `arduino_status_only`: Arduino health/status commands were captured, but no action commands.
- `arduino_bridge_connected`: Arduino bridge connected, but action-path evidence is incomplete.
- `arduino_probe_verified_clean`: a deliberate probe move/click command produced an ack and clean matching cursor/button evidence.
- `arduino_probe_verified_noisy`: a deliberate probe command worked, but background movement or movement error made the observation noisy.
- `arduino_probe_sent_no_observed_delta`: a probe command was sent, but no cursor delta was observed.
- `arduino_mirror_requested`: mirror mode was requested but not yet proven.
- `arduino_mirror_active`: mirror action commands were seen, but not correlated to observed OS input.
- `arduino_mirror_verified`: Arduino action commands correlate with observed cursor/button input.
- `arduino_mirror_failed`: mirror mode was requested but action-path proof is missing.
- `conversion_trace_only`: OS input was converted to Arduino-style deltas offline, but was not sent live.
- `mixed_input_path`: mirror commands and uncorrelated OS input appear together.
- `live_mirror_click_storm`: repeated live CLICK commands exceeded human-safe rates.
- `live_mirror_rate_limited`: the mirror sent or attempted enough commands to trigger throttling.
- `live_mirror_panic_stopped`: the mirror stopped itself because the panic threshold was reached.
- `live_mirror_feedback_suspected`: output commands appear to have been captured again as source input.
- `live_mirror_ui_click_loop_suspected`: telemetry UI/control input appears in the mirror path.
- `live_mirror_duplicate_click_risk`: a normal OS click was also mirrored as a live Arduino click without source suppression.
- `unknown_input_path`: insufficient evidence.

## Evidence Chain

The desired chain is:

physical or VM input event -> Arduino mirror command/event -> Arduino ack/status -> observed Windows cursor/button/key event -> RuneLite telemetry/menu/target reaction.

Probe verification proves the Arduino command path can move/click. It does not prove that every normal VM mouse action is being replayed through Arduino during a manual recording.

Live mirror verification requires `liveMirrorCommand: true` and `probeCommand: false` rows in `arduino_action_commands.jsonl`, followed by observed cursor/button evidence.

The analyzer writes:

- `arduino_mirror_verification.json`
- `input_path_integrity_summary.json`
- `arduino_action_commands.jsonl`

Useful fields:

- `requestedMode`
- `inputPathClassification`
- `mirrorVerificationStatus`
- `movementCommandCount`
- `clickCommandCount`
- `ackCount`
- `correlatedCommandToObservedMovementCount`
- `correlatedCommandToObservedClickCount`
- `conversionErrorPx`
- `possibleDoubleInput`
- `maxArduinoCommandsPerSecond`
- `maxClickCommandsPerSecond`
- `droppedCommandCount`
- `throttledCommandCount`
- `panicStopCount`
- `liveMirrorSafetyClassifications`
- `armMode`
- `disarmReason`
- `mirrorArmedStartElapsedSeconds`
- `mirrorDisarmElapsedSeconds`
- `menuSelectionsAfterDisarm`
- `actionClicksAfterDisarm`
- `finalMirrorRecordingVerdict`
- `clickPolicyUsed`
- `duplicateClickLikelyCount`
- `liveClickWithoutSuppressionCount`
- `mapOnlyClickCount`
- `clickOwners`

Correlation alone is not enough for verification. A run with a click feedback loop can correlate perfectly because each Arduino click creates the next observed click. The analyzer therefore marks unsafe command rates as `live_mirror_click_storm` and keeps `liveMirrorVerified: false`.

Correlation also does not prove click ownership. If a normal OS/manual click is captured and the mirror sends a live Arduino `CLICK` for the same gesture, the analyzer reports `duplicate_os_plus_arduino_click`, sets `possibleDoubleInput: true`, and downgrades the final recording verdict to `WARN`. Use `map_only` for menu validation unless the original OS click can be suppressed or the Arduino is the actual physical input source.

## Raw Input Attribution

Raw Input device attribution is optional. When unavailable, the recording still uses polling input and relies on Arduino command-to-observed-cursor correlation.

## Strict Mode

Use `--require-arduino-mirror-verified` when a recording should not start unless mirror verification passes. If the current bridge cannot prove mirror mode, strict mode fails rather than guessing.

Use this probe first:

```powershell
python telemetry-viewer\arduino_mirror_verifier.py --probe --port COM6 --move 25 0 --observe-ms 750 --quiet-window
```

Then record with `--arduino-live-mirror` and inspect `arduino_action_commands.jsonl`, `arduino_live_mirror_summary.json`, and `input_path_integrity_summary.json`.

## Persistent Arm Diagnosis

The analyzer distinguishes a safe mirror test from a full recording:

- `test_window` is correct for `Run Live Mirror Test`; it arms briefly and
  auto-disarms after the configured duration.
- `recording_persistent` is correct for manual recordings; it remains armed
  until Stop Recording, panic stop, focus policy, or cleanup.

If a full recording used `test_window`, the analyzer reports
`test_window_used_for_recording`, `recording_persistent_arm_missing`, and
counts action/menu clicks that happened after disarm. The final mirror recording
verdict is:

- `PASS`: action clicks occurred while mirror was armed and command correlation
  is available.
- `WARN`: mirror worked, but game/menu actions happened after disarm.
- `FAIL`: mirror was requested but could not become active, or the path was
  unsafe.

For menu-row validation, `menuSelectionsAfterDisarm` must be `0`, and the
click ownership summary should have `duplicateClickLikelyCount: 0`. With the
safe `map_only` click policy, live non-probe Arduino click commands are not
expected; the useful proof is the OS click, menu-row geometry, target quality,
and VM-to-Arduino click mapping.
