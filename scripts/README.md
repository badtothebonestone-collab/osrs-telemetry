# Scripts

This file is the recovery command inventory for Codex. During recovery, Codex must run only blessed commands unless a later milestone explicitly changes `PROJECT_STATE.md`.

## Blessed commands

Only this command is approved for Codex to run during recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1
```

## Support commands

These are read-only helpers. They support the blessed command, but they are not separate blessed entrypoints for future Codex runs.

| Command | Purpose | Notes |
| --- | --- | --- |
| `powershell -ExecutionPolicy Bypass -File scripts/doctor.ps1` | Repo/environment doctor. | Read-only; called by the blessed runner. |
| `python telemetry-viewer\context_service.py --latest-session --state-baseline` | R1 read-only state parser/status snapshot. | Read-only; called by the blessed runner. |
| `python telemetry-viewer\context_service.py --latest-session --compact-context` | R2 compact context response from the R1 state baseline. | Read-only; called by the blessed runner while R2 is active. |
| `python scripts\verify_recovery_response.py --schema <schema>` | Narrow JSON verifier for recovery milestone responses. | Read-only; called by deterministic tests, not a separate blessed runner. |

## Deprecated or unknown commands

Codex should not run these unless a future milestone explicitly reactivates them in `PROJECT_STATE.md`.

| Path | Reason it is not blessed | Appears | May still be imported by code? |
| --- | --- | --- | --- |
| `Start-LiveControlPanel.ps1` | Hard-codes `C:\Users\stone\osrs-telemetry\example-plugin` and launches the old control panel path. | obsolete/action-capable | No direct imports found. |
| `Start-NormalLiveStack.ps1` | Hard-codes `C:\Users\stone\osrs-telemetry\example-plugin` and can auto-start the normal live stack. | obsolete/action-capable | No direct imports found. |
| `start_live_control_panel.bat` | Batch wrapper with the same hard-coded old path. | obsolete/action-capable | No direct imports found. |
| `start_normal_live_stack.bat` | Batch wrapper with the same hard-coded old path and auto-start behavior. | obsolete/action-capable | No direct imports found. |
| `gradlew.bat` with `run` | Starts a RuneLite development client; this is not loaded-scene proof and is not the R1 recovery check. | action-capable/unknown for recovery | Build wrapper; not imported. |
| `telemetry-viewer\telemetry_launcher.py` | Older GUI launcher; registry marks it deprecated while older README files still mention it. | obsolete/action-capable | No direct imports found. |
| `telemetry-viewer\live_control_panel.py` | Current daily UI outside recovery, but it launches subprocesses and is not the R1 blessed runner. | action-capable | Yes, tests import it. |
| `telemetry-viewer\live_core_daemon.py` | Current daemon outside recovery, but starting services is outside R1. | read-only service/unknown for recovery | Yes, tests and runtime helpers import it. |
| `telemetry-viewer\run_daily_gauntlet.py` | Useful daily check outside recovery, but it depends on live daemon/session state and is not the R1 blessed runner. | read-only helper/unknown for recovery | Yes, tests and QA helper import it. |
| `telemetry-viewer\run_woodcut_bank_live_qa.py` | Read-only QA outside recovery, but it is activity-specific and outside R1. | read-only helper/unknown for recovery | Yes, tests import it. |
| `telemetry-viewer\context_service.py` modes other than `--state-baseline` and `--compact-context` | Same file contains broader query, handoff, external lookup, and recovery-capable modes; only `--state-baseline` and `--compact-context` are recovery support commands. | mixed/unknown for recovery | Yes, daemon, MCP adapter, and tests import it. |
| `telemetry-viewer\context_service.py --ensure-loaded-scene` | Recovery-capable path that can invoke loaded-scene recovery; not part of R1 state baseline. | action-capable/recovery-capable | Same import status as `context_service.py`. |
| `telemetry-viewer\execute_next_action.py` | Execution-capable CLI; `--execute` can issue live input. | action-capable | Yes, tests and traced-cycle helper import it. |
| `telemetry-viewer\run_runelite_bootstrap.py` | Bootstrap/recovery helper; execution modes can interact with startup surfaces. | action-capable | Yes, recovery core, traced-cycle helper, and tests import it. |
| `telemetry-viewer\tools\run_traced_dev_cycle.py` | Dev-cycle wrapper can delegate to recovery and action execution paths in run mode. | action-capable | Yes, tests import it. |
| `telemetry-viewer\run_stabilization_suite.py` | Broad suite useful outside R1; R1 uses the smaller blessed runner checks. | read-only broad check | No direct imports found. |
| `telemetry-viewer\inspect_live_packets.py` | Retired compatibility shim for removed live packet archive inspection. | obsolete | Tests execute it as a subprocess. |
| `telemetry-viewer\test_telemetry_paths.py` | Deprecated compatibility wrapper; canonical tests live under `telemetry-viewer\tests`. | obsolete/read-only | No direct imports found. |
| `telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply` | Deletion-capable maintenance mode. | action-capable/destructive | Tests import maintenance helpers; do not run apply mode during recovery. |
