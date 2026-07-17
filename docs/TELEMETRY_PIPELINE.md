# Telemetry, Observation, and Decision Pipeline

## Status and scope

The bounded telemetry/observation/decision pipeline is implemented and has a
deterministic acceptance gate. A current-build loaded-scene timing distribution
is still a live-validation gap; retained live artifacts provide the before
baseline only.

This milestone changes internal observation and target-selection behavior. It
does not add a second telemetry authority or an input path. Production
activation remains:

```text
Task decision -> SafetyGate -> InputCoordinator -> Arduino
```

Exact PID/HWND, focus, geometry, freshness, coherence, physical-input, typed
verification, and cleanup rules remain authoritative. No timing or cache metric
can authorize input.

## Confirmed baseline problems

The retained dense sensor-spine samples proved that the previous canonical
radius-32 request scanned all 104 x 104 scene slots because it supplied no
center. Every request scanned 10,816 tiles, discovered and definition-enriched
6,743-7,728 objects, projected up to 192, and returned 64. At the smallest
retained count, at least 6,528 of 6,592 definition enrichments (99.0%) could not
be returned.

The old cache also expired after 350 ms even when tick and geometry were
unchanged. A timed-out `clientThread.invoke` remained queued. The endpoint used
an unbounded executor and encoded a normal response three times. Python
discarded census cap/completeness fields, merged duplicate rows field by field,
performed linear stable-key lookup, and allowed task code to rescan candidate
lists. A current EngineFrame trace recorded 77 fetches over 16 source ticks
(4.8 fetches per tick) but could not distinguish cache hits from refreshes.

Baseline evidence is retained under:

- `_run_proofs/sensor_spine/20260710_111428/run_2/`;
- `_run_proofs/final_regression/20260711_110232_inventory_normalized_live/`;
- `demo_runs/20260712T170027843742Z_final-manual-walk-000a886-final/`; and
- `_run_proofs/camera_controller_live/20260715T051415.0193041Z/`.

## Chosen architecture

```text
typed phase query plan
  -> bounded HTTP admission
  -> one-active/one-newest-pending client-thread scheduler
  -> exact-source/request-shape raw cache
  -> player or explicit-anchor tile window
  -> definition-free stable-identity census
  -> deterministic exact-key / ID / distance ranking
  -> bounded whole-row enrichment
  -> bounded returned-row projection
  -> two-pass exact-size response encoding
  -> bounded host read and strict parse
  -> immutable SceneIndex + typed census/pipeline evidence
  -> target continuity and bounded candidate evidence
  -> SafetyGate + existing guarded activation path
```

`WorldModelCache` keeps at most four raw snapshots, 256 enriched rows per
snapshot, and 128 projections per snapshot. Its cache identity includes source
tick, session, process, geometry frame, live plane, scene base, dirty sequence,
anchor, radius, and requested raw capabilities. Priority keys, priority IDs,
and projection budgets do not force a raw rescan.

The canonical missing-center request is player anchored. An explicit malformed,
wrong-plane, clipped, or outside-scene center produces incomplete coverage and
cannot prove absence. Collision hashing, actor capture, inventory capture, and
ground-item definition work run only for query shapes that request them.

Object discovery records only immutable identity and location facts. Definition
name/actions and projection are added only after the bounded return set is
chosen. Exact duplicate rows are counted. A stable-key identity conflict is
quarantined as a whole; fields are never borrowed across rows.

## Query and evidence contracts

`ObservationRequest` adds these bounded task-neutral fields:

- `priority_object_keys` (at most 32 exact stable keys);
- `center_world_location`;
- `radius_tiles` (1-96);
- `max_objects` (0-64 at the host boundary);
- `max_projection_objects` (0 through `max_objects`); and
- `purpose`, a diagnostic lowercase identifier.

They map to `worldModel.priorityObjectKeys`, `centerWorldLocation`,
`radiusTiles`, `maxObjects`, `maxProjectionObjects`, and `purpose` in
`plugin_snapshot_request.v1`.

`scene_object_census.v1` remains readable and gains additive fields for anchor,
radius, requested/scanned/missing tiles, discovered/duplicate/conflicting/
indexed/enriched/projected/returned objects, exact priority-key coverage,
response and source caps, raw scene coverage, and absence eligibility.

The important meanings are separate:

- `censusComplete`: the requested raw anchored region was scanned completely
  without hitting the internal census cap. It may be true when the returned
  row list is capped.
- `authoritativeAbsenceEligible`: an arbitrary missing row can be treated as
  absent. This is false when the returned row list is capped or a conflict was
  quarantined.
- `priorityAbsenceEligible`: a requested exact priority key can be treated as
  absent even when the ordinary returned list is capped, provided the raw
  census is complete and that exact identity did not conflict.

`world_model_pipeline.v1` carries exact source/cache identity, hit/miss and
refresh reason, bounded cache totals, query/refresh durations, and operation
counts. `client_thread_query_diagnostics.v1` carries request status,
coalescing/work-executed state, active/pending/max depth, queue/execution timing,
and submitted/executed/coalesced/superseded/timed-out/expired/late/failed
counters. Endpoint queue and two-pass serialization evidence are also retained.

Python preserves these as frozen `SceneCensusEvidence` and
`ObservationPipelineEvidence`. `EngineFrame.observation.sceneCensus` and
`EngineFrame.observation.observationPipeline` serialize them additively. Legacy
artifacts without the new completeness fields remain readable with UNKNOWN
completeness; UNKNOWN is never upgraded into negative proof or object-activation
authority. A legacy client that cannot carry an explicit anchor, exact key, or
budget likewise has completeness and absence authority revoked for that fetch.

## Target behavior

The task now requests phase-specific evidence:

- resource discovery: explicit work-area anchor, radius 16, at most 64 rows and
  32 projections;
- locked resource/bank/route-object verification: exact key and ID, target
  anchor, radius 4, at most 16 rows and 8 projections;
- ordinary route lookahead: step anchor, radius 16, at most 24 rows and 16
  projections; and
- UI-only phases: zero object/projection return budget.

Selection is stable across input permutations. A prebuilt immutable `SceneIndex`
provides constant-time exact-key and exact-ID lookup. Candidate identity and
screen-occlusion indexes are built once. Eligible candidates are bounded at 64
and rejection evidence at 32.

An exact target lock survives two incomplete or UNKNOWN omission frames. The
third blocks terminally while retaining the lock; it does not switch targets or
declare depletion. Only arbitrary authoritative absence or exact-priority
absence unlocks. A complete raw census alone is not negative proof. Exact-key
identity contradiction blocks. An unrelated quarantined duplicate remains
isolated.

Object activation is denied both in the task and in `SafetyGate` unless census
metadata explicitly proves complete raw scene coverage, and it is also denied
when the exact target identity conflicts. A present, consistent target may
proceed from a raw-complete response even when unrelated rows were capped; the
normal fresh exact-target validation still runs before activation.

## Measured deterministic deltas

| Measure | Before | After | Change |
|---|---:|---:|---:|
| Dense scene slots scanned, radius 32 | 10,816 | 4,225 | 60.9% fewer |
| Task discovery slots, radius 16 | 10,816 | 1,089 | 89.9% fewer |
| Locked-target slots, radius 4 | 10,816 | 81 | 99.3% fewer |
| Dense definition/action enrichment | 6,743-7,728 | at most 64 discovery / 16 locked | at least 99.1% / 99.8% fewer |
| Dense projections | 112 observed, 192 budgeted | at most 32 discovery / 8 locked | at least 71.4% / 92.9% fewer than observed |
| Same-source raw refresh | wall-clock repeat possible | exact-source cache hit | no redundant scan |
| Client-thread pending work | unbounded | active 1 + pending 1 | hard depth 2 |
| Endpoint pending queue | unbounded | capacity 8; one active snapshot | bounded + 503 backpressure |
| Normal JSON encoding | 3 passes | exactly 2 | 33.3% fewer |
| GUI EngineFrame conversion per render | 5 | 1 | 80% fewer |
| 1,001-row target identity evaluations | 1,001 | 33 | 96.7% fewer |
| Target rejection records | 1,000 | 32 | bounded |

The forced-fresh 30-sample synthetic 4,225-object Java refresh benchmark
measured p50/p95/maximum of 7.520/12.327/12.419 ms. One hundred exact-source
hits measured 0.715/1.527/4.815 ms. Each refresh scanned/discovered 4,225,
enriched/projected/returned 64. Synthetic response bytes measured p50/p95/
maximum of 107,896/107,900/107,902. This is a deterministic same-machine
benchmark, not a claim about live RuneLite latency.

The 1,001-row target benchmark improved p50/p95/maximum from
0.4388/0.6717/2.2055 ms to 0.0669/0.1557/0.4046 ms. The 64-row parse benchmark
improved from 0.973/1.955/5.386 ms to 0.791/1.059/2.202 ms. Immutable exact-key
lookup improved from 1.031 microseconds for the prior linear lookup to 0.077
microseconds (13.4x). A structurally oversized 1,000-row payload that previously
was accepted and parsed in 10.611/15.232/18.105 ms is now rejected before row
processing in 0.049/0.104/0.213 ms.

Retained live before timing was 49.236/93.775/93.775 ms service and
22/62/62 ms refresh for 12 dense samples. No current-build live after sample is
claimed, so those live values are not compared directly with the synthetic
after benchmark.

## Validation

- `run.cmd test`: **PASS**, 973/973 Python tests plus the normal Java gate;
- forced fresh Java `--rerun-tasks`: **PASS**, 124/124 tests across 12 suites,
  with all four Gradle tasks executed;
- retained golden-cycle and camera replay: **PASS**, 7/7;
- Python compile, input-boundary, and retained Java snapshot-fixture checks:
  **PASS**; and
- current-build loaded-scene live timing: **NOT AVAILABLE** because neither the
  `8890` nor `8893` local listener was running; the bounded `observe` attempt
  failed with connection refused and sent no input.

## Remaining limits

- Current-build RuneLite service, queue, cache-hit, payload, and target-churn
  p50/p95/maximum still need a suitable loaded-scene bounded live run.
- Explicit world anchors in instanced regions fail closed if they cannot map to
  the loaded scene; instance-aware anchor translation is future work.
- Endpoint overlap intentionally returns retryable `503 endpoint_busy`; the
  runtime waits for its next bounded poll rather than accumulating work.
- Raw census hard cap remains 10,000 identities. A hit is explicit incomplete
  evidence and cannot authorize absence or activation.
- The response format remains additive v1 for compatibility. A future breaking
  cleanup can publish v2 after retained consumers migrate.

Firmware source and transport ownership are outside this milestone. Firmware
was not flashed.
