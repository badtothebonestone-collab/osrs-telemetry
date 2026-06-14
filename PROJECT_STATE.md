# Project State

## Active branch

`work/resume-script-development`

## Active worktree

`C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery`

## Recovery mode and baseline status

Recovery mode is complete. The recovered project is baseline-ready on `work/resume-script-development`; normal script-development work should start from this branch.

The deterministic baseline remains the R1/R2/R3/R4 read-only recovery gate. It includes:

- R1 state baseline checks
- R2 compact context boundary checks
- R2 response verifier checks
- R3 no-action diagnostic checks
- R4 read-only live-readiness fixture checks

No R6, R7, or R8 milestone is active.

The dirty old checkout at `C:\Users\badto\osrs-telemetry` remains quarantined and reference-only. Do not import, copy, merge, or execute code from that checkout without a new explicit milestone and review.

## Current architecture boundary

The recovered baseline is limited to read-only state, compact context, diagnostic work, deterministic live-readiness fixture validation, and documentation-only integration triage: parse available telemetry/state, validate shape/freshness, summarize current status, report clear blockers, and document how old dirty-checkout changes should be evaluated.

Runtime/source feature work, gameplay behavior, route behavior, banking behavior, direct input, and anti-detection behavior are outside the current recovery boundary.

R4 validates loaded-scene readiness as observation-readiness only. It proves that live-like fixture telemetry is handled safely across the R1/R2/R3 boundary; it does not grant permission to choose a task, route, target, bank, activity, or action.

R5 documentation-only integration triage is complete. It inventories old dirty-checkout changes and defines how they may be evaluated later against the recovered R1/R2/R3/R4 boundary. It does not import, copy, merge, restore, or execute old behavior.

## Blessed run/check command

`powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

This is the blessed baseline command. Do not invent substitute run commands.

The command runs `scripts\doctor.ps1`, in-memory Python syntax compilation, and deterministic standard-library unittest checks.

The deterministic baseline currently includes:

- `telemetry-viewer\tests\test_state_baseline.py`
- `telemetry-viewer\tests\test_compact_context_boundary.py`
- `telemetry-viewer\tests\test_recovery_response_verifier.py`
- `telemetry-viewer\tests\test_recovery_diagnostics.py`
- `telemetry-viewer\tests\test_r4_live_readiness_fixtures.py`

The verifier test exercises deterministic fixture CLI output and `scripts\verify_recovery_response.py`. Required R1/R2 responses fail the gate on invalid JSON, missing required fields, `status: "FAIL"`, `ok: false`, forbidden response fields, or forbidden compact-context response text. The R3 test is deterministic and no-action; it consumes in-memory `context_response.v1` fixtures only. The R4 test is deterministic and fixture-only; it consumes local fixture data and in-memory diagnostic helpers only.

For R4, the runner does not call `--latest-session`. Latest-session diagnostics remain optional outside R4 and are not proof that loaded-scene observation readiness passed.

Command quarantine and support command details live in `scripts\README.md`. That file does not bless any additional recovery entrypoint.

## Canonical R1 state parser

- Payload module: `telemetry-viewer\state_baseline.py`
- CLI wrapper: `telemetry-viewer\context_service.py`
- Status command: `python telemetry-viewer\context_service.py --latest-session --state-baseline`
- Output schema: `recovery_state_baseline.v1`

The parser reads existing live state files under `interaction_geometry\live` when a session is available. `context_service.py` loads the existing files and delegates read-only normalization to `state_baseline.py`. It reports a compact snapshot with game state, logged-in inference, tick, timestamp, state age, player position, inventory, bank, and activity fields when those fields are already present in read-only telemetry.

Current known limitations:

- Missing state is reported as `WARN`, not repaired.
- Malformed JSON is reported as `WARN`, not rewritten.
- Stale timestamps are reported as `WARN`, not refreshed.
- The parser does not prove loaded-scene readiness.
- The parser does not start RuneLite, the daemon, the snapshot endpoint, or any external service.
- The parser emits no click, mouse, keyboard, menu, route, banking, task, or gameplay action command.

## Canonical R2 compact context boundary

- Payload module: `telemetry-viewer\context_boundary.py`
- CLI wrapper: `telemetry-viewer\context_service.py`
- Status command: `python telemetry-viewer\context_service.py --latest-session --compact-context`
- Optional request: `--compact-context-request '{"schema":"context_request.v1","needs":["state","player","inventory","source"],"responseMode":"compact"}'`
- Request schema: `context_request.v1`
- Response schema: `context_response.v1`

The compact context boundary consumes the R1 `recovery_state_baseline.v1` payload and returns only read-only facts: schema, ok, errors, warnings, timestamp/tick/state age, game/logged-in state, player position, inventory summary, an allowlisted activity summary when already present, liveness summary, and source metadata. Existing imports through `context_service.py` remain compatible for tests and callers.

Unsupported compact-context request values are never echoed verbatim. Unsupported `needs`, `task`, `profile`, `responseMode`, invalid schema, invalid JSON, and unknown request fields are reported with safe codes such as `unsupported_need`, `unsupported_task`, `unsupported_profile`, `unsupported_response_mode`, `invalid_schema`, `invalid_json_request`, and `unsupported_request_field_count`.

Current known limitations:

- It does not choose tasks, routes, targets, or banking behavior.
- It does not prove loaded-scene readiness.
- It does not emit action, click, mouse, keyboard, menu, input, command, movement, interact, interaction, target, execute, anti-detection, or gameplay command fields.
- It does not start RuneLite, the daemon, the snapshot endpoint, or any external service.

## Canonical R3 diagnostic boundary

- Path: `telemetry-viewer\recovery_diagnostics.py`
- Test command: `python telemetry-viewer\tests\test_recovery_diagnostics.py`
- Input schema: `context_response.v1`
- Output schema: `recovery_diagnostic.v1`

The diagnostic boundary accepts an already-built compact context response and reports readiness only. It does not read live files, call subprocesses, start services, or call action-capable scripts.

Allowed output fields are `schema`, `ok`, `status`, `reasons`, `required_context`, `observed_context`, and `warnings`.

Current known limitations:

- It validates only the presence and shape of read-only facts needed for diagnostics.
- It does not choose tasks, routes, targets, banking behavior, or activity behavior.
- It does not emit action, click, mouse, keyboard, menu, input, command, movement, interact, interaction, target, execute, anti-detection, or gameplay command fields.
- It does not prove loaded-scene readiness.

## Canonical R4 read-only live-readiness fixtures

- Fixture path: `telemetry-viewer\tests\fixtures\r4_live_readiness`
- Test command: `python telemetry-viewer\tests\test_r4_live_readiness_fixtures.py`
- Input boundary: already-loaded fixture data and `context_response.v1`
- Output schema: `recovery_diagnostic.v1`

R4 validates missing state, malformed state, stale logged-in state, login-screen state, logged-in state without scene evidence, loaded-scene evidence, incomplete telemetry, and recursive response safety invariants.

Loaded-scene readiness in R4 means observation-readiness only: current player position plus loaded-scene/world-model-style evidence is present in deterministic fixture data. It does not permit gameplay input, route execution, banking, task selection, or activity automation.

Current known limitations:

- R4 does not read live RuneLite/dev-client state.
- R4 does not call `--latest-session` as proof.
- `gradlew run` remains a development launch only and is not proof of a loaded scene.
- R4 does not start RuneLite, the daemon, the snapshot endpoint, or any external service.

## Canonical R5 read-only integration triage

- Path: `docs\recovery\R5_INTEGRATION_TRIAGE.md`
- Scope: documentation-only planning and inventory
- Input: read-only inspection results from `C:\Users\badto\osrs-telemetry`
- Gate: unchanged blessed command above

R5 documents how to evaluate old dirty-checkout changes against the recovered R1/R2/R3/R4 boundary. It classifies old docs, fixtures, diagnostics, tests, generated knowledge, and action-capable code by salvage risk.

Current known limitations:

- R5 does not modify runtime/source code.
- R5 does not modify tests.
- R5 does not import, copy, merge, or execute old code.
- R5 does not bless old entrypoints.
- R5 does not restore task, route, banking, activity, or action behavior.

## Current known entrypoints

- `src\main\java\com\osrstelemetry\TelemetryPlugin.java` - RuneLite plugin entrypoint.
- `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java` - local read-only snapshot endpoint.
- `src\main\java\com\osrstelemetry\WorldModelCache.java` - loaded-scene/world-model cache.
- `telemetry-viewer\live_core_daemon.py` - read-only live daemon on `127.0.0.1:8890`.
- `telemetry-viewer\context_service.py` - query/context service and recovery-capable CLI.
- `telemetry-viewer\state_baseline.py` - R1 read-only state baseline payload boundary; not a standalone runner.
- `telemetry-viewer\context_boundary.py` - R2 compact context request/response boundary; not a standalone runner.
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
- `telemetry-viewer\recovery_diagnostics.py` - R3 in-memory diagnostic boundary; not a standalone runner.
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
- `python telemetry-viewer\tests\test_recovery_diagnostics.py`
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
- Blindly merging dirty-checkout action proposal, executor, route demonstration, route guide/template, generated knowledge, or execution-test changes.
