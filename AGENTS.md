# Codex Instructions

Read this file first. Then read `PROJECT_STATE.md` and `MILESTONES.md`.

## Source Of Truth

- `PROJECT_STATE.md` describes current repo facts.
- `MILESTONES.md` describes current build order.
- `RECOVERY_LOG.md` records recovery history.
- Deprecated, historical, cleanup, handoff, and archive docs are reference only.
- If older docs conflict with the three files above, follow the three files above.

## Operating Rules

- Do not invent alternate run commands.
- Use only blessed commands listed in `PROJECT_STATE.md`.
- If a blessed command is missing, say it is missing; do not substitute another command.
- When blessed commands change, update `PROJECT_STATE.md` in the same change.
- Source changes must be tied to the current milestone in `MILESTONES.md`.
- Keep changes small and scoped to the active milestone.
- Inspect worktree status before edits and do not overwrite user work.
- Do not delete, move, reset, clean, or discard files unless the user explicitly asks.

## Recovery Safety

- During recovery milestones, do not implement features or refactor runtime/source code.
- During recovery milestones, do not add anti-detection, evasion, randomization, bypass, or stealth behavior.
- During recovery milestones, do not add or run click, mouse, keyboard, menu, banking, route, task, or gameplay action execution.
- Read-only telemetry/state parsing and documentation are allowed when tied to the current milestone.
- Execution-capable examples in old docs are not active instructions.

## Current Architecture Boundary

- The active recovery boundary is read-only state: parse, validate, summarize, and report.
- Runtime action layers are out of scope until a milestone explicitly allows them.
- `gradlew run` is a development launch only; it is not loaded-scene proof.
- Loaded-scene readiness must be proven by current telemetry/state, not by chat memory or stale files.
