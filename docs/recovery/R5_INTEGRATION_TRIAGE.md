# R5 Integration Triage

## Purpose

R5 is a read-only plan for evaluating old dirty checkout changes against the recovered R1/R2/R3/R4 boundaries.

It is planning and inventory only. It does not import old code, merge old code, restore old behavior, or bless any old entrypoint. The goal is to classify what might be useful later while keeping the recovered boundary intact.

## Current safe baseline

- R1 state baseline: `telemetry-viewer\state_baseline.py` builds `recovery_state_baseline.v1` from already available telemetry/state and handles missing, malformed, and stale state without repair or client control.
- R2 compact context boundary: `telemetry-viewer\context_boundary.py` builds `context_response.v1` with allowlisted read-only facts and sanitized request warnings.
- R3 no-action diagnostic scaffold: `telemetry-viewer\recovery_diagnostics.py` consumes already-built `context_response.v1` data and returns `recovery_diagnostic.v1` diagnostic facts only.
- R4 read-only live readiness fixtures: `telemetry-viewer\tests\fixtures\r4_live_readiness` and `telemetry-viewer\tests\test_r4_live_readiness_fixtures.py` prove live-like telemetry cases with deterministic fixtures only.
- Blessed command: `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`.

R5 does not add a new runner path. The blessed command remains the gate, and the R5 documentation branch keeps the existing deterministic R4 gate until a future deterministic documentation check is explicitly designed.

## Old checkout inspected

- Path: `C:\Users\badto\osrs-telemetry`
- Branch: `stabilization/live-loop-recovery-20260609`
- Status summary: dirty checkout with 16 modified tracked files and 1 untracked recording-analysis document.

Changed file groups:

- Docs/knowledge:
  - `docs\knowledge\NEXT_TASKS.md`
  - `docs\knowledge\OPEN_GAPS.md`
  - `docs\live_loop_execution_fix_report.md`
- Route guide/template data:
  - `route_guides\woodcutting_area_to_bank.route_guide.json`
  - `route_templates\woodcutting_area_to_bank.route_template.json`
- Input control / executor:
  - `telemetry-viewer\input_control\action_proposal.py`
  - `telemetry-viewer\input_control\executor.py`
- Capability registry / knowledge base:
  - `telemetry-viewer\knowledge_base\capability_registry.json`
  - `telemetry-viewer\knowledge_base\open_gaps.json`
  - `telemetry-viewer\knowledge_base\project_knowledge.json`
  - `telemetry-viewer\knowledge_base\recordings_index.json`
  - `telemetry-viewer\knowledge_base\script_api_map.json`
- Route demonstration:
  - `telemetry-viewer\route_demonstration.py`
- Tests:
  - `telemetry-viewer\tests\test_action_proposal.py`
  - `telemetry-viewer\tests\test_input_control_executor.py`
  - `telemetry-viewer\tests\test_route_demonstration.py`
- Recording analysis:
  - `docs\recording_analysis_bank_route_after_3208_3212.md` (untracked)

## Do not blindly merge

High-risk areas:

- Action proposal changes that alter selection, readiness, or proposed behavior.
- Input executor changes that attach live geometry or convert readiness evidence into executable screen coordinates.
- Route demonstration changes that rebuild guide progress, route evidence, or route blockers.
- Route guides/templates that turn old recordings into active guide behavior.
- Knowledge base changes that contain generated local evidence, absolute paths, or stale task conclusions.
- Tests that validate action execution, executor behavior, route execution, or live route progression.

These areas must stay quarantined until a later milestone explicitly permits a controlled design review. They should not enter R1/R2/R3/R4/R5 paths by import, copy, or incidental test dependency.

## Candidate salvage categories

- Safe docs/reference candidates:
  - Human-readable notes that describe old evidence, blockers, and unresolved questions without changing active instructions.
  - Recording-analysis prose after removing or clearly labeling local-only provenance.
- Fixture/data candidates:
  - Small deterministic snippets that can be reduced to read-only facts and moved under a fixture path with no execution permission implied.
  - Route or telemetry examples only if they are renamed and scoped as observation/reference data, not behavior.
- Read-only diagnostics candidates:
  - Pure fact extraction or validation ideas that accept already-loaded data and return only diagnostic facts.
  - Any such candidate must preserve the R3 allowed output field contract.
- Tests that can be converted to no-action tests:
  - Tests that assert quarantine behavior, forbidden fields, sanitized warnings, or missing-fact reporting.
  - Tests must be deterministic and fixture-only unless a later milestone explicitly marks them optional diagnostics.
- Action-capable code that must stay quarantined:
  - Executor changes, proposal-selection changes, route progression behavior, and any code that can influence live client behavior.
- Obsolete/unknown:
  - Generated knowledge base updates with local paths, stale tasks, or unclear provenance.
  - Old behavior tests that encode the dirty checkout's assumptions without a recovered-boundary design.

## Integration rules

- Every imported behavior must pass through the R1/R2/R3/R4 boundaries.
- No old entrypoint becomes blessed automatically.
- No action-capable module can be imported by R1/R2/R3/R4/R5 paths.
- No route/action/executor behavior is restored without a new explicit milestone.
- All future tests must be deterministic unless explicitly marked optional diagnostic.
- The blessed runner remains the gate.

R5 output is documentation only. It must not make live state required, use `--latest-session` as proof, or treat `gradlew run` as loaded-scene proof.

## Proposed next milestones

- R6: Convert safe old docs/fixtures into a reference-only archive.
- R7: Convert old action-execution tests into read-only diagnostic tests where possible.
- R8: Decide whether any action-capable code remains out of scope or needs a separate controlled design review.
