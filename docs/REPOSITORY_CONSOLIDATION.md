# Repository Consolidation

## Authority and provenance

This document describes the repository-consolidation branch assembled on
2026-07-17. It reconstructs the complete accumulated rescue implementation from
checkpoint `5d8b636`, then applies the independently verified 27-file telemetry
production-soak delta. After merge, this branch is the authoritative repository
state.

The previously dirty recovery checkout and the isolated telemetry checkout were
not cleaned or overwritten. Their full status, patches, untracked-file hashes,
and valuable untracked source copies are retained in the external timestamped
repository-consolidation proof bundle. The older `osrs-telemetry` checkout is a
separate legacy line that diverged at `master`; it was preserved as evidence and
was not merged into the current engine.

Telemetry handoff integrity was independently confirmed:

- patch SHA-256: `ede59eea6e3fb471bb4286271c5f5a67cefc6d565f88679428d576c573177152`;
- overlay SHA-256: `4ee2f72554ff9174b4f4f3af73eca8e3f4f215dd3b0b6300023beae58e39469a`;
- patch, overlay, manifest, and isolated-tree path sets: the same 27 files;
- patch size: 1,804 insertions and 97 deletions; and
- no live RuneLite, Arduino action, firmware change, or credential interaction
  occurred during that telemetry milestone.

## Current authoritative architecture

```text
RuneLite game tick
  -> atomic SensorFrame
  -> bounded authenticated snapshot endpoint on 8893
  -> strict ObservationClient parsing and request/response binding
  -> immutable Observation plus SceneIndex
  -> EngineApplication composition root
  -> TaskRuntime and explicit WoodcutBankTask state machine
  -> target decision plus continuity evidence
  -> SafetyGate
  -> sole InputCoordinator
  -> private Arduino transport
  -> STOP_ALL / DISARM / STATUS cleanup
  -> fresh Observation and typed Verifier outcome
  -> immutable latest EngineFrame and passive subscribers
  -> optional overlay and GUI views with no control authority
```

`run.cmd task`, `run.cmd execute`, and the compatibility
`python -m osrs_bot task` surface all enter the same `EngineApplication`
composition root. Their JSON result is `engine_application.v1`; task, safety,
input, cleanup, and verification remain below that facade. `run.cmd observe` is
a read-only adapter diagnostic, not a second runtime. The soak command is
synthetic diagnostic tooling and adds no control path.

There is one production telemetry endpoint (`8893`), one Observation adapter,
one task/site definition, one SafetyGate, and one Arduino-only InputCoordinator.
No software mouse or keyboard fallback exists.

## Readiness and evidence boundary

| Area | Deterministic or synthetic evidence | Live or hardware evidence | Current state |
|---|---|---|---|
| Telemetry and observation | Full Python/Java regression, strict-schema fixtures, replay, adversarial selection, and bounded soak | No current-build loaded-scene timing; endpoint `8893` was absent during consolidation | Production code integrated; live timing gap remains |
| Task and runtime | Golden full-cycle replay and complete regression | Historical bounded live cycle exists; current broader route/Tree/cycle quality recheck is incomplete | Single supported task is coherent and fail-closed |
| Target continuity | Capped/incomplete-frame retention and terminal-storm tests | No new consolidation live run | Deterministic gate passes |
| Input boundary | Architecture scan and Arduino/InputCoordinator regressions | Retained bounded Arduino pointer proof includes acknowledged cleanup | Arduino-only production path; no consolidation input sent |
| Camera firmware | Source and deterministic protocol tests | Camera-input v2 source has not been flashed; prior camera proof has a disconnect gap | Not production-proven end to end |
| GUI | Facade/controller/presentation/EngineFrame regression | Base GUI has retained live proof; current production-action lifecycle recheck is not complete | Thin operator frontend, no input authority |

Fresh consolidation validation passed 984/984 Python tests, 127/127 forced-
fresh Java tests across 12 suites with all four Gradle tasks executed, 7/7
retained replay tests, 79/79 tracked Python compilation, the 5,000-sample plus
1/2/4/8-poller soak, 8/8 firmware protocol tests, and 201/201 focused input-
boundary/InputCoordinator tests. Python tests used a test-only permissive
`os.mkdir` wrapper because Python 3.12 mode-`0700` temporary-directory ACLs are
inaccessible to this restricted Windows sandbox token. No product code,
assertion, or test selection was changed by that harness.

Synthetic evidence demonstrates boundedness and determinism; it does not claim
RuneLite service latency or hardware behavior. Historical live artifacts remain
useful but do not turn this consolidation into a new live proof. Hardware claims
require acknowledged Arduino receipts and final safe cleanup, not merely a
successful software call.

## Launch, test, and diagnose

```powershell
.\run.cmd gui
.\run.cmd observe
.\run.cmd task
.\run.cmd execute COM6
.\run.cmd telemetry-soak
.\run.cmd replay
.\run.cmd test
```

Before any live gameplay, prove a fresh loaded scene and exact process/window
identity. Live activation remains opt-in and Arduino-only. After any live input,
retain `STOP_ALL`, `DISARM`, `STATUS`, zero-held-input, closed-ledger, and backend
cleanup evidence.

Use the following read-only surfaces when diagnosing a stalled run:

```powershell
.\run.cmd app catalog
.\run.cmd app validate-profile
.\run.cmd app run
```

`docs/TELEMETRY_PIPELINE.md`, `docs/ENGINE_FRAME.md`, and
`docs/INPUT_COORDINATOR.md` define the detailed contracts. `docs/ENGINE_STATUS.md`
separates retained historical proof from current gaps, and `PLANS.md` carries the
remaining phase acceptance work.

## Known weaknesses and next milestone

- Obtain a bounded current-build loaded-scene distribution for endpoint, queue,
  cache-hit, payload, and target-churn timing without sending gameplay input.
- Complete the broader movement/Tree/bank-cycle quality gate at the retained
  layout; the pointer submilestone alone does not close that phase.
- Flash and validate camera-input firmware v2 only under a separately approved
  hardware milestone, then prove acquisition and interaction in one run.
- Recheck the GUI production-action lifecycle with exact identity binding and
  acknowledged cleanup.
- Keep the legacy checkout read-only unless a specific missing behavior is
  demonstrated; do not merge it wholesale into this engine.

The best next milestone is a bounded, query-first current-build live readiness
run: prove endpoint `8893`, loaded-scene coherence, and diagnostic timing first;
then, only if those gates pass, execute one Arduino-only cycle with complete
cleanup evidence.
