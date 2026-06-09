# Codex Instructions

Read this file first, then `PROJECT_STATE.md`, `CURRENT_GOAL.md`, and `docs/INDEX.md`.

## Mandatory Startup For Broad Telemetry/API/Bot Tasks

1. Read `docs\knowledge\PROJECT_STATE.md`.
2. Read `docs\knowledge\ENTRYPOINTS.md`.
3. Read `docs\knowledge\CAPABILITY_REGISTRY.md`.
4. Read `docs\knowledge\API_DATA_PATHS.md`.
5. Read `docs\knowledge\SCRIPT_API_MAP.md`.
6. Read `docs\knowledge\OPEN_GAPS.md`.
7. Reuse canonical modules. Do not create alternate recovery, Start Game, recorder, or analyzer paths.
8. After telemetry/API/analyzer/context changes, update knowledge docs and indexes.
9. Run focused tests/checks.

## Current Architecture

- Use the coordinate-first architecture. WorldPoint/world tile coordinates are canonical; canvas and screen pixels are only projections after live geometry proves the target.
- Treat the plugin endpoint, live daemon, Knowledge Fabric, and live readiness checks as the source of truth. Query first before changing behavior.
- Keep navigation, traversal, interaction, and banking as separate layers. Do not merge their decision logic.
- Take one meaningful live action, then wait for tick/state proof before taking another meaningful action.
- Every navigation decision should have a reason string. When tracing is enabled, emit a compact `navigation_decision_trace.v1` record.
- Use the existing repository recovery flow for live client/session recovery. Do not create new recovery, login, or session-startup systems.
- If loaded-scene recovery is needed, use `liveness_recovery_core.py` or `context_service.py --ensure-loaded-scene`.
- If Start Game is needed, use `start_game_command.py`.
- Keep the RuneLite bridge focused on read-only telemetry export.
- Keep the Python side focused on parsing, recording, schema/capability detection, context shaping, diagnostics, and QA.
- For new bridge/API fields, prefer: recorder -> analyzer -> schema gap report -> targeted bridge export -> sidecar context field -> optional MCP wrapper.
- Prefer compact, typed, versioned schemas. Raw recordings are for debug, audit, and training; they must not become the normal live context shape.

## Do Not Revive

- Do not revive old live packet archive, NDJSON/JSONL runtime writers, stale handoff plans, or abandoned scanner/filter architectures.
- Do not add global pathfinding.
- Do not add randomization, evasion, or anti-detection logic.
- Do not use `pyautogui` or `pydirectinput` for live input. Live input must stay on the HumanInputController -> ArduinoHIDBackend path unless the user explicitly authorizes a debug-only exception.

## Change Discipline

- Inspect `git status` before edits and do not overwrite existing user or navigation-tracing work.
- Keep fixes small, bounded, and behavior-focused.
- Add or update regression checks for behavior fixes.
- If code changes, run focused tests plus `python telemetry-viewer\run_stabilization_suite.py`.
- Always run relevant compile/check commands and report failures honestly.
- Always show changed files and a concise diff summary at the end.
- Do not replace live bot action tasks with dry-run unless the task explicitly asks for dry-run or a true external blocker prevents live execution.
- If a missing capability is found, classify the missing layer: plugin / recorder / analyzer / context / MCP / task_script_api / executor / tests / docs.

## Project Knowledge Base

- Before broad telemetry/API/script tasks, inspect `docs\knowledge\PROJECT_STATE.md`, `docs\knowledge\ENTRYPOINTS.md`, and `docs\knowledge\OPEN_GAPS.md`.
- After adding telemetry fields, analyzer outputs, context fields, MCP tools, or task-script fields, update `docs\knowledge\API_DATA_PATHS.md` and `docs\knowledge\CAPABILITY_REGISTRY.md`.
- After analyzing important recordings, update `docs\knowledge\RECORDING_INDEX.md` and `docs\knowledge\ACTIVITY_KNOWLEDGE.md`.
- Do not leave useful data recorder-only when scripts need it; promote it through analyzer, `context_service.py`, and `task_script_api.py`.
- Detailed current state belongs in `docs\knowledge`; keep this file concise.
