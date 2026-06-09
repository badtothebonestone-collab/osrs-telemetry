# Arduino Input Path Recording

`arduino_input_bridge.py` records Arduino HID input-path evidence for manual
recordings. It reuses the existing Arduino HID protocol shape used by
`input_control/backend_arduino_hid.py` and the sketch under
`arduino/ArduinoHIDBridge`.

The bridge is optional. If no hardware or serial library is available, normal
recording can continue unless Arduino is explicitly required.

## Modes

- `off`: no Arduino involvement.
- `label_only`: mark the recording for Arduino-style analysis without opening a
  serial connection.
- `bridge`: connect, run status/health checks, and record command/ack evidence.
- `mirror`: request mirror analysis. This is only proven when probe/action
  commands are sent through Arduino and correlated to observed OS input.
- `arduino_probe_verified`: a deliberate probe command moved/clicked through
  Arduino and produced observed evidence.
- `conversion_trace_only`: OS input was converted to Arduino-style deltas
  offline; it was not sent live through Arduino.

## Commands

```powershell
python telemetry-viewer\arduino_input_bridge.py --list-ports
python telemetry-viewer\arduino_input_bridge.py --status
python telemetry-viewer\arduino_input_bridge.py --port COM6 --status
python telemetry-viewer\arduino_mirror_verifier.py --probe --port COM6 --move 12 0 --observe-ms 500
python telemetry-viewer\arduino_input_bridge.py --port COM6 --calibrate --out recordings\<folder>\arduino_calibration.json
python telemetry-viewer\arduino_input_bridge.py --port COM6 --record-events --out recordings\<folder>
```

## Recording With Arduino Evidence

```powershell
python telemetry-viewer\manual_recorder.py --label manual_action-Tree_cutting_input_arduino_trace --latest-session --interactive --summary --capture-input --capture-mouse --capture-keyboard --capture-window-context --join-input-telemetry --camera-behavior --arduino --arduino-auto-start --arduino-record-events --arduino-passthrough-mode bridge --vm-mouse-mapping --write-arduino-mapping
```

Use `--arduino-required` only when you want startup to fail if the board is not
available.

## Output Files

- `arduino_events.jsonl`: connect/status/command/ack/error/calibration events.
- `arduino_action_commands.jsonl`: structured action-path MOVE/CLICK/key
  command records with command ids, ack status, latency, and errors.
- `arduino_status.json`: latest bridge status and backend health.
- `arduino_calibration.json`: optional commanded versus observed movement
  samples.
- `arduino_trace_summary.json`: analyzer summary of Arduino events.
- `vm_mouse_arduino_mapping.json`: observed VM mouse path represented as
  Arduino-style relative deltas.

## Evidence Types

- OS input trace: mouse/key events captured by `input_trace_recorder.py`.
- Telemetry-observed click history: RuneLite/plugin fields such as
  `lastMenuOptionClicked`, useful for game-side action evidence but not proof
  that OS input capture worked.
- Arduino status/health events: PING, IDENTIFY, CAPS, STATUS, STOP_ALL, and
  acknowledgements proving the bridge was connected.
- Arduino per-action command events: MOVE, CLICK, MOUSE_DOWN, MOUSE_UP,
  KEY_DOWN, KEY_UP, WHEEL, and related commands proving the Arduino action path
  carried movement/click/key commands.
- VM mouse to Arduino mapping: Python-derived relative movement/click command
  suggestions from observed OS input trace.

The analyzer classifies Arduino traces as `arduino_unavailable`,
`arduino_status_only`, `arduino_bridge_connected`,
`arduino_action_commands_seen`, `arduino_mirror_mode_seen`, or
`arduino_mapping_only`. If only health/status commands are present, the report
will say that the bridge connected but no per-action movement/click command
stream was captured.

Input-path integrity uses the richer labels `arduino_probe_verified`,
`arduino_mirror_active`, `arduino_mirror_verified`, `arduino_mirror_failed`,
`conversion_trace_only`, and `mixed_input_path`.

## Troubleshooting

- No port found: set the COM port manually or run `--list-ports`.
- Serial permission error: close other tools using the same COM port.
- Firmware/protocol not recognized: flash `arduino/ArduinoHIDBridge`.
- Mirror requested but not proven: run the Arduino probe and inspect
  `arduino_action_commands.jsonl`.
- Arduino disconnects: the recording remains usable, and the bridge records an
  error event/status warning.
## Mirror Verification

Mirror mode is not considered proven just because the Arduino bridge is connected. Use `input_path_integrity_summary.json` and `arduino_mirror_verification.json` to check whether movement/click commands were captured, acknowledged, and correlated with observed OS input.
## Probe, Conversion, And Live Mirror

`arduino_probe_verified_clean` or `arduino_probe_verified_noisy` means a
deliberate test command reached the Arduino. It does not prove the manual
recording action stream used Arduino.

`conversion_trace_only` means the system observed OS input and converted it into
Arduino-style deltas after the fact.

`arduino_mirror_active` means non-probe Arduino commands were sent during the
recording. `arduino_mirror_verified` means those non-probe commands correlated
with observed cursor/button evidence.
