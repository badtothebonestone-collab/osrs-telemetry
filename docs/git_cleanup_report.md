# Git Cleanup Report

Date: 2026-06-09

## Branch

Checkpoint branch:

```text
stabilization/live-loop-recovery-20260609
```

Base branch before checkout: `master`.

## Files To Commit

The checkpoint stages the project state in these categories:

- Source code: `telemetry-viewer\*.py`, `telemetry-viewer\input_control\*.py`,
  and `src\main\java\com\osrstelemetry\*.java`.
- Tests: `telemetry-viewer\tests\*.py`.
- Docs and knowledge: `docs\*.md`, `docs\knowledge\*.md`, and
  `telemetry-viewer\knowledge_base\*.json`.
- Stable route assets: `route_templates\*.json` and `route_guides\*.json`.
- Small helper launcher: `run_telemetry_ui.bat`.

## Files Intentionally Ignored

`.gitignore` now keeps these generated/local paths out of normal status noise:

- `bot_runs/`
- `recordings/`
- `**/__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `*.tmp`
- `*.temp`
- `*.bak`
- runtime logs, including `telemetry-viewer/logs/`

Existing ignored local paths remain in place for `.osrs-telemetry/`, live logs,
parquet/duckdb exports, local config, and calibration/status artifacts.

## Local Artifacts Not Committed

See `docs\local_artifact_inventory.md`.

Summary:

- `bot_runs/`: 127 folders, 3745 files, 546,677,503 bytes.
- `recordings/`: 84 folders, 2210 files, 472,242,941 bytes.
- `telemetry-viewer/logs/live_core_daemon_8890.out.log`: about 432 MB.

## Files Left Dirty

After the checkpoint commit, the expected remaining dirty items should be only
ignored local runtime artifacts. If non-ignored files remain, treat that as a
staging mistake and inspect `git status --short --untracked-files=all` before any
more live work.
