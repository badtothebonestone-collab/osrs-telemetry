# Minimal Task Contract

The shared contract exposes only the seam needed for another explicit OSRS task
FSM. It is not a task language, planner, behavior tree, or data-driven
transition system.

## Runtime-facing protocol

Every task implements five operations from `osrs_bot.task_contract`:

- `observation_request()` returns the bounded tile projections needed for the
  next decision.
- `decide(observation)` returns an opaque task state, a diagnostic reason, and
  one immutable `Action`.
- `apply_verification(result)` consumes a typed `VerificationResult`.
- `discard_pending_action(reason, *, target_invalidated)` forgets one proposal
  only after the runtime has typed it as safely unsent. `target_invalidated=true`
  allows the concrete resource task to suppress that exact stale key for one
  alternate selection; cursor-state invalidation passes `false`, preserves the
  target, and merely reobserves from the resumable phase.
- `snapshot()` returns task identity, opaque state, optional blocker, and one
  of `RUNNING`, `COMPLETE`, or `BLOCKED`.

The runtime knows none of the task's phases, mutable progress, object or item
IDs, route, bank floor, or dialogue wording. Executable actions without a
`VerificationSpec` are rejected before an input interface can be called.

## Typed outcomes

`Verifier` emits one immutable outcome: item quantity increased/equaled,
moved closer, arrived, plane changed, interface opened/closed, dialogue option
appeared, or camera pose changed. A passing result cannot exist without a typed
outcome. Reason strings--including the reason passed to
`discard_pending_action`--are diagnostic only and never select a task
transition. The runtime may call that discard seam only for typed
`TARGET_EVIDENCE_INVALIDATED` or `CURSOR_STATE_INVALIDATED`, a matching blocked
receipt/failure kind, zero activation commands, a preactivation-only ledger,
either authoritative connected cleanup or a closed empty pre-serial
ledger/backend, and its one-consecutive-replan bound still available. Cursor
replans also pin PID/session identity and require a newer gameplay tick;
repetition blocks rather than silently following manual cursor motion or stale
target evidence.

If wire evidence shows that the semantic click or key may already have been
written but the coordinator receipt is unsuccessful, `ExecutionResult` marks
that activation attempt. Runtime blocks it as a post-activation proof failure:
it never calls the safely-unsent discard seam, never retries, never grants
verification progress, and its terminal reason/disposition never claims that
the semantic action was safely unsent. `ExecutionResult.sent` remains false
because the coordinator receipt was not fully successful; the separate
`activation_attempted` truth prevents that value from authorizing a retry. A
preparatory context-menu opener alone is not the semantic action; only the final
row click counts for this classification.

## Safety ownership

`SafetyGate` evaluates two deliberately separate layers:

1. Engine invariants: coherent fresh source evidence, loaded scene, session and
   process binding, foreground focus, PIN refusal, and exact tick evidence.
   Pointer actions additionally require target identity, verified geometry,
   canvas bounds, and exact lane-appropriate hover/menu or widget proof; key
   actions require their exact permitted key shape. Tasks and future profiles
   cannot disable these checks.
2. Immutable task constraints on the action: required interface state and
   plane, exact dialogue choice, allowed inventory contents, and camera pose.
   A `CameraConstraint` binds the exact projected target and source location,
   geometry frame, starting yaw, left/right key direction, and bounded hold.
   The later typed camera outcome requires a stationary player, a newer
   geometry frame, and yaw movement in that direction. Constraints can only
   narrow an action; they cannot authorize one that failed an engine invariant.

Task-specific dialogue tokens, interface plane/state, and permitted item IDs
are supplied to `WoodcutBankTask` by its validated definition binding. The
shared safety and verification modules contain no Lumbridge coordinates, Tree
IDs, log IDs, or woodcut phases.

## Current implementation

`WoodcutBankTask` remains one explicit FSM and owns all of its mutable state.
The generic fake task in `tests/test_runtime.py` exercises the same unmodified
runtime, safety-compatible action contract, and typed verifier pathway.
