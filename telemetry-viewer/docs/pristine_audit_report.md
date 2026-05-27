# Pristine System Audit Report

Generated: 2026-05-27T08:22:01-05:00

## Scope

This audit checked the current query-first OSRS telemetry pipeline after the
live packet archive removal, plugin config simplification, Knowledge Fabric
work, external knowledge integration, bootstrap/login FSM work, Arduino serial
hardening, and target-view/camera recovery changes.

No long live gameplay was run. RuneLite/8893 was not available during this
audit, so live-scene validation is reported as a liveness limitation, not a
pipeline failure.

## Repository And Process Baseline

- Branch: `master`.
- Worktree: dirty before this audit began, with many modified files, removed
  legacy packet classes/tests, and many untracked new pipeline files. Nothing
  was reverted.
- Python: 3.12.10.
- Java: OpenJDK 21.0.11.
- Gradle: available and passing.
- Active processes observed:
  - `live_core_daemon.py` on 8890, session
    `C:\Users\badto\.osrs-telemetry\sessions\2026-05-27_07-14-13`.
  - Arduino monitor process for COM6.
  - Gradle daemon.
- 8893 PluginSnapshotEndpoint: not listening / timed out during audit.
- COM/Arduino: COM6, Arduino Leonardo, protocol `arduino_hid.v1`.
- VM CPU note: the audit and test suite completed on 2 cores without observed
  instability. Full suite time was about 81 seconds.

## Pipeline Health

`python telemetry-viewer\context_service.py --pipeline-health`

- Result: `WARN`.
- Active components reported:
  - RuneLite plugin
  - PluginSnapshotEndpoint 8893
  - WorldModelCache
  - live daemon/context service 8890
  - Knowledge Fabric
  - MCP/local adapter
  - current_debug_context
  - explicit bundles
  - external knowledge cache
  - Arduino/HumanInputController
- 8890 daemon: `ok`, latest tick 1460, snapshot-no-files mode, overlay state
  written, no compact packet files required or written.
- 8893 snapshot health: `FAIL`, timed out at `http://127.0.0.1:8893/health`.
- Recommended live next step: recover/launch RuneLite so 8893 is healthy before
  any live validation.

## Plugin Config Audit

Normal visible config keys are limited to current useful settings:

- `enabled`
- `outputDirectory`
- `enablePluginSnapshotEndpoint`
- `pluginSnapshotHost`
- `pluginSnapshotPort`
- `pluginSnapshotAuthToken`
- `pluginSnapshotAllowNonLocalHost`
- `telemetryDebugOverlayEnabled`
- `telemetryDebugOverlayMode`
- `telemetryDebugOverlayMaxTargets`
- `telemetryDebugOverlayShowLabels`
- `telemetryDebugOverlayShowAimPoints`
- `telemetryDebugOverlayGeometryMode`
- `telemetryDebugOverlayShowClickableHull`
- `telemetryDebugOverlayShowBounds`

No NDJSON, JSONL, live packet archive, workflow preset, raw recording, frame
capture, or compact stream/file archive setting is exposed in the normal UI.

Developer diagnostic keys remain hidden. Retired keys are listed in
`telemetry-viewer/config_keys.json` and in pipeline health output for startup
migration. Migration is scoped to the `osrs-telemetry` config group, preserves
current snapshot host/port/auth/output/overlay keys, migrates
`pluginSnapshotEnabledInNormalLive=true` to `enablePluginSnapshotEndpoint=true`,
and unsets retired keys idempotently.

Live plugin settings were not visually inspected in RuneLite because 8893/RuneLite
was down during this audit. Source/registry/tests validate the config surface.

## Live Packet Archive Guard

`python telemetry-viewer\maintenance.py --live-packets-report`

- `livePacketsRuntimeRemoved=true`
- `ndjsonRuntimeRemoved=true`
- `jsonlRuntimeRemoved=true`
- `livePacketWriterActive=false`
- Legacy live packet files present: false.
- Legacy live packet file count: 0.
- Legacy live packet total: 0 MB.
- Telemetry root size: 374.924 MB.
- Cleanup recommended: false.

Repository/docs search found only legacy cleanup/report documentation and
explicit bounded JSONL diagnostics. Stale documentation phrasing that described
retired compact navigation/collision packets as normal runtime output was
updated during this audit.

## MCP And Direct Queries

Direct MCP adapter checks passed:

- `python telemetry-viewer\mcp_server.py --list-tools`
- `python telemetry-viewer\mcp_server.py --list-resources`
- `python telemetry-viewer\mcp_server.py --call-tool get_pipeline_health`
- `python telemetry-viewer\mcp_server.py --call-tool get_current_debug_context`
- `python telemetry-viewer\mcp_server.py --call-tool list_available_profiles`
- `python telemetry-viewer\mcp_server.py --call-tool external_knowledge_status`

No click/input execution tools were exposed through MCP. The adapter remains a
read-only inspection surface.

One usability caveat remains: the direct `context_service.py --query
current-debug-context` wrapper can exit nonzero when 8893/live files are absent,
while the MCP tool path returns a structured current debug context with
`bootstrapState=plugin_endpoint_down`. This should be normalized later so the
first-query workflow is equally smooth in no-live-scene states.

## External Knowledge Cache

`external_knowledge_status` passed:

- Cache path: `C:\Users\badto\.osrs-telemetry\external_knowledge_cache`.
- Cache size: 0.043 MB.
- Max cache size: 500 MB.
- Cache-first: true.
- Explicit refresh only: true.
- External API enabled by default: false.
- Hot runtime external API calls: false.
- User-Agent required: true.
- Sources healthy: true.

Spot checks:

- Item ID 1511 -> Logs.
- Search `logs` -> Logs, Oak logs, Willow logs.
- Oak -> Woodcutting level 15.
- Staircase -> Climb-up / Climb-down.
- Lumbridge Castle bank -> advisory coordinate 3208,3220,2.

External facts remain advisory and do not override live RuneLite evidence.

## Replay, Script Authoring, And Debug Bundles

Latest replay scenario:

`C:\Users\badto\.osrs-telemetry\sessions\2026-05-27_07-14-13\interaction_geometry\live\replay_scenarios\20260527_121755_409138_target_view_service_offscreen_live_pre_validation\scenario.json`

Replay result: `PASS`, no live input. It reproduced the upstairs service target
state and proposed `service_view_recovery`, not repeated `open_service` skips.
Key evidence included:

- Service targets loaded on plane 2.
- Deposit/bank objects offscreen or without usable projection.
- `service_target_exposure.v1` showed unusable exposure.
- `target_view_state.v1` was present.
- Target bearing and yaw error were computed.
- Camera motor plan used keyboard arrows with a bounded hold.

Latest script-authoring context:

`C:\Users\badto\.osrs-telemetry\sessions\2026-05-27_07-14-13\interaction_geometry\live\script_authoring_context\20260527_121755_434879_woodcutting_bank_logs`

It contains capped sections for current context, blocker, world/model summaries,
resource/service/route/pathing/view data, external knowledge status, data
quality, coverage, static library excerpts, and route/profile excerpts.

Latest visual debug bundle evidence exists under:

`C:\Users\badto\.osrs-telemetry\sessions\2026-05-27_07-14-13\interaction_geometry\live\debug_bundles`

Recent bundle reasons include `service_view_recovery_start`,
`service_view_recovery_end`, and `final_summary`. No new visual bundle was
captured during this no-live audit.

## Bootstrap/Login FSM Audit

Static/source/test audit confirms:

- Saved-account Play Now, Click here to play, disconnected OK, credential
  required, stale LOGGED_IN/no-scene, and loaded scene states are represented.
- Loaded scene proof requires more than stale LOGGED_IN; it checks live/world
  evidence.
- Jagex Launcher automation is blocked by default.
- Credential screens stop as `manual_login_required`.
- Startup clicks route through HumanInputController and Arduino when executed.
- Coordinate bounds/DPI validation tests exist.

Live bootstrap was not executed because RuneLite/8893 was down and this pass was
not meant to launch long live workflows.

## Arduino And Input Integrity

Safe checks run:

- STOP_ALL: pass.
- STATUS: pass.
- IDENTIFY: pass, `arduino_hid.v1`.
- CAPS: pass.
- Latest STATUS: armed=false, keysDown=0, mouseButtonsDown=0.
- Input integrity file: WARN only because last Arduino event was stale; monitor
  present, VID/PID matched, injected/lower-IL counts 0, and
  directBackendBypassCount 0.

No movement/click was run during this audit.

## Target View And Camera Wiring

Generic target-view code is present in `telemetry-viewer/target_view_core.py`
and is used by service exposure/action proposal and view-quality reporting.

Verified behavior by replay/search/tests:

- `target_view_state.v1`
- `target_view_policy.v1`
- `service_target_exposure.v1`
- target bearing/yaw error computation
- strict service exposure thresholds
- edge slivers are not usable exposure
- service object offscreen/no click point triggers `service_view_recovery`
- recovery is non-click camera movement
- recovery success requires usable exposure, not a tiny edge projection
- debug bundles include target/service view recovery reasons

Wheel zoom is reported as unavailable when the Arduino protocol/backend lacks
wheel commands. Live camera motor output remains routed through
HumanInputController/Arduino.

## Route And Service Pipeline

Route/service wiring remains covered by replay and tests:

- `service_route_core.py` consumes snake_case and camelCase service candidate
  fields.
- Route-visible Staircase intercept remains covered by `test_service_route_core`.
- Service-object offscreen state now resolves to camera recovery before another
  click attempt.
- Route/pathing wall-hugging fix did not regress in the stabilization suite.

No live route/service smoke was run because 8893 was unavailable.

## Tests And Validation

Commands run:

- `python -m py_compile ...` for the requested core/query/executor/bootstrap
  files: PASS.
- `python telemetry-viewer\run_stabilization_suite.py`: PASS, 179/179.
- `.\gradlew.bat test`: PASS.
- `.\gradlew.bat build`: PASS.
- `git diff --check`: PASS after trimming two pre-existing EOF blank-line
  issues.
- `python telemetry-viewer\maintenance.py --live-packets-report`: PASS.
- MCP direct tools/resources and representative read-only tool calls: PASS.

## Files Changed By This Audit

- `docs/telemetry_schema.md`: corrected retired compact navigation/collision
  packet wording.
- `docs/clickable_hull_pipeline.md`: updated wording from compact-live packets
  to snapshot/world-model projection payloads.
- `telemetry-viewer/docs/cleanup_inventory.md`: clarified snapshot diagnostics
  and bounded debug JSON/JSONL wording.
- `src/main/java/com/osrstelemetry/TelemetryConfig.java`: removed trailing EOF
  blank line only.
- `telemetry-viewer/tests/test_run_daily_gauntlet.py`: removed trailing EOF
  blank line only.
- `telemetry-viewer/docs/pristine_audit_report.md`: added this report.

No files were deleted or moved during this audit.

## Remaining Risks And Next Work

1. Recover or launch RuneLite so 8893 is healthy, then run a tiny live smoke
   that verifies `loadedSceneVerified=true`.
2. Normalize `context_service.py --query current-debug-context` so it returns a
   useful structured context instead of a nonzero wrapper result when 8893 is
   down.
3. Consider a later deeper purge of historical offline compact/raw diagnostic
   branches in `live_target_processor.py` and related tests if they are proven
   unnecessary. They are not active runtime archive paths today.
4. Visually inspect the RuneLite plugin settings after launching the dev client
   to confirm the source/test-validated clean UI.
5. Review untracked/generated files before committing; the worktree is large and
   intentionally dirty from multiple strengthening passes.
