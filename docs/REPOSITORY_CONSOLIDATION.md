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
| Telemetry and observation | Full Python/Java regression, strict-schema fixtures, replay, adversarial selection, and bounded soak | Follow-up loaded-scene diagnostic: 500 planned requests, 469 ordinary observations, 31 exact typed handoffs, zero schema failures; no timing distribution | Production code and exact handoff lane live-integrated; distribution gap remains |
| Task and runtime | Golden full-cycle replay and complete regression | One follow-up camera action verified; broader route/Tree/cycle quality recheck remains incomplete | One gathering runtime is coherent and fail-closed; component proof only |
| Target continuity | Capped/incomplete-frame retention and terminal-storm tests | Exact world-only and requested-tile-plus-world handoffs observed; no ordinary target activation | Deterministic and handoff gates pass |
| Input boundary | Architecture scan and Arduino/InputCoordinator regressions | One `CAMERA_HOLD left 327` receipt has changed-pose verification and acknowledged zero-held cleanup | Arduino-only production path live-proven for one camera component |
| Camera firmware | Source and deterministic protocol tests | Already-installed v2.0.0 negotiated from hardware; one camera hold passed; no flash in the follow-up and wheel remains unproved | Hold capability component-proven, not end-to-end acquisition/interaction |
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

A later task-platform continuation supplies that bounded component receipt: one
camera hold was acknowledged and verified, all 13 wire commands passed, final
firmware status was disarmed with zero held input, the ledger/backend closed,
and COM6 was released. It does not retroactively create a live timing
distribution or prove the copper route, ordinary interaction, banking, wheel
polarity, or a complete gathering cycle.

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
- Do not reflash the already-installed camera-input v2 firmware without a
  separately approved hardware milestone. Prove wheel polarity only if a fresh
  task decision requires it, then prove acquisition and ordinary interaction in
  one bounded run.
- Correct the saved-session login pointer correction overshoot before repeating
  that production login step; operator-only post-auth setup is not gameplay
  proof.
- Recheck the GUI production-action lifecycle with exact identity binding and
  acknowledged cleanup.
- Keep the legacy checkout read-only unless a specific missing behavior is
  demonstrated; do not merge it wholesale into this engine.

The best next milestone is a bounded current-build route interaction run: retain
the now-proven endpoint/coherence gates, fix the login-pointer weakness first,
then permit one ordinary Arduino target activation and verification with the
same complete cleanup evidence. A full cycle remains a later, separately
bounded claim.
