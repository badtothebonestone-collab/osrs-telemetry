# Milestones

Active milestone: R3 - No-action diagnostic scaffold

## R1 - Read-only state baseline

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

## R2 - Compact context boundary

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

## R3 - No-action diagnostic scaffold

Goal:
Prepare a modular diagnostic scaffold that consumes compact context and reports read-only readiness only.

Out of scope:
- anti-detection
- bypass behavior
- direct game input
- task behavior
- route behavior
- banking behavior
- activity behavior

Acceptance:
- diagnostic module boundary exists
- `context_response.v1` is dependency-injected
- output contains diagnostic fields only
- forbidden field names are absent recursively
- tests cover diagnostic output shape only
