# Minimal Task Contract

Phase 3 exposes only the seam needed for another explicit OSRS task FSM. It is
not a task language, planner, behavior tree, or data-driven transition system.

## Runtime-facing protocol

Every task implements four operations from `osrs_bot.task_contract`:

- `observation_request()` returns the bounded tile projections needed for the
  next decision.
- `decide(observation)` returns an opaque task state, a diagnostic reason, and
  one immutable `Action`.
- `apply_verification(result)` consumes a typed `VerificationResult`.
- `snapshot()` returns task identity, opaque state, optional blocker, and one
  of `RUNNING`, `COMPLETE`, or `BLOCKED`.

The runtime knows none of the task's phases, mutable progress, object or item
IDs, route, bank floor, or dialogue wording. Executable actions without a
`VerificationSpec` are rejected before an input interface can be called.

## Typed outcomes

`Verifier` emits one immutable outcome: item quantity increased/equaled,
moved closer, arrived, plane changed, interface opened/closed, or dialogue
option appeared. A passing result cannot exist without a typed outcome. Reason
strings are diagnostic only and never select a task transition.

## Safety ownership

`SafetyGate` evaluates two deliberately separate layers:

1. Engine invariants: coherent fresh source evidence, loaded scene, session and
   process binding, foreground focus, exact tick/menu evidence, PIN refusal,
   target identity, verified geometry, canvas bounds, and exact hover/menu
   proof. Tasks and future profiles cannot disable these checks.
2. Immutable task constraints on the action: required interface state and
   plane, exact dialogue choice, and allowed inventory contents. Constraints
   can only narrow an action; they cannot authorize one that failed an engine
   invariant.

Task-specific dialogue tokens, interface plane/state, and permitted item IDs
are supplied to `WoodcutBankTask` by its validated definition binding. The
shared safety and verification modules contain no Lumbridge coordinates, Tree
IDs, log IDs, or woodcut phases.

## Current implementation

`WoodcutBankTask` remains one explicit FSM and owns all of its mutable state.
The generic fake task in `tests/test_runtime.py` exercises the same unmodified
runtime, safety-compatible action contract, and typed verifier pathway.
