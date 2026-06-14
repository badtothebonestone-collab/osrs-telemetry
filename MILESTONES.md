# Milestones

Active milestone: R4 - Read-only live readiness fixtures

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

Status:
Complete in the recovery baseline. R2.5 is a cleanup pass to reduce mixed-responsibility risk without schema or behavior changes.

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

## R2.5 - Context boundary hardening after R3

Status:
Complete in commit `632ad0f`.

Goal:
Separate the pure/read-only R1 and R2 payload boundaries from broad CLI/server glue without changing behavior.

Out of scope:
- feature work
- task behavior
- route behavior
- banking behavior
- activity automation
- action execution
- anti-detection

Acceptance:
- no schema changes
- no duplicate parser or runner
- compatibility imports remain for existing tests
- R1, R2, R2 verifier, and R3 tests pass
- blessed command passes
- remaining mixed-responsibility risk is documented

## R3 - No-action diagnostic scaffold

Status:
Complete in commit `548c179`. It remains in the deterministic safety gate during R2.5.

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

## R4 - Read-only live readiness fixtures

Status:
Active fixture validation pass.

Goal:
Prove that the R1/R2/R3 boundary handles live-like telemetry safely with deterministic, read-only fixtures.

Out of scope:
- task selection
- route behavior or route execution
- banking behavior
- activity automation
- action execution
- anti-detection
- live RuneLite/dev-client state as a test dependency
- treating `--latest-session` or `gradlew run` as loaded-scene proof

Acceptance:
- R4 fixtures cover missing state, malformed state, stale logged-in state, login-screen state, logged-in state without scene evidence, loaded-scene evidence, and incomplete telemetry
- loaded-scene readiness is observation-readiness only
- `context_response.v1` has no forbidden fields recursively
- `recovery_diagnostic.v1` has no forbidden fields recursively
- no fixture name implies execution permission
- R1, R2, R2 verifier, R3, R2.5, and R4 deterministic tests pass
- blessed command passes without calling `--latest-session` for proof
