# OSRS Telemetry Recorder

`telemetry-viewer\telemetry_ui.py` opens the Record Everything console. The UI
is meant to behave like a recording appliance: start the game, start telemetry,
record what you do, stop, and read the automatic analysis.

## Normal Use

1. Open OSRS Telemetry Recorder.
2. Click `Start Game`.
3. Click `Start Telemetry`.
4. Click `Start Recording`.
5. Do the task normally.
6. Click `Stop Recording`.
7. Read the automatic analysis summary.
8. Click `Open Output Folder` if you need the files.

The main screen shows only:

- Status cards: `Game`, `Telemetry`, `Recording`, `Last Result`, and `Output`.
- Buttons: `Start Game`, `Start Telemetry`, `Start Recording`,
  `Stop Recording`, `Analyze Latest`, `Open Output Folder`, and
  `Diagnostics / Settings`.
- Small optional fields: `Label`, `Output folder`, and
  `Auto Analyze After Stop`.

No route template, Arduino, mirror, capture, input backend, analyzer flag, or
route monitor setting is required for normal recording.

## Record Everything Default

Start Recording always uses the internal `record_everything_default` profile.
It records broad evidence first and lets the analyzer decide afterward whether
the run was a route, woodcutting, banking, menu interaction, camera/input
sample, or generic telemetry recording.

The default profile uses:

- latest active session and telemetry preflight
- fresh-telemetry wait when supported
- polling input capture
- mouse, keyboard, and window context
- optional raw input attribution
- input/telemetry joining
- camera behavior
- menu capture burst until selection
- coordinate alignment, input classification, target quality, and menu
  interaction analysis
- traversal lifecycle and grouped route steps
- banking lifecycle and inventory-delta analysis
- route monitor/history when a matching route template can be selected
- Arduino probe/mapping only when Arduino is configured

Arduino is optional. Route templates are optional. Live Arduino clicks and
movement mirroring are off by default.

For traversal recordings, Simple Mode does not force every run through the
default bank-to-woodcutting template. Analysis auto-detects the route from
start/end areas, picks the matching one-way template when available, and reports
`untemplated route` when no matching template exists yet.

## Stop And Analyze

Stop Recording stops the recorder cleanly. If `Auto Analyze After Stop` is
checked, the analyzer runs in the background and the UI remains responsive.

The result area shows:

- detected activity type
- `PASS`, `WARN`, or `FAIL`
- biggest warning
- latest report path

Combat interruptions can add a compact summary line with cause/opponent, damage
taken/dealt, hitsplat count, HP change, and whether the task resumed. This is
reported automatically; no main-screen combat setting is needed.

Banking recordings show as `Banking` when `banking_lifecycle.json` detects a
bank/deposit action, deposited or withdrawn items, or direct bank state. If the
bank action is inferred from inventory/menu evidence but direct bank state is
missing, the result is usually `WARN` and the warning names the missing bank
capabilities.

Record Everything preserves the bridge `bank_ui` live-cache payload when the
plugin snapshot endpoint provides it. The main screen does not add a bank
setting; the Banking summary simply reports direct bank evidence and bank
container availability after analysis. When bank-side item deltas are available,
the summary also reports bank delta evidence so a deposit can be distinguished
from inventory-only inference.

The primary report is usually:

```text
schema_gap_report.md
```

The recording folder also contains `summary.json` and any relevant sidecar
artifacts created by the analyzer.

## Output Folder

Recordings default to:

```text
C:\Users\badto\osrs-telemetry\recordings
```

Use `Change Output Folder` if you want recordings elsewhere. The selected output
folder is saved and reused.

## Session Manifest

Every UI-started recording writes:

```text
%USERPROFILE%\.osrs-telemetry\ui_control\recording_session\ui_recording_session_manifest.json
```

The manifest records the profile, label, output folder, recording folder,
telemetry status at start, optional route template and route monitor folder,
recorder command, analyzer command, detected activity, final verdict, biggest
warning, and report path. For banking recordings, the automatic analysis summary
can show deposited items, direct bank evidence, bank container availability, and
whether bank container delta confirmation was present.

## Diagnostics / Settings

Diagnostics is for troubleshooting only. Normal recording should not require it.

It contains:

- `Paths`: output folder, game launch command, repo root
- `Telemetry`: active session, freshness, context service status
- `Route Templates`: default template validation and compare/extract helpers
- `Arduino / Mapping`: optional port, probe, mapping-only evidence, and
  experimental live mirror controls
- `Commands / Logs`: generated commands and recent logs
- `Reset`: restore recommended defaults

## Commands

Open the UI:

```powershell
python telemetry-viewer\telemetry_ui.py
```

Run checks without opening the UI:

```powershell
python telemetry-viewer\telemetry_ui.py --check
```

Reset to the recommended Record Everything console:

```powershell
python telemetry-viewer\telemetry_ui.py --reset-config --check
```

Open Diagnostics on launch:

```powershell
python telemetry-viewer\telemetry_ui.py --diagnostics
```

Old configs that reopened advanced panels are migrated back to the simple
console on load.
## Interruption Summary

The Simple Mode screen still has no combat/interruption settings. After analysis, the compact result/log can show:
- interruption type
- primary cause
- whether the task resumed
- combat observed yes/no
- hitsplats count

If an old recording lacks direct combat telemetry, the UI should show the analyzer warning rather than treating the recording as failed.

## Woodcutting Loop Summary

Simple Mode does not add a woodcutting-loop control. Analyze Latest runs the
loop lifecycle automatically when useful artifacts are present. The compact
result/log can show:

- current phase
- next expected phase
- verdict
- biggest warning

Use Diagnostics / Settings only when you need to open
`woodcutting_loop_lifecycle.json` directly.
