# Codex Instructions

Read this file first, then `PROJECT_STATE.md`, `CURRENT_GOAL.md`, and `docs/INDEX.md`.

## Current Architecture

- Use the coordinate-first architecture. WorldPoint/world tile coordinates are canonical; canvas and screen pixels are only projections after live geometry proves the target.
- Treat the plugin endpoint, live daemon, Knowledge Fabric, and live readiness checks as the source of truth. Query first before changing behavior.
- Keep navigation, traversal, interaction, and banking as separate layers. Do not merge their decision logic.
- Take one meaningful live action, then wait for tick/state proof before taking another meaningful action.
- Every navigation decision should have a reason string. When tracing is enabled, emit a compact `navigation_decision_trace.v1` record.

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
