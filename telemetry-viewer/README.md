# Python Telemetry Toolchain

`telemetry-viewer` currently contains the Python telemetry toolchain, not only
viewer code. The folder name is kept for compatibility. User-facing scripts are
intentionally left at this root so existing commands continue to work.

A future repo cleanup may rename or split this folder as `telemetry-tools`.

## Launch And Control

```powershell
python telemetry-viewer\telemetry_launcher.py
```

## Replay

```powershell
python telemetry-viewer\replay_viewer.py
```

## Validation

```powershell
python telemetry-viewer\validate_session.py
```

## Export

```powershell
python telemetry-viewer\export_session.py
```

## Perception Build

```powershell
python telemetry-viewer\build_perception_dataset.py
```

## Perception Inspect

```powershell
python telemetry-viewer\inspect_perception.py
```

## Latest State

```powershell
python telemetry-viewer\latest_state.py
```

## Path Tests

```powershell
python telemetry-viewer\tests\test_telemetry_paths.py
```

The legacy command below remains as a compatibility wrapper:

```powershell
python telemetry-viewer\test_telemetry_paths.py
```
