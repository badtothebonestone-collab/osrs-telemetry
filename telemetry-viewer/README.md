# Python Telemetry Toolchain

`telemetry-viewer` currently contains the Python telemetry toolchain, not only
viewer code. The folder name is kept for compatibility. User-facing scripts are
intentionally left at this root so existing commands continue to work.

A future repo cleanup may rename or split this folder as `telemetry-tools`.

## Launch And Control

```powershell
python telemetry-viewer\telemetry_launcher.py
```

Most users should use the launcher first. Individual scripts remain available
for debugging and advanced workflows.

Happy path:

1. Start Collection
2. Wait for the launcher to lock a fresh active session
3. Calibrate if needed, then Save Default Profile
4. Replay / label tick ranges
5. Build Dataset
6. Inspect Dataset
7. Export Curated
8. Run Doctor / Status

Start Collection launches RuneLite, records the collection start time, ignores
old stale sessions, and waits until fresh ticks arrive before locking the
active session. Replay, calibration, dataset building, curated export, and
status tools then prefer that locked session. Advanced mode is for debugging
and direct script-level commands only.

Test crops are disposable previews under `perception\test_crops\<run_id>`.
`training_data` is persistent, and `curated_manifest.jsonl` is the clean list
for later model/training experiments.

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

## Dataset Pipeline

```powershell
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\training_dataset_inspector.py
python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
python telemetry-viewer\dataset_status.py
```

## Target Geometry QA

```powershell
python telemetry-viewer\target_geometry_inspector.py
```

Open `http://127.0.0.1:8800/` to overlay existing
`interaction_geometry` UI/world target records on retained frame images. This
is read-only QA tooling. It does not interact with RuneLite, send input, or
modify telemetry, geometry, or frame files.

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
