# EngineFrame and Passive Overlay

## One diagnostic truth

`osrs_bot.engine_frame.EngineFrame` is the immutable read-only status contract
published by the real `TaskRuntime` path. `EngineFramePublisher` retains only
the latest frame under a monotonic sequence and supports passive readers. It
does not keep history, invoke callbacks, mutate the task, or authorize input.

Frames are published after real observation, decision, execution, and verifier
boundaries, plus terminal outcomes. A terminal frame retains the last real
execution receipt and typed verification outcome instead of losing them when
the FSM advances.

`engine_frame.v1` includes:

- task ID, state, status, definition/profile IDs, and bounded route/cycle
  progress from the task's normal snapshot operation;
- source tick, capture time, frame/geometry identity, session, process, canvas/
  viewport bounds, and camera yaw/pitch/zoom from the immutable Observation,
  plus the configured desired zoom range and its typed classification;
- additive `observation.sceneCensus` and `observation.observationPipeline`
  evidence copied from that same immutable Observation; these report source
  coverage, duplicate handling, bounded work, cache/queue state, payload size,
  and host timings without granting the frame or a reader control authority;
- the actual immutable decision, including its diagnostic reason and action;
  serialized generic-key and camera-hold actions retain their semantic key or
  direction and bounded duration, while camera zoom retains only its semantic
  signed amount rather than a raw transport command;
- additive task-owned camera evidence: acquisition state and episode ID, locked
  target key/kind, desired yaw and pitch ranges, error/visible-ratio/pitch/zoom
  classification, correction and cumulative-hold counts, capability maximum,
  response-model sample/rate values, last observed deltas/no-effect, pitch-limit
  direction, fresh overshoot proof, and retained reason;
- the actual selected target and the eligible/rejected candidates produced by
  the task's selection path, including stable rejection codes;
- ordered `SafetyCheck` values produced by the safety evaluations actually
  used before activation, including bounded retries;
- pending verification and the latest `VerificationResult`/typed `Outcome`;
- the last immutable `InputReceipt`, including its exact reason, typed
  `failureKind` (such as `cursor_state_invalidated`), complete command/ACK
  ledger, `safelyUnsent` pre-serial proof when applicable, and final firmware
  status. Its additive `cursorFeedback` evidence carries wait/settled counts,
  maxima, and the last delayed MOVE's command, points, timings, and outcome.
  Current receipts also retain the typed cursor-invalidation cause, a bounded
  ordered sequence of actual PMv2 cursor samples, exact pointer geometry,
  movement/activation bounds, and initial-versus-correction plan counts.
  Versioned transactions additionally retain typed required capabilities, the
  immutable negotiated device capabilities, the exact activation boundary, and
  pending/final camera pose or zoom verification evidence.
  Movement-only external-cursor receipts additionally carry
  `cursorReacquisition`: PMv2 virtual/neutral bounds, before/after cursor,
  bound PID/root HWND, exact outer/client/canvas geometry, completion,
  unchanged-geometry, and no-activation evidence. Older v1 artifacts may omit
  these additive fields. The adjacent `activationAttempted`
  boolean preserves the conservative may-have-activated classification even
  when that receipt is unsuccessful; and
- cleanup evidence derived from command/ACK counts and final firmware state,
  plus the current blocker.

Diagnostics never rerun target selection or SafetyGate checks. A diagnostic
publication failure is recorded internally and cannot change task control.
The publisher intentionally retains only the latest frame/receipt, not prior
retry history; per-transaction evidence remains complete, but a later execution
can replace an earlier cursor-invalidation/reacquisition receipt.

## Observation census and pipeline evidence

`engine_frame.v1` keeps the existing schema version and adds two objects beneath
`observation`. Both are immutable copies of owner-produced Observation evidence;
EngineFrame does not recompute them. Optional values are omitted from JSON when
unknown. Older frames may omit both objects, and a reader must treat that as
UNKNOWN evidence rather than complete coverage or a cache hit.

`observation.sceneCensus` uses exactly `scene_census_evidence.v1`. Its additive
field names are:

```text
schema, sourceSchema, metadataPresent,
complete, authoritativeAbsenceEligible, priorityAbsenceEligible,
sceneCoverageComplete, count, returned, responseCapHit, sourceCapHit,
centerWorldLocation, anchorSource, radiusTiles,
requestedTileCount, scannedTileSlots, scannedTiles, missingTileCount,
discoveredObjectCount, sourceDuplicateObjectCount,
sourceContradictoryDuplicateCount, indexedObjectCount,
enrichedObjectCount, projectedObjectCount,
requestedPriorityObjectIds, requestedPriorityObjectKeys,
reportedPriorityObjectIds, returnedPriorityObjectIds,
priorityObjectsComplete, reportedPriorityObjectKeys,
returnedPriorityObjectKeys, priorityKeysComplete,
duplicateRowCount, duplicateGroupCount, conflictingDuplicateKeys,
omittedUnnamedCount, parsedObjectCount
```

`centerWorldLocation`, when present, is the exact `{x, y, plane}` anchor.
`complete` means the raw requested census was complete; it does not mean every
discovered row was returned. `authoritativeAbsenceEligible` is the stronger
proof required to treat an arbitrary omitted row as absent.
`priorityAbsenceEligible` applies only to requested exact priority identities and
requires complete raw coverage; a conflict for that identity revokes it. Source
duplicate counts are emitted by Java, while row/group/conflict/omission/parsed
counts include the bounded host adapter's whole-row validation. No field allows
data from one duplicate row to be merged into another.

`observation.observationPipeline` uses exactly
`observation_pipeline_evidence.v1`. Its additive top-level field names are:

```text
schema, sourceSchema, requestId, querySequence, queryPurpose,
sourceTick, clientTick, sessionId, processId, geometryFrameId, rawCacheKey,
responseBytes, httpMillis, decodeMillis, parseMillis, indexMillis,
serviceTimingMillis,
cacheHit, cacheMiss, cacheEntries, cacheHits, cacheMisses,
refreshSequence, refreshReason, refreshDurationMillis, queryDurationMillis,
worldModelAgeMillis, maxResponseBytes,
requestedProjectionRefs, effectiveProjectionRefs,
projectionRefsBeforeCap, projectionRefsAfterCap, trimmedProjectionRefs,
projectionRefsCapped,
serializationPasses, serializedBytesReusedForWrite, operationCounts,
queryDiagnostics, endpointQueueDiagnostics
```

`operationCounts` is a string-to-nonnegative-integer map supplied by the world
model. When present, `queryDiagnostics` retains the exact nested
`client_thread_query_diagnostics.v1` fields:

```text
schema, lane, requestStatus, requestCoalesced, workExecuted,
timeoutMillis, queueWaitMillis, executionMillis,
activeRequestCount, pendingRequestCount, maxDepth,
submittedCount, executedCount, coalescedCount, supersededCount,
timedOutCount, expiredBeforeExecutionCount, lateResultCount, failedCount,
lastQueueWaitMillis, maxQueueWaitMillis,
lastExecutionMillis, maxExecutionMillis
```

When present, `endpointQueueDiagnostics` retains the exact nested
`plugin_snapshot_endpoint_queue_diagnostics.v1` fields:

```text
schema, workerLimit, pendingCapacity, activeWorkerCount,
pendingRequestCount, pendingRemainingCapacity, largestWorkerCount,
completedRequestCount, executionRejectionCount, rejectionPolicy,
snapshotRequestActive, snapshotBusyRejectionCount, executorState
```

The source/session/tick/geometry identifiers bind the metrics to the same
Observation. Cache, queue, payload, serialization, and elapsed-time values are
diagnostic only. They may explain latency or a failed-closed wait, but raw timing
never authorizes activation. `SafetyGate` continues to evaluate authoritative
coverage/identity facts before `InputCoordinator`; no EngineFrame reader can
turn either evidence object into a retry or input decision.

## Candidate and safety evidence

Generic frozen diagnostic records live beside the task contract. The concrete
woodcut FSM classifies candidates once and uses that same result for both
selection and `DecisionEvidence`. Target evidence carries exact identity,
action, point, bounds, source tick, and geometry-frame provenance. A target
cannot be both eligible and rejected, and the selected target must be eligible.

`SafetyGate.evaluate_*` records each ordered subcheck and returns the same
terminal `SafetyResult` as the retained `validate_*` API. The gameplay action
layer carries those exact checks into `ExecutionResult`; display code never
calls SafetyGate.

## Camera acquisition evidence

`DecisionEvidence.camera` is an additive diagnostic view of the task-owned
episode. Its `acquisitionState` is one of `idle`, `stabilizing`, `coarse`, `fine`,
`ready`, `zoom_required_but_unavailable`, `non_improving`, `exhausted`, or
`invalidated`. `episodeId`, `lockedTargetKey`, and `lockedTargetKind` expose the
current lock without granting a reader authority to retain or change it.

The same object retains `desiredYawRange`, `desiredPitchRange`,
`visibleAreaRatio`, `pitchValid`, `zoomClassification`,
`zoomRequiredButUnavailable`, and `capabilityMaxHoldMillis`. Its bounded
`responseModel` publishes only sample count and calibrated yaw/pitch units per
millisecond. `lastResponse` publishes the last observed deltas, no-effect,
pitch-limit direction, and whether fresh changed-geometry evidence proved
overshoot. The task remains the sole owner of those facts; overlay/GUI readers
must not recompute a hold, reverse a direction, release a target, or authorize an
activation from them.

Older `engine_frame.v1` and decision fixtures may omit these additive camera
fields. Readers use safe defaults and must not fabricate a historical episode,
capability negotiation, response measurement, or zoom classification.

## Versioned camera-input receipt evidence

The nested receipt remains `input_transaction_receipt.v1`; the capability
expansion is additive rather than a second receipt or frontend contract.
Current receipts may contain:

- `requiredCapabilities`: ordered `required_input_capabilities.v1` values for
  the typed intent;
- `negotiatedCapabilities`: one immutable `input_capabilities.v1` or
  `input_capabilities.v2` value built from the actual device handshake;
- `activationBoundary`: `input_activation_boundary.v1`, retaining the semantic
  operation, private command name, PID/optional root HWND, attempted and
  acknowledged state, command sequence, requested/applied duration or wheel
  amount, cursor point when required, source geometry identity, and pre-action
  yaw/pitch/zoom; and
- `cameraVerification`: `camera_input_verification.v1`, retaining pending/pass/
  fail, reason, observed tick, before/after yaw/pitch/zoom and geometry identity,
  and whether protected UI state remained unchanged.

For camera hold, the activation boundary records `CAMERA_HOLD`, one approved
direction, and the exact requested/applied duration. For zoom it records
`WHEEL`, the exact signed requested/applied amount, the cursor point already
inside the approved world viewport, and the pinned HWND/geometry provenance.
The runtime attaches the later verifier result; firmware acknowledgement alone
never fabricates pose or zoom success.

Legacy receipts and EngineFrames may omit all four fields. A reader must render
that as capability/activation-detail evidence unavailable, not as v2 support or
successful camera verification. The GUI and overlay only present these frozen
values; they cannot negotiate capabilities, select a duration/wheel amount,
rerun SafetyGate, or authorize a retry.

## Passive overlay

`osrs_bot.debug_overlay.DebugOverlay` consumes only the latest EngineFrame:

- selected target: green;
- eligible alternatives: amber;
- rejected candidates, when explicitly enabled: red;
- compact state, binding/progress, target, safety, verification/outcome,
  cleanup, and blocker text.

Absolute geometry is drawn only when its source tick and geometry-frame ID
still match the frame's displayed Observation. Stale geometry is suppressed;
the overlay does not reproject or replace it.

The Windows host has no input bindings. It resolves Tk's HWND to the top-level
root window, applies and verifies `WS_EX_TRANSPARENT`, `WS_EX_NOACTIVATE`,
`WS_EX_TOOLWINDOW`, and `WS_EX_LAYERED` on that root, then positions and shows
the topmost window without activation. It imports no Arduino, coordinator,
Observation client, task selection, or SafetyGate code. Startup/render failure
hides and destroys any created window so frozen stale evidence is not left
visible, warns, and leaves the engine running. Normal stop exits the callback
and Tk mainloop before destroying the root and releasing callback/canvas
references on the overlay's owning thread. With `--overlay` omitted, no overlay
window or thread is created.

Examples:

```powershell
.\run.cmd task --overlay
.\run.cmd task --overlay --overlay-show-rejected
.\run.cmd execute COM6 --overlay
```

The overlay is diagnostic only. It is not proof that a target is currently
safe to click; only the normal task, SafetyGate, InputCoordinator, and Verifier
path can authorize and prove an action.

## Application read contract

`EngineApplication.read_engine_frame()` returns the exact latest object held by
the runtime's `EngineFramePublisher`; it does not copy, rebuild, or reinterpret
the frame. `read_statistics()` likewise returns the runtime-owned immutable
counters rather than deriving them from frame sequence numbers.

Application lifecycle is intentionally separate. `pause_requested`, `paused`,
and `safe_stop_requested` describe cooperative frontend control, while the
EngineFrame stage continues to describe the real engine boundary. A frontend
must render both honestly and must never turn lifecycle state into target,
safety, verification, receipt, cleanup, or blocker evidence.

## Additive observability evidence

EngineFrame's optional `observability` object is immutable owner-produced
diagnostic evidence. It uses the additive `engine_observability.v1` schema:

- `schema`: exactly `engine_observability.v1`;
- `timing`: one `engine_phase_timing.v1` object;
- `waitState`: the exact active wait-state string, or `null`;
- `waitElapsedMillis`: a bounded nonnegative integer for the active wait, zero
  when no wait is active; and
- `observedWaitStates`: an immutable ordered list of exact wait states observed
  by the owning execution path.

The nested `engine_phase_timing.v1` object contains:

- `schema`: exactly `engine_phase_timing.v1`; and
- `phases`: an ordered additive list whose entries contain exact `phase`,
  `count`, `totalMillis`, `maxMillis`, and `lastMillis` fields.

Every sample is an integer from 0 through 86,400,000 ms. Each aggregate count is
from 0 through 1,000,000 and its total is bounded by 86,400,000,000,000 ms.
`maxMillis` and `lastMillis` cannot exceed the per-sample bound. Aggregates are
frozen, nonnegative, ordered by the fixed phase vocabulary, and updated by
returning a new value rather than mutating prior evidence.

The exact eleven timing phases, in serialization order, are:

1. `observation_request_fetch`
2. `source_coherence_freshness_wait`
3. `task_decision`
4. `safety_gate_evaluation`
5. `input_lease_acquisition`
6. `arduino_connect_negotiate_arm`
7. `pointer_planning_feedback_settlement`
8. `serial_write_acknowledgement`
9. `post_action_fresh_observation_wait`
10. `semantic_or_camera_verification`
11. `final_cleanup`

The exact eight wait states are:

- `WAITING_FOR_NEXT_SCENE_UPDATE`;
- `WAITING_FOR_SOURCE_COHERENCE`;
- `INPUT_TRANSACTION_BUSY`;
- `CURSOR_FEEDBACK_SETTLING`;
- `ARDUINO_HEALTH_STALE`;
- `ARDUINO_COMMAND_FAILED`;
- `SENSOR_STALE`; and
- `PRESENTATION_FRAME_STALE`.

Engine owners publish these states; the GUI does not infer execution waits from
frame age. `WAITING_FOR_NEXT_SCENE_UPDATE`,
`WAITING_FOR_SOURCE_COHERENCE`, `INPUT_TRANSACTION_BUSY`, and
`CURSOR_FEEDBACK_SETTLING` are neutral expected/busy states, not fault aliases.
`SENSOR_STALE` is exact sensor safety truth. `PRESENTATION_FRAME_STALE` and
`ARDUINO_HEALTH_STALE` are distinct passive-age facts.
`ARDUINO_COMMAND_FAILED` is a real command-path failure and is presented
immediately.

`ExecutionResult.observability` and `InputReceipt.observability` carry the same
schema for their owner-produced portions. Command records may add bounded
`writeDurationMillis` and `acknowledgementDurationMillis`. All fields are
optional additions to existing wire formats; older EngineFrame and
`input_transaction_receipt.v1` fixtures may omit them. Readers must use safe
defaults rather than fabricating historical measurements.

The compact execution contract remains structurally important:

```text
lastExecution.activationAttempted
lastExecution.receipt
```

`activationAttempted` belongs to the enclosing execution/EngineFrame. It is not
inside `InputReceipt`, and presentation code must not look for it there.
Observability payloads contain only bounded phase/wait numbers and enumerations;
they must not contain secrets, session tokens, raw typed text, raw serial
payloads, or raw ACK lines.

Neither EngineFrame nor any frontend gains task, safety, retry, input,
verification, or cleanup authority from this evidence. Regression acceptance
for this increment is recorded in `docs/ENGINE_STATUS.md`; live gameplay
remained explicitly out of scope.
