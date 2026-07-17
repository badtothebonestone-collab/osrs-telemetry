# Telemetry, Observation, and Decision Pipeline

## Status and scope

The bounded telemetry/observation/decision pipeline is implemented and has a
deterministic acceptance gate. A current-build loaded-scene timing distribution
is still a live-validation gap; retained live artifacts provide the before
baseline only.

The production-soak continuation closes additional pressure-path correctness
gaps without claiming a live after distribution. The raw census may retain at
most 10,000 identities, but a response may return and definition-enrich at most
64 rows. Projection budget is consumed per request before either a cached or a
new projection can enter that response, so a warm cache cannot bypass a smaller
`maxProjectionObjects` request. If an exact requested priority identity is
present in the filtered raw census but omitted by the response-row budget,
`priorityAbsenceEligible` is false; a cap omission is never false absence.

Planned Python fetches now verify that the response echoes the requested center,
anchor source, radius, purpose, and exact priority-key/ID set before granting
coverage or absence authority. A mismatch fails closed instead of allowing
cross-request evidence to certify a decision. Typed retryable
`503 endpoint_busy` responses enter the neutral `ENDPOINT_BACKPRESSURE` wait,
do not spend the observation-error budget, and permit up to eight busy-lane
events before the next accepted planned Observation. The ninth terminates,
subject to existing deadlines; interleaved provenance-handoff events do not
reset that independent count. Malformed endpoint JSON returns `400` without leaking
endpoint admission for the next request.

A separate admitted-request race can cross from the captured SensorFrame to a
newer request-time query tick or geometry frame. The endpoint preserves the
coherent core as HTTP 200 `WARN`, omits the provenance-rejected census, and
reports `world_model_provenance_mismatch`. Planned fetches accept no authority
from that response. The required world omission may co-occur only with the
exact interaction pair (`interaction_hot` missing, its provenance warning, and
`menuFresh=false`) and/or the exact requested-tile pair (`tile_projection`
missing plus `tile_projection_provenance_mismatch`, with both tile envelopes
absent). Otherwise interaction evidence must be mirrored and fresh, while
requested tile evidence must be complete, mirrored, schema-valid, and bound to
the exact requested labels and locations. Up to eight handoff-lane events may
occur before the next accepted planned Observation; the ninth terminates,
subject to the ordinary runtime/verification deadlines. The lane charges no observation or
additional action attempt, and a post-action retry continues the same pending
verification without re-execution. Diagnostic `fetch()` retains a non-loaded
`WARN`. Any partial census, silent requested-tile omission, extra warning or
capability, contradictory envelope, stale core, or malformed shape is terminal.

Live evidence recording is off the EngineFrame publication path. A bounded
256-frame queue feeds one daemon writer, records its high-water mark and drops,
caps retained recorder errors, and reports whether the writer stopped during
the bounded finish. This protects runtime publication from filesystem and JSON
serialization latency. Repeatable synthetic soak evidence is available through
`python -m osrs_bot.telemetry_soak` or `run.cmd telemetry-soak`; its stable
`telemetry_pipeline_soak.v1` output separates parse, publication, concurrency,
memory/thread, and endpoint-backpressure scenarios from live evidence.

This continuation is integrated on the repository-consolidation branch after
independent verification of the exact 27-file patch and overlay. The original
authoritative and isolated checkouts are preserved in the external repository
recovery record; no telemetry milestone remains stranded outside the branch.
The production endpoint on `8893` was not listening, so no production input was
sent and no current-build live after sample is claimed.

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

`WorldModelCache` keeps at most four raw snapshots, 256 cached enriched rows per
snapshot, and 128 cached projections per snapshot. A raw snapshot may census at
most 10,000 identities, while each response returns and definition-enriches at
most 64 rows. Its cache identity includes source tick, session, process,
geometry frame, live plane, scene base, dirty sequence, anchor, radius, and
requested raw capabilities. Priority keys, priority IDs, and projection budgets
do not force a raw rescan. Per-request enrichment and projection budgets are
still applied before cached values enter the response.

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
  absent even when the ordinary returned list is capped only when the raw
  census is complete, that exact identity did not conflict, and a present
  matching raw row was not itself omitted from the returned list by the cap.

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
For planned requests, the returned center, anchor source, radius, purpose, and
exact requested priority sets must also match the request. This response-shape
binding prevents a concurrent or stale response from lending authority to a
different plan.

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

The task publishes this continuity state as frozen
`TargetContinuityEvidence`. `EngineFrame.task.targetContinuity` contains
`lockedTargetKey`, `lockedTick`, `lastSeenTick`,
`incompleteOmissionFrames`, `retentionReason`, and `lastUnlockReason`. This
makes retention and every observed unlock explainable from the same task-owned
snapshot without exposing mutable lock state. Generic tasks that do not own a
target lock publish `null`.

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

### Production-soak closeout measurements

The final repeatable synthetic run used 5,000 serial samples and 1,000 parses at
each of 1/2/4/8 pollers. Ordinary warm parse p50/p95/p99/maximum was
0.1557/0.3018/0.4607/3.1052 ms; JSON decode plus parse was
0.1904/0.3544/0.5045/2.4453 ms; oversized rejection was
0.0502/0.1127/0.1711/0.4000 ms; and EngineFrame publication was
0.0030/0.0057/0.0063/0.1326 ms. The 1,001-row target classifier measured
0.0824/0.1741/0.2657/1.3814 ms and performed exactly 33 identity evaluations,
one ambiguity query, one ranked selection, and 32 retained rejections.

The process returned to one thread after every concurrency level. Each level
produced one result signature. RSS grew from 32,575,488 to 35,266,560 bytes over
the complete serial/target run; traced parse memory peaked at 14,573 bytes. At
eight pollers p50/p95/p99/maximum was 0.1754/0.3116/0.4279/61.6591 ms. The
maximum is retained as a real host scheduling tail. The bounded-backpressure
probe recovered after eight typed busy responses and terminated the ninth-
response storm with zero accepted observations.

The forced-fresh final Java dense benchmark measured refresh
p50/p95/p99/maximum 7.316/16.288/20.334/20.334 ms and exact-source reuse
0.896/3.310/7.579/9.263 ms. The response remained 107,896/107,900/107,902/
107,902 bytes and scanned/discovered 4,225 while enriching/projecting/returning
64. Against the same run's pre-change Java baseline, refresh p50 improved from
8.112 ms, p95 was effectively flat from 16.277 ms, and maximum regressed from
17.710 ms. Exact-hit p50/p95/maximum regressed from 0.846/1.800/8.468 ms. These
load/JIT-sensitive tail regressions are reported rather than attributed to the
reliability fixes.

## Validation

- complete Python regression: **PASS**, 984/984 with zero failures, errors, or
  skips, using the documented test-only Windows sandbox temporary-directory ACL
  harness;
- forced-fresh Java `--rerun-tasks`: **PASS**, 127/127 across 12 suites with all
  four Gradle tasks executed;
- retained golden-cycle and camera replay: **PASS**, 7/7;
- Python syntax compilation: **PASS**, 79/79 files;
- 5,000-sample serial, 1/2/4/8-poller concurrency, oversized, target-decision,
  EngineFrame-publication, memory/thread, and bounded-backpressure soak:
  **PASS**;
- focused cache-budget, malformed-request recovery, response-shape,
  target-continuity, GUI/EngineFrame, recorder, and Arduino activation-boundary
  gates: **PASS**; and
- current-build loaded-scene live timing: **NOT AVAILABLE** because the
  production `8893` endpoint was not running. No input or firmware change was
  attempted.

## Remaining limits

- Current-build RuneLite service, queue, cache-hit, payload, and target-churn
  p50/p95/p99/maximum still need a suitable loaded-scene bounded live run.
- Explicit world anchors in instanced regions fail closed if they cannot map to
  the loaded scene; instance-aware anchor translation is future work.
- Endpoint overlap intentionally returns retryable `503 endpoint_busy`; the
  runtime waits without spending its observation-error budget. It permits eight
  events in that independent lane before the next accepted planned Observation
  and stops on the ninth rather than accumulating work.
- Raw census hard cap remains 10,000 identities. A hit is explicit incomplete
  evidence and cannot authorize absence or activation.
- Response return and definition-enrichment work is hard-capped at 64 rows;
  cached projections remain reusable internally but cannot bypass a smaller
  per-request projection budget.
- The bounded live recorder can intentionally drop frames under sustained
  writer overload. Its receipt reports the queue high-water, drop count, and
  writer shutdown state rather than hiding that loss.
- The response format remains additive v1 for compatibility. A future breaking
  cleanup can publish v2 after retained consumers migrate.

Firmware source and transport ownership are outside this milestone. Firmware
was not flashed.
