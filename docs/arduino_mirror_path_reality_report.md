# Arduino Mirror Path Reality Report

## Current Reality

`Start Arduino Bridge` currently starts `telemetry-viewer\arduino_input_bridge.py`. In normal `bridge` mode it opens the Arduino HID backend, runs health/status commands, records `arduino_events.jsonl`, and writes `arduino_status.json`.

`passthrough-mode mirror` used to change the label and warning text only. It did not turn normal VM mouse input into live Arduino HID commands, and it did not suppress the original OS input. That is why the V2 menu-row validation recording could prove OS input and RuneLite menu geometry, but could not prove the Arduino mirror path.

This pass adds a real action-command wrapper and a deliberate probe path:

- `ArduinoCommandClient.connect()`
- `status()`
- `send_move(dx, dy)`
- `send_click(button)`
- `send_mouse_down(button)`
- `send_mouse_up(button)`
- `send_wheel(delta)`
- `send_key_down(key)`
- `send_key_up(key)`
- `read_ack()` / `poll_ack()`
- `close()`

Action-path command records are written to:

`arduino_action_commands.jsonl`

## Firmware And Protocol Support

The repo firmware at `arduino\ArduinoHIDBridge\ArduinoHIDBridge.ino` supports:

- `MOVE dx dy`
- `CLICK button holdMs`
- `MOUSE_DOWN button`
- `MOUSE_UP button`
- `KEY_DOWN key`
- `KEY_UP key`
- `KEY_PRESS key holdMs`
- `HOLD_KEYS keys holdMs`
- `PING`, `IDENTIFY`, `CAPS`, `STATUS`, `ARM`, `DISARM`, `STOP_ALL`

The firmware does not currently expose a wheel command, so wheel is reported as unsupported with a protocol hint.

## V2 Root Cause

Fixture:

`C:\Users\badto\osrs-telemetry\recordings\20260603_031002_manual_action-menu_row_validation_V2`

V2 had:

- `arduino_events.jsonl`: connect, error, disconnect
- `arduino_action_commands.jsonl`: missing
- movement commands: 0
- click commands: 0
- acknowledgements: 0
- observed OS input: present

`arduino_status.json` showed:

`ArduinoHIDError: Arduino serial port COM6 is locked by osrs-telemetry:12596`

So V2 failed mirror proof for two reasons:

1. The old mirror bridge path never sent movement/click commands.
2. COM6 was locked by another process during status startup, so even status/health could not complete cleanly.

The coordinate/menu-row result remains valid, but V2 cannot prove mirror mode retroactively because no action-path command stream exists in the recording.

## What Changed

The new command wrapper sends real protocol commands through the existing `ArduinoHIDBackend` and records structured command rows:

- `command_id`
- `command_kind`
- `sent_at_monotonic`
- `sent_at_utc`
- `port`
- `protocol`
- `payload`
- `expected_ack`
- `ack_received`
- `ack_at_monotonic`
- `ack_latency_ms`
- `error`
- `raw_line`

The probe verifier can now run:

```powershell
python telemetry-viewer\arduino_mirror_verifier.py --probe --port COM6 --move 12 0 --observe-ms 500
```

It sends a deliberate Arduino move, observes cursor movement, and classifies the result.

## Classification Meanings

| Classification | Meaning |
|---|---|
| `os_polling_only` | OS polling captured input; no Arduino command path evidence. |
| `arduino_status_only` | Arduino status/health worked, but no action commands were seen. |
| `arduino_bridge_connected` | Bridge connected, but action-path evidence is incomplete. |
| `arduino_probe_verified` | A deliberate Arduino probe command produced observed cursor/button evidence. |
| `arduino_mirror_requested` | Mirror was requested, but no proof exists yet. |
| `arduino_mirror_active` | Non-probe action commands were sent during the recording. |
| `arduino_mirror_verified` | Non-probe action commands correlate with observed OS input. |
| `arduino_mirror_failed` | Mirror was requested but action-path proof is missing or failed. |
| `conversion_trace_only` | OS input was converted to Arduino-style deltas offline; it was not sent live. |
| `mixed_input_path` | Arduino commands and unrelated OS input both appear. |
| `unknown_input_path` | Not enough evidence to classify. |

## Active Mirror Caveat

The probe makes the Arduino command path real and testable. `arduino_live_mirror.py` now provides the first live mirror stream for manual recordings: captured OS input events are converted into non-probe Arduino action commands during the recording.

There is still double-input risk because the original OS input is not suppressed. The analyzer detects this with `mixed_input_path`, uncorrelated OS input counts, and live mirror correlation counts.

A stricter active mirror would require one of these designs:

- capture normal OS input and suppress the original event while replaying through Arduino,
- use Arduino HID as the actual physical input source,
- or route generated action commands through the Arduino backend directly.

If normal OS input remains active while Arduino also sends commands, the analyzer can classify this as `mixed_input_path` or flag possible double input.

## Code Path Needed For `arduino_mirror_verified`

`arduino_mirror_verified` requires non-probe action commands in `arduino_action_commands.jsonl` that correlate with observed input during the recording.

That means the live action code must send movement/clicks through:

`HumanInputController -> ArduinoHIDBackend`

or another explicit caller of `ArduinoCommandClient`.

Passive `Start Arduino Bridge` status capture is not enough. Use `--arduino-live-mirror` so `arduino_action_commands.jsonl` contains non-probe `MOVE`/`CLICK` rows.
