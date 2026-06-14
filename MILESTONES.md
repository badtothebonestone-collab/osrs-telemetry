# Milestones

Active milestone: R2 — Compact context boundary

## R1 — Read-only state baseline

Goal:
One command can verify that the project can read/parse/report state safely.

Out of scope:
- task behavior
- route behavior
- banking behavior
- action execution
- anti-detection

Acceptance:
- one blessed command exists
- Python files compile, if applicable
- tests run, if present
- missing/malformed/stale state is handled cleanly
- status output is clear

## R2 — Compact context boundary

Goal:
Expose compact context response from read-only state.

Out of scope:
- action execution
- anti-detection
- high-level task automation

Acceptance:
- request/response schema documented
- no action fields
- tests cover compact response shape

## R3 — Single simple task scaffold, no unsafe behavior

Goal:
Prepare a modular task scaffold that consumes context but does not perform direct input/action execution.

Out of scope:
- anti-detection
- bypass behavior
- direct game input

Acceptance:
- task module boundary exists
- state/context is dependency-injected
- tests cover decision output shape only
