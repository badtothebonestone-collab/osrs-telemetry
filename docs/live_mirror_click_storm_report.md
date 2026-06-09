# Live Mirror Click Storm Report

## Fixture Inspected

`C:\Users\badto\.osrs-telemetry\ui_control\arduino_live_mirror_test\20260603_070208_ui_live_mirror_test`

Files inspected:

- `arduino_action_commands.jsonl`
- `arduino_live_mirror_status.json`
- `arduino_live_mirror_summary.json`
- `arduino_mirror_verification.json`
- `input_path_integrity_summary.json`
- `input_events.jsonl`
- `summary.json`

## Observed Command Stream

- Arduino commands generated: `70`
- Probe commands: `1`
- Non-probe live mirror commands: `69`
- MOVE commands: `7` total, `6` non-probe
- CLICK commands: `63`
- MOUSE_DOWN commands: `0`
- MOUSE_UP commands: `0`
- Keyboard commands: `0`
- Ack count: `70`
- Command duration: about `7.875` seconds
- Average command rate: about `8.89` commands/second
- Max total command rate: `16` commands/second
- Max click command rate: `14` click commands/second

The input trace contained `252` events, including `63` mouse-downs, `63` mouse-ups, and `63` clicks. The repeated click stream stayed on RuneLite viewport coordinates around the same screen point.

## Source Event Pattern

The action command stream used one Arduino CLICK per observed OS `click` event. One OS event did not directly generate multiple Arduino CLICK records. Instead, the Arduino CLICK produced an observed Windows click, polling captured that click as a new source event, and live mirror sent another Arduino CLICK.

That feedback loop repeated until the short test ended.

## Root Cause

The previous live mirror path was a raw repeater:

- mirror started active immediately
- the UI click that launched the test was not protected by an arming window
- click mode mirrored every polling `click` event
- output feedback clicks were not suppressed
- no per-button click edge state existed
- no click cooldown existed
- no command-rate cap or panic stop existed
- UI/window filtering was incomplete

The fixed code path is `telemetry-viewer\arduino_live_mirror.py`.

## Fix Summary

The live mirror now:

- starts `disarmed`
- arms only after input capture is ready
- supports an arming delay and optional short test window
- tracks per-button state
- emits one CLICK only for one clean click gesture in `click` mode
- emits MOUSE_DOWN/MOUSE_UP only on edges in `down_up` mode
- drops drag releases and middle-mouse camera drags as normal clicks
- dedupes source event sequences
- suppresses button feedback after Arduino click output
- rate-limits clicks, button commands, movement commands, and total commands
- panic-stops when command bursts exceed the configured threshold
- filters telemetry UI and non-RuneLite foreground input when configured
- writes dropped/throttled/panic counters into `arduino_live_mirror_summary.json`

## Historical Fixture Classification

After the fix, the analyzer classifies this historical run as:

- `inputPathClassification`: `live_mirror_click_storm`
- `mirrorVerificationStatus`: `live_mirror_click_storm`
- `mirrorQuality`: `unsafe_click_storm`
- `liveMirrorVerified`: `false`
- safety classifications: `live_mirror_click_storm`, `live_mirror_feedback_suspected`, `live_mirror_rate_limited`

The historical fixture cannot be repaired retroactively, but it is now impossible to misread as a good mirror validation.
