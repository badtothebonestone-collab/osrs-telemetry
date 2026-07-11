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
- source tick, capture time, frame/geometry identity, session, process, and
  canvas bounds from the immutable Observation;
- the actual selected target and the eligible/rejected candidates produced by
  the task's selection path, including stable rejection codes;
- ordered `SafetyCheck` values produced by the safety evaluations actually
  used before activation, including bounded retries;
- pending verification and the latest `VerificationResult`/typed `Outcome`;
- the last immutable `InputReceipt`; and
- cleanup evidence derived from command/ACK counts and final firmware state,
  plus the current blocker.

Diagnostics never rerun target selection or SafetyGate checks. A diagnostic
publication failure is recorded internally and cannot change task control.

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

The Windows host has no input bindings and verifies `WS_EX_TRANSPARENT`,
`WS_EX_NOACTIVATE`, `WS_EX_TOOLWINDOW`, and `WS_EX_LAYERED` before showing the
topmost window without activation. It imports no Arduino, coordinator,
Observation client, task selection, or SafetyGate code. Startup/render failure
warns and leaves the engine running. With `--overlay` omitted, no overlay
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
