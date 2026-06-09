# Probe Vs Live Mirror Analysis

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260603_053706_manual_action-menu_row_validation_mirror_probe_verified`

## Verdict

`WARN`: the Arduino command wrapper is proven, but the historical recording did not contain a live mirror action stream.

## Probe Result

- Probe classification: `arduino_probe_verified_noisy`
- Probe command: `MOVE 12 0`
- Arduino port/protocol: `COM6` / `arduino_hid.v1`
- Ack: `OK MOVE`
- Ack latency: `47 ms`
- Observed probe delta: `-465,178`
- Error from commanded delta: `-477,178`

The probe sent a real Arduino command and received an ack, but the observed cursor movement was far from the commanded vector. That is command-path proof, not clean calibration proof.

## Live Mirror Result

- Requested mode: `mirror`
- Input path classification: `arduino_probe_verified_noisy`
- Live mirror active: `false`
- Live mirror verified: `false`
- Non-probe action commands: `0`
- Movement/click commands: `1` / `0`
- Correlated movement/click commands: `0` / `0`

The only movement command was the probe command. There were no `liveMirrorCommand: true` non-probe rows in `arduino_action_commands.jsonl`.

## Interpretation

The historical recording proves:

- COM6 was usable.
- The protocol wrapper can send a MOVE command.
- The Arduino firmware acknowledged the command.

It does not prove:

- normal manual input was mirrored through Arduino,
- live non-probe MOVE/CLICK commands were emitted during the action,
- those commands correlated with observed cursor/button evidence.

## Next Recording

Run a new mirror validation recording with `--arduino-live-mirror`. After analysis, `input_path_integrity_summary.json` should show either:

- `arduino_mirror_active`: non-probe commands were sent but not correlated yet, or
- `arduino_mirror_verified`: non-probe commands correlated with observed input.

