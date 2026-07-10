# Engine Stabilization Plan

## Mission

Turn the proven Lumbridge west ordinary-tree -> Lumbridge Castle bank -> return
slice into a small OSRS-specific engine. Preserve one sensor truth, one task
FSM, one safety gate, one Arduino action path, typed verification, bounded
cleanup, and a thin runtime. Do not grow a generic planner, task language,
behavior tree, knowledge system, learned policy, anti-detection system, or GUI
runtime logic.

## Decisions

- **D001 — Evidence classification:** the 2026-07-10 physical cycle is stitched
  component proof across bounded continuation runs, not one uninterrupted
  process. It is valid route/interaction evidence but must never be presented
  as a raw end-to-end Observation replay.
- **D002 — Regression artifact:** preserve the live evidence hashes and final
  semantic contract in `tests/fixtures/golden_lumbridge_cycle.json`. The golden
  replay is deterministic, sanitized, hardware-free, and exercises the real
  `WoodcutBankTask` plus `Verifier`.
- **D003 — Fail-closed route evidence:** absent or temporarily non-actionable
  projection evidence waits; an explicitly contradictory labeled projection
  blocks.
- **D004 — Milestone discipline:** one phase, one reviewed diff, one checkpoint
  commit. Do not push without explicit user direction.

## Phases and acceptance

| Phase | Status | Acceptance |
|---|---|---|
| 0. Freeze proven baseline | Complete | Full diff audited; Python/Java suites pass; terminal trace reaches `COMPLETE`; tracked golden replay passes; stale proof docs corrected; checkpoint committed/tagged. |
| 1. Governing contract | Next | Product vision, architecture, rules, status, and this plan describe the OSRS-specific engine and prohibited expansion. |
| 2. Coherent sensor truth | Pending | Atomic tick `SensorFrame`; source-based freshness; mixed/stale/missing/menu/schema tests; bounded live observe. |
| 3. Minimal task seam | Pending | Runtime has no concrete woodcut imports/phase checks; typed outcomes; fake task runs unchanged runtime/safety/verifier. |
| 4. One task/site definition | Pending | One immutable Lumbridge definition and one validated default profile; unsafe/malformed values fail closed; replay unchanged. |
| 5. Arduino boundary | Pending | One `InputCoordinator`; no production bypass; bounded pointer policy; command/ACK plus authoritative final firmware status. |
| 6. EngineFrame + overlay | Pending | One immutable diagnostic truth; passive click-through overlay mirrors it and has no control authority. |
| 7. Demonstration capture | Pending | Read-only record/inspect commands produce hashed JSONL, manifest, timeline, screenshots, and reviewed semantic suggestions. |
| 8. Frontend contracts | Pending | Minimal facade proves list/validate/start/pause/stop/status/demo operations without duplicating task or safety logic. |
| Final regression | Pending | Full suites/replay pass; bounded fresh live observation and safe default-cycle evidence; cleanup and audits confirmed. |

## Phase 0 completed work

- Audited branch `rescue/m1-sensor-spine` at parent `a843915` and the complete
  13-file live-hardening diff.
- Fresh validation: 114 Python tests and 22 Java tests passed before the replay
  was added; the final Phase 0 validation includes the new replay tests.
- Classified the 51 ignored vertical-slice traces (48 blocked, 2 error,
  1 terminal complete) and preserved the key evidence hashes without committing
  machine/session-specific logs.
- Kept transient unavailable route geometry as a wait but restored terminal
  blocking for contradictory labeled projection identity.
- Covered the verified Escape bank-close fallback when the visible close widget
  has no usable point.
- Added `run.cmd replay` and the full semantic golden cycle.
- Final Phase 0 validation: 116 Python tests passed; a forced fresh Java run
  executed 22 tests with zero failures, errors, or skips; `git diff --check`
  passed.

## Remaining limitations

- The live corpus is stitched and lacks complete raw observations, command/ACK
  receipts, and immutable source provenance.
- Source freshness is still based partly on assembled response time; Phase 2 is
  the next safety-critical implementation milestone after the governing docs.
- Arduino callers and final firmware status proof are not yet centralized;
  Phase 5 owns that migration.
- No EngineFrame, overlay, demonstration recorder, or frontend facade exists.
