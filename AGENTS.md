# Engineering Rules

## Governing context

At the start of an engine work session, read `AGENTS.md`, `PLANS.md`, and
`docs/ENGINE_STATUS.md`. Read `docs/PRODUCT_VISION.md` and
`docs/ARCHITECTURE.md` for new or cross-cutting work, then load only the contract
documents for the boundary being changed. Do not mechanically load every design
document for a narrow fix.

`docs/RESCUE_CONTRACT.md` freezes the proven regression slice; it is no longer
the active development phase.

## Product boundary

Build a small, modular, OSRS-specific automation engine. The only implemented
task/site remains Lumbridge west ordinary Trees -> Lumbridge Castle bank ->
return. New flexibility comes from validated profiles and immutable task/site
definitions, while every task retains its own explicit FSM.

Never introduce a generic or high-level game-agent framework, generic planner,
task language, task DSL, behavior-tree framework, knowledge fabric, learned
control policy, automatic learning, MCP surface, compatibility layer for
deleted architecture, or second telemetry endpoint. Bounded deterministic
algorithms local to one owning boundary, such as pointer trajectories, are
allowed and may not bypass the explicit task FSM. Do not add another task or
site during the active mission.

Anti-detection, stealth, evasion, and randomization intended to avoid detection
are not project goals.

## Sources of truth

The following list is the governing target architecture. Current milestone
exceptions are explicit in `docs/ENGINE_STATUS.md` and must be removed by their
assigned milestones rather than hidden in documentation.

Keep one source of truth for each layer:

- one RuneLite snapshot endpoint and coherent tick sensor frame;
- one immutable `Observation` downstream of the adapter;
- one active task-specific FSM behind the shared task contract;
- one `SafetyGate` with non-overridable engine invariants;
- one Arduino session owner and automated input path;
- one typed verification/outcome path;
- one immutable diagnostic `EngineFrame` with no control authority;
- one thin application facade that composes those owners without duplicating
  them.

RuneLite API facts are authoritative for session/ticks, player position and
plane, inventory/equipment, entity identity/actions, menus/widgets, and game
values. Vision may supplement, propose a point inside API-confirmed geometry, or
veto an unsafe visual condition; it may never replace authoritative API facts.

## Safety invariants

- All automated OS input, including login assistance, is Arduino-backed.
- Never type credentials, MFA, or a bank PIN.
- Profiles and task/site definitions may narrow behavior but may never weaken
  freshness, PID/session/focus binding, exact identity, geometry, hover/menu
  proof, PIN refusal, required verification, runtime/action bounds, or cleanup.
- Require a fresh loaded scene before gameplay. `LOGGED_IN` alone is not proof.
- `docs/INPUT_COORDINATOR.md` governs pointer transactions. Always sample the
  real current Win32 cursor in a proven per-monitor-v2 device-pixel context;
  never infer it from a prior command. RuneLite stays stationary during cursor
  recovery: production cursor paths may not reposition or resize it and may not
  use a software cursor API. A cursor outside the verified canvas may move only
  through the existing Arduino-backed coordinator under its existing
  cross-process lease, to a neutral point comfortably inside that canvas, with
  no click or key activation. That movement invalidates the old intent and
  requires fresh lane-specific observation and safety validation.
- Before pointer motion, bind the exact telemetry PID and foreground root HWND,
  prove the physical mouse quiet, sample the PMv2 virtual desktop, and retain
  exact native outer/client plus telemetry-canvas geometry. A connected Arduino
  preflight must also prove zero firmware-held keys and mouse buttons before the
  first MOVE. Gameplay waits for
  RuneLite to become foreground and blocks if it does not; saved-session login
  may use one bounded `SetForegroundWindow`-only attempt when exact identity and
  outer/client geometry remain unchanged. A minimized window or failed focus
  requires manual attention. After the cursor enters the canvas and again
  before activation, the exact pinned root HWND/PID must own the point.
  The first MOVE also requires two identical PMv2 cursor samples separated by
  one timestep, followed by a fresh physical-button quiet proof and one final
  unchanged cursor/foreground sample. A stationary manual takeover is accepted
  as current truth, while continuing motion or a late prior report requires
  fresh reobservation.
  Gameplay may reconcile only a one-device-pixel AWT/native outer-origin
  quantization when outer size is exact and the native client still contains
  the exact canvas; login outer/client geometry remains exact.
  Windows button state is aggregate and source-blind. Run a bounded post-ACK
  attribution window after the coordinator's acknowledged click; same-button
  human input anywhere in that window is
  source-indistinguishable and best effort. Other-button or persistent activity
  blocks there; the ordinary quiet proofs still block fresh activity outside
  that window.
- A firmware MOVE acknowledgement proves command handling, not Windows cursor
  arrival. Start a monotonic feedback deadline before the serial MOVE so ACK
  latency counts. If the ordinary samples do not show a complete cumulative
  effect on every commanded axis, poll without input at fixed 20 ms intervals:
  the complete effect must be observed by 200 ms and two later identical whole-
  cursor samples must fit by 240 ms, with at most ten extra polls. Send no Arduino
  command, STATUS, rearm, or watchdog refresh during that wait. Every extended
  sample must retain the pinned foreground, virtual-desktop/canvas bounds,
  exact unchanged outer/client/canvas geometry, direction, gain, and
  uncommanded-axis proof; exact point ownership is additionally required once
  the cursor has entered the canvas. After stability, re-prove physical-button
  quiet and one final unchanged owned cursor, discard the old trajectory, and
  replan from current truth. Timeout becomes typed cursor-state invalidation
  and may receive only the bounded lane-specific fresh reobservation. Saved-
  session login permits at most two total cursor-recovery attempts before a
  manual-attention blocker. Same-direction, in-gain buttonless human motion is
  source-indistinguishable, so fresh ownership and semantic validation remain
  the final vetoes.
- Require exact target identity, verified canvas geometry, and exact post-move
  lane evidence for every pointer activation: object hover/menu or widget
  geometry/state as appropriate. Typed key actions instead require their exact
  engine-owned key shape and task constraint. Every sent pointer or key action
  requires a later typed verification.
- An object aim point is authorized only inside the first present RuneLite API
  shape in clickbox -> convex hull -> canvas tile order and inside the viewport.
  `canvasLocation` is diagnostic evidence unless that authoritative shape also
  contains it. Exact post-move hover/menu evidence remains the final veto.
- Every connected attempt must end with confirmed `STOP_ALL`, `DISARM`, and
  authoritative wire `STATUS` proving disarmed, zero held keys, zero held mouse
  buttons, and no unresolved command evidence.
- A lease or preflight rejection before serial connect instead needs an empty
  closed ledger and closed backend; never claim firmware cleanup for hardware
  that was not opened. A connected cursor-reacquisition attempt always requires
  the full authoritative cleanup sequence, including on failure.
- Fail closed. Missing authorization proof is not permission to add an input-
  capable fallback.
- Dry-run, replay, overlay, diagnostics, and demonstration capture must not
  inject input or open hardware sessions.

`run.cmd login COMx` may click only the retained idle-disconnect OK,
saved-session Play Now, and Click here to play surfaces. All other
login/recovery surfaces fail closed. A coherent loaded scene may use a bounded
template-only absence check after the normal matcher caps, but that check cannot
use the disconnect heuristic or authorize input. Login cursor recovery is
limited to two total automated attempts; after that, or for credentials, MFA,
PIN, minimized-window, or unsupported-prompt handling, request manual attention.

## Architecture and development

- Keep `run.cmd` as the public wrapper over the existing application facade; it
  must not duplicate engine logic or compose a second runtime path.
- Production control downstream of the observation adapter consumes immutable
  engine contracts; it must not read plugin caches or raw response dictionaries.
  Bounded read-only diagnostics and the demonstration recorder may inspect or
  preserve raw payload evidence, but never use it as a second control path.
- Keep task logic, safety, RuneLite parsing, Arduino control, verification, and
  state ownership out of future GUI/overlay code.
- Overlay and status readers may only render immutable `EngineFrame` evidence.
  They must suppress stale geometry and never rerun selection or safety checks.
- Generic runtime orchestration may use task contracts but must not know
  concrete task phases, item IDs, route facts, or mutable progress internals;
  the explicit task FSM necessarily owns those task-specific facts.
- Demonstrations and historical runs are append-only evidence. They may suggest
  reviewed definition/fixture changes but never authorize input or raw replay.
- Frontend lifecycle commands must carry the current run/capture ID. Pause and
  safe stop are cooperative at no-input boundaries; never kill a worker or
  interrupt an Arduino transaction/pending verification.
- An LLM may inspect immutable definitions/evidence and launch an explicitly
  user-authorized public wrapper command. It must not synthesize individual
  runtime actions or bypass the FSM, safety gate, input coordinator, or verifier.
- Prefer deletion and direct OSRS-specific code over speculative abstraction.
- Preserve Arduino firmware and the wire protocol unless hardware/protocol
  evidence proves a change is necessary. The host backend may change for a
  reproduced OS, transport, or cleanup defect backed by focused tests and
  proportionate live evidence.

## Work and validation discipline

- Keep `PLANS.md` and `docs/ENGINE_STATUS.md` current after every milestone.
- Keep each completed milestone in one coherent checkpoint commit; closely
  related hardening fixes may share that checkpoint. Do not push unless asked.
- Use focused tests while iterating. At each checkpoint, run the full affected-
  language suite plus `run.cmd replay`; at phase and final gates run both Python
  and Java. Inspect the diff for prohibited expansion before committing.
- Gradle writes current build/test output to its configured external
  `layout.buildDirectory`. Never count Java results from a stale checkout-local
  `build/` directory; resolve the configured output and verify its timestamps.
- Use live validation only for evidence replay cannot provide. Keep it bounded;
  after a repeated equivalent failure, preserve evidence and patch the failing
  boundary before another run.
- After any live input, prove cleanup with `STOP_ALL`, `DISARM`, and safe status.

The final gate commands are:

```powershell
.\run.cmd replay
.\run.cmd test
```

Client/plugin changes require a newly launched RuneLite client built from the
current checkpoint. Host-only Python changes require a fresh host process,
proof tied to the current checkout commit, a repinned exact PID/session, and
fresh telemetry; they may reuse the unchanged RuneLite client. Documentation-
only edits do not invalidate otherwise current proof.

Non-input live commands are `run.cmd observe`, `run.cmd task`, and
`run.cmd record-demo NAME`; `run.cmd inspect-demo PATH` verifies finalized
evidence offline. `run.cmd app catalog|profile-schema|validate-profile` exposes
the frontend contract. Gameplay is explicitly opt-in through
`run.cmd execute COMx` or `run.cmd app run --execute --arduino-port COMx`.
