# Project State

## Active branch

`recovery/2026-06-14-surgical-reset`

## Active worktree

`C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery`

## Current architecture boundary

Recovery is limited to read-only state work: parse available telemetry/state, validate shape/freshness, summarize current status, and report clear blockers.

Runtime/source feature work, gameplay behavior, route behavior, banking behavior, direct input, and anti-detection behavior are outside the current recovery boundary.

## Blessed run/check command

`powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

This is the only blessed command for the current recovery milestone. Do not invent substitute run commands.

The command runs `scripts\doctor.ps1`, in-memory Python syntax compilation, and deterministic standard-library unittest checks.

Because `MILESTONES.md` marks R2 active, the deterministic gate includes:

- `telemetry-viewer\tests\test_state_baseline.py`
- `telemetry-viewer\tests\test_compact_context_boundary.py`
- `telemetry-viewer\tests\test_recovery_response_verifier.py`

The verifier test exercises deterministic fixture CLI output and `scripts\verify_recovery_response.py`. Required responses fail the gate on invalid JSON, missing required fields, `status: "FAIL"`, `ok: false`, forbidden response fields, or forbidden compact-context response text.

The runner may print `--latest-session` R1/R2 output as an optional diagnostic. Latest-session warnings or `ok: false` are advisory only and are not the proof that R1/R2 passed.

Command quarantine and support command details live in `scripts\README.md`. That file does not bless any additional recovery entrypoint.

## Canonical R1 state parser

- Path: `telemetry-viewer\context_service.py`
- Status command: `python telemetry-viewer\context_service.py --latest-session --state-baseline`
- Output schema: `recovery_state_baseline.v1`

The parser reads existing live state files under `interaction_geometry\live` when a session is available. It reports a compact snapshot with game state, logged-in inference, tick, timestamp, state age, player position, inventory, bank, and activity fields when those fields are already present in read-only telemetry.

Current known limitations:

- Missing state is reported as `WARN`, not repaired.
- Malformed JSON is reported as `WARN`, not rewritten.
- Stale timestamps are reported as `WARN`, not refreshed.
- The parser does not prove loaded-scene readiness.
- The parser does not start RuneLite, the daemon, the snapshot endpoint, or any external service.
- The parser emits no click, mouse, keyboard, menu, route, banking, task, or gameplay action command.

## Canonical R2 compact context boundary

- Path: `telemetry-viewer\context_service.py`
- Status command: `python telemetry-viewer\context_service.py --latest-session --compact-context`
- Optional request: `--compact-context-request '{"schema":"context_request.v1","needs":["state","player","inventory","source"],"responseMode":"compact"}'`
- Request schema: `context_request.v1`
- Response schema: `context_response.v1`

The compact context boundary consumes the R1 `recovery_state_baseline.v1` payload and returns only read-only facts: schema, ok, errors, warnings, timestamp/tick/state age, game/logged-in state, player position, inventory summary, an allowlisted activity summary when already present, liveness summary, and source metadata.

Unsupported compact-context request values are never echoed verbatim. Unsupported `needs`, `task`, `profile`, `responseMode`, invalid schema, invalid JSON, and unknown request fields are reported with safe codes such as `unsupported_need`, `unsupported_task`, `unsupported_profile`, `unsupported_response_mode`, `invalid_schema`, `invalid_json_request`, and `unsupported_request_field_count`.

Current known limitations:

- It does not choose tasks, routes, targets, or banking behavior.
- It does not prove loaded-scene readiness.
- It does not emit action, click, mouse, keyboard, menu, input, command, movement, interact, interaction, target, execute, anti-detection, or gameplay command fields.
- It does not start RuneLite, the daemon, the snapshot endpoint, or any external service.

## Current known entrypoints

- `src\main\java\com\osrstelemetry\TelemetryPlugin.java` - RuneLite plugin entrypoint.
- `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java` - local read-only snapshot endpoint.
- `src\main\java\com\osrstelemetry\WorldModelCache.java` - loaded-scene/world-model cache.
- `telemetry-viewer\live_core_daemon.py` - read-only live daemon on `127.0.0.1:8890`.
- `telemetry-viewer\context_service.py` - query/context service and recovery-capable CLI.
- `telemetry-viewer\live_control_panel.py` - current daily control panel per registry.
- `telemetry-viewer\run_daily_gauntlet.py` - daily sanity check surface.
- `telemetry-viewer\run_woodcut_bank_live_qa.py` - read-only live QA surface.
- `telemetry-viewer\diagnose_live_readiness.py` - readiness diagnostic.
- `telemetry-viewer\execute_next_action.py` - execution-capable action CLI; out of scope during recovery milestones.
- `telemetry-viewer\run_runelite_bootstrap.py` - recovery/bootstrap helper; execution-capable when flags allow it.
- `telemetry-viewer\tools\run_traced_dev_cycle.py` - traced dev-cycle wrapper; execution-capable in run mode.
- `scripts\doctor.ps1` - read-only repository/environment doctor.
- `scripts\verify_recovery_response.py` - JSON verifier used by deterministic recovery tests.
- `scripts\run_current_milestone.ps1` - blessed current-milestone recovery runner.
- `gradlew.bat` / `gradlew` - Gradle wrapper for Java build/test/dev launch.

## Current known test/check commands

- `.\gradlew.bat test`
- `.\gradlew.bat build`
- `python telemetry-viewer\run_stabilization_suite.py`
- `python telemetry-viewer\tests\test_telemetry_paths.py`
- `python telemetry-viewer\context_service.py --pipeline-health`
- `python telemetry-viewer\context_service.py --latest-session --state-baseline`
- `python telemetry-viewer\context_service.py --latest-session --compact-context`
- `python telemetry-viewer\tests\test_recovery_response_verifier.py`
- `python telemetry-viewer\context_service.py --query pipeline-health`
- `python telemetry-viewer\context_service.py --query current-debug-context`
- `python telemetry-viewer\context_service.py --query explain-current-blocker`
- `python telemetry-viewer\context_service.py --query run-readiness`
- `python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting`
- `python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes`

These are discovered commands, not the blessed recovery command. The `--latest-session` commands are optional diagnostics when printed by the blessed runner.

## Current known telemetry/state inputs

- RuneLite plugin snapshot endpoint on `127.0.0.1:8893`.
- Live core daemon on `127.0.0.1:8890`.
- Current session files under `telemetry-viewer\sessions\`.
- Knowledge Fabric and context queries from `telemetry-viewer\context_service.py`.
- Pipeline health, readiness, and blocker diagnostics.
- Loaded-scene evidence from current game state, client tick hot state, player state, world model objects, and play-panel status.

## Explicitly out of scope during recovery

- Feature implementation.
- Runtime/source refactors.
- Anti-detection, evasion, randomization, bypass, or stealth behavior.
- Click, mouse, keyboard, menu, banking, route, task, or gameplay action execution.
- Treating `gradlew run` as loaded-scene proof.
- Copying execution-capable commands from old docs.
- Moving uncertain historical docs without human review.
