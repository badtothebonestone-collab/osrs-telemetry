# Architecture

## Governing target

The engine is an OSRS-specific contract pipeline:

```text
Profile
  -> validated Task/Site Definition
  -> explicit task-specific FSM

RuneLite API facts
  -> immutable atomic SensorFrame
  -> immutable Observation

Observation + task decision
  -> ActionIntent + VerificationSpec
  -> SafetyGate
  -> InputCoordinator
  -> PointerMotionPolicy
  -> Arduino transport + firmware

Fresh Observation
  -> Verifier
  -> typed Outcome
  -> task FSM

Runtime state and evidence
  -> immutable EngineFrame
  -> recorder / passive overlay / thin operator GUI
```

There is one observation truth, one runtime orchestrator, one active explicit
task FSM, one safety gate, one Arduino session owner, one typed verification
path, and one read-only diagnostic truth. Diagnostics have no control authority.

The bounded architecture migration and first operator GUI are implemented. The
current column is implemented; the target column now names preservation rules.

| Layer | Current baseline | Governing target |
|---|---|---|
| Sensor | atomic game-tick `SensorFrame` plus separately stamped client/menu evidence | preserve the same single source contract while adding no second endpoint |
| Observation | immutable source-coherent `Observation` | preserve the same single truth for task-neutral seams |
| Definition/profile | one immutable `LUMBRIDGE_WEST_TREES_V1` plus one validated one-cycle default profile | preserve typed validation; add no dynamic loader during this mission |
| Task | minimal `Task` protocol plus definition-bound concrete `WoodcutBankTask` FSM | preserve the protocol and explicit task-specific transitions |
| Decision | opaque state/action plus immutable selected/eligible/rejected evidence from the actual task path | preserve this task-neutral evidence contract for frontends |
| Safety | one `SafetyGate`, explicitly split between engine invariants and typed task constraints | preserve the same non-overridable boundary |
| Runtime configuration | immutable endpoint/Arduino/poll/bound values plus one bounded behavior policy and run seed | same contract consumed by every frontend |
| Input | one `InputCoordinator`, seed-reproducible bounded pointer policy, private Arduino transport, immutable wire receipts | preserve the sole-owner boundary for every future caller |
| Verification | one `Verifier` returning immutable typed `Outcome` values | preserve the same typed pathway |
| Diagnostics | one immutable latest `EngineFrame` with route/camera/target/pointer/timing evidence plus optional passive click-through overlay | preserve one no-authority status contract for recorder/frontend readers |
| Demonstration evidence | bounded read-only recorder plus tamper-verifying semantic inspector | preserve append-only, no-replay, review-only evidence |
| Frontend | tokenized `EngineApplication`, facade-only async GUI controller, Tkinter operator GUI, and diagnostic CLI | preserve one in-process facade with no GUI-owned domain or input authority |

## Ownership boundaries

The following categories must never collapse into one unvalidated configuration
blob:

| Category | Owns | Must not own |
|---|---|---|
| Profile | selected definition, bounded goals, supported preferences | engine safety switches, raw IDs/routes, endpoint or serial internals |
| Task/site definition | versioned OSRS IDs, actions, areas, bank, route, transitions, predicates, provenance | mutable task state, hardware, runtime lifecycle |
| Runtime configuration | endpoint, Arduino port, poll rate, hard limits, behavior bounds and seed | task meaning or weaker safety rules |
| Engine invariants | freshness, binding, focus, geometry, menu proof, PIN refusal, verification, cleanup | user-overridable options |

The first implementation contains exactly one built-in Lumbridge definition and
one default profile. Adding a definition does not create a generic planner: it
provides validated facts to a still-explicit task FSM.

`BehaviorConfig` and one run-scoped `BehaviorPolicy` own bounded route, camera,
aim, pointer, and timing choices. A run seed plus stable decision IDs make those
choices reproducible without global random state. Context-sensitive delays may
vary pointer/click/camera pacing, but observations and typed game outcomes remain
the primary waiting signals; arbitrary sleeps never replace state verification.

## Current implementation

### Sensor

The RuneLite plugin owns the only live telemetry cache. `PluginLiveCache`
publishes one immutable `SensorFrame` through one atomic reference; partial or
login captures replace the prior frame and explicitly mark missing facts. Its
localhost service exposes exactly three read-only routes:

- `GET /health`
- `GET /schema`
- `POST /snapshot`

The endpoint does not apply configuration or accept gameplay commands.
`plugin_snapshot_response.v2` separates frame capture time from
`assembledAtUtc`. Core facts share one frame tick/session/process identity.
World-model and tile geometry must also match the frame's camera/window
fingerprint before the endpoint merges them. Menu evidence is independently
stamped from its real post-menu-sort sample.

The canonical adapter requests one neutral `scene_object_census`; the endpoint
does not expose resource/route/service classifiers and Python ignores those
retired payload names. Scene rows contain factual identity, actions, location,
and projection only. Optional definition-owned priority object IDs are ordering
hints for projection work, never filters or classifiers; the census retains
competing objects. Projection selection otherwise uses the explicit request,
factual distance, and stable object key. Selected definitions assign resource,
route, and bank meaning downstream. Dialogue capture is also structural:
pinned RuneLite widget identities expose raw prompt/option facts, while exact
staircase wording remains definition/task-owned.

The same endpoint exposes demonstration-only bounded `client_tick_tail`,
`actor_census`, and `collision_window` needs. Hot rows from client tick,
post-menu-sort, and `MenuOptionClicked` share one monotonic sequence and retain
per-lane drop counters. Actor rows are NPC-only; nested interacting-player data
is omitted. Actor/collision evidence is exposed only after the endpoint binds
its source tick, capture, session, process, and geometry frame to the atomic
observation. These additive views do not create a second observation or input
surface.

### Observation

`osrs_bot.observation.ObservationClient` sends one canonical snapshot request
and produces one immutable `Observation`:

```text
player, location, plane, inventory, nearby_objects, menus, widgets,
game_state, camera yaw/pitch, source timestamp, assembly timestamp,
frame identity, tick, canvas bounds, optional client-window bounds
```

Source coherence, freshness, canvas bounds, warnings, and missing capabilities
travel with the same object because they determine whether any action is safe.
Object census rows are deduplicated by stable object key. Canvas coordinates are converted to
screen coordinates once, at this boundary. Menu samples preserve the explicit
top/default entry, scene parameters, client-tick sequence, and sampled pointer.
When RuneLite opens a context menu, the adapter also exposes the transformed
menu bounds and deterministic visible row bounds used by the input path.
Actions and verifications are bound to the plugin session; live input is also
bound to the exact telemetry-owning RuneLite process.

The optional device-pixel `clientWindow*` envelope is all-or-none, has positive
dimensions, and must contain the canvas. It represents the outer window and is
ownership/movement-only cursor-ingress evidence, never an activation region;
the input preflight requires its exact `GetWindowRect` size, permits only a
one-device-pixel AWT/native origin reconciliation for gameplay, and separately
proves the exact canvas inside the actual Win32 client. Object aim points are
likewise paired to authoritative geometry: the point must be inside the viewport
and the first present API shape in clickbox -> convex hull -> canvas tile order.
The engine insets a fully visible shape. For a clipped or oversized authoritative
shape it instead requires bounded visible overlap plus a safe interior aim that
remains inside that same shape. Clipping never permits weaker-geometry fallthrough
or broadening. UI-overlapped and competing regions remain excluded; the engine
scores bounded interior candidates and selects among strong points with
recorded-seed variation. `canvasLocation` alone is diagnostic unless the
authoritative shape contains it. Fresh exact hover/menu evidence still vetoes
the selected point before activation.

`LOGGED_IN` alone is not loaded-scene proof. The plugin requires a local player
and an explicitly absent Welcome to Gielinor panel before publishing
`scenePlayable=true`; `Observation.loaded_scene` requires that bit as well.

No downstream module reads raw JSON or a plugin cache.

### Bounded telemetry and observation pipeline

The scene path is a staged, bounded extension of the single sensor/Observation
authority. Its full contract, benchmark method, and current limitations are in
[`TELEMETRY_PIPELINE.md`](TELEMETRY_PIPELINE.md).

```text
phase-specific ObservationRequest
  -> bounded endpoint admission
  -> one-active / one-newest-pending client-thread scheduler
  -> exact-source and raw-request-shape cache
  -> player-centered or explicit-anchor tile window
  -> definition-free identity census and deterministic duplicate quarantine
  -> bounded selected-row definition/action enrichment
  -> bounded returned-row projection
  -> two-pass exact-size serialization
  -> bounded host read, parse, and immutable SceneIndex
  -> one Observation with census/pipeline evidence
  -> task decision -> SafetyGate -> InputCoordinator
```

`WorldModelCache` retains at most four exact-source raw snapshots, 256 cached
enriched rows and 128 cached projections per snapshot. The raw identity census
has a 10,000-row hard cap; each response may return and definition-enrich at
most 64 rows. Raw identity includes source tick, session, process, geometry
frame, plane, scene base, dirty sequence, anchor, radius, and requested raw
capabilities. Same-source requests with different priority ordering or smaller
enrichment/projection budgets can reuse the raw census; any identity or raw-
shape change refreshes it. Per-request projection budget is consumed before a
cached or newly calculated projection can enter the response, so warm reuse
cannot bypass a smaller request. No wall-clock TTL can force a redundant scan
within the same source identity.

RuneLite client-thread admission is globally bounded to one active and one
newest pending query. Identical keys coalesce, newer distinct pending work
supersedes the older request, expired work is discarded before execution, and a
result arriving after its deadline is not accepted. The HTTP executor has four
workers and eight pending slots, while only one expensive snapshot may be
active; overlap returns retryable `503 endpoint_busy`. Python recognizes only
the typed retryable busy response, publishes neutral `ENDPOINT_BACKPRESSURE`
wait/timing evidence, and does not spend the ordinary observation-error budget.
At most eight consecutive busy responses are retried; the next one terminates
the affected runtime path. Malformed request JSON
returns `400`, and endpoint admission is released for the next request. JSON
encoding is exactly two passes and the final byte array is reused for the
response write.

Scene discovery captures immutable identity and location before it consults
definitions, actions, or projection. Exact duplicate keys resolve
deterministically. Contradictory identity signatures are quarantined whole, so
no row can borrow geometry, actions, or projection from another object. Python
performs a bounded read/parse and constructs one immutable `SceneIndex` for
constant-time stable-key and preindexed object-ID lookup. A planned fetch also
requires its returned center, anchor source, radius, purpose, and exact priority
sets to match the request before the response can grant completeness or absence
authority; this prevents cross-request contamination under concurrent polling.

Raw coverage completeness is distinct from response-row capping. Ordinary
absence requires a complete raw census, no response omission, and no relevant
contradiction. An exact requested priority key may prove its own absence from a
complete raw census even when unrelated rows were capped, but not when that
present matching raw row was itself omitted by the response-row cap. Incomplete,
malformed, mismatched, or contradictory evidence remains fail-closed;
performance counters and timing are diagnostic only and never authorize
activation.

### Task

`osrs_bot.task_contract` exposes five operations: request bounded observation
projections, decide from one immutable observation, apply one typed verification
result, discard a proposal that the input boundary proves was never activated,
and publish an immutable running/complete/blocked snapshot. Task state is opaque
to runtime. Runtime does not import the concrete task, compare its phases, or
inspect its mutable progress. An executable action without a verification
specification is rejected before an input boundary is called.

`WoodcutBankTask` is an explicit state machine:

```text
FIND_TREE -> CHOP -> VERIFY_LOGS
    ^                    |
    |                    v (inventory full)
    +------------- NAVIGATE_TO_BANK -> OPEN_BANK
                                           |
                                           v
NAVIGATE_TO_TREES <- CLOSE_BANK <- VERIFY_DEPOSIT <- DEPOSIT_LOGS
        |
        v
     COMPLETE
```

The task accepts one validated `BoundProfile`. The sole built-in definition
owns all resource/bank selectors, areas, route steps and transitions, inventory
predicates, tick expectations, and evidence provenance. The profile owns only
the selected definition and one-cycle goal. Neither owns mutable FSM state or
engine safety controls.

The two definition routes remain exact tuples of classified guidance, mandatory
turn/transition, and arrival evidence. The task projects a bounded lookahead and
uses polyline progress, corridor deviation, turns, collision/scene support, and
visibility to select the farthest useful supported point. Normal guidance may
be skipped; mandatory turns, stair/door/plane transitions, and arrivals may not.
Missing projection evidence waits without input, and a present labeled
projection with contradictory identity blocks.

The definition-owned camera lane is one task-owned, target-locked acquisition
episode rather than a series of independent reactive pulses. It pins the exact
object/tile/route identity, route index where applicable, session/PID, and client,
canvas, and viewport rectangles. Normal candidate ranking cannot switch targets
during that episode. Release requires invalid/depleted target state, lost
authoritative identity, unsafe evidence, or exhaustion of the bounded non-
improvement budget; a pinned-rectangle or process/session change invalidates
instead of silently replanning against a different environment.

The episode's final goal is a range: sufficient visible/clickbox ratio, target
inside the configurable central region, required viewport-edge clearance,
route-direction bias, valid pitch, and desired zoom class. Route framing keeps a
controlled leading-edge allowance for supported long targets, while interaction
framing remains stricter about clickbox clearance. World player-to-target bearing
provides wrap-safe desired yaw even while the target is off-screen; current
projection supplies yaw/pitch screen correction. An exact definition-owned Tree,
bank booth, or route-transition may therefore enter acquisition before it is
actionable, but activation still requires fresh authoritative final geometry and
exact hover/menu proof.

The default episode sends one coarse correction and at most one fine correction.
Every typed key hold receives an acknowledged typed pose result, records its
actual yaw/pitch delta and changed geometry-frame identity, and invalidates the
prior projection. The task-retained bounded response model relates direction and
requested duration to observed delta, no-effect/pose-limit, and overshoot, then
selects the next hold from remaining error and measured rate. Left/right cannot
reverse unless a fresh changed-geometry result proves overshoot. An unchanged-
pose UP/DOWN limit suppresses that direction until pose changes. Materially
unsatisfied zoom that prevents safe framing produces
`zoom_required_but_unavailable` when no negotiated wheel capability exists and
no compensating key loop.

`CameraKeyCapabilities` is injected at runtime composition, while the sole
coordinator independently requires the immutable capability negotiated from the
device. Protocol `arduino_hid.v2` advertises a numeric 600 ms camera-only maximum
under `cameraKeyHold`; the task response model may choose a duration only within
that bound. Generic dialogue/interface key presses and retained multi-key
transport behavior remain bounded to 250 ms. A legacy v1 device remains usable
for pointer and short-key actions, but the new camera hold fails before
activation because v1 cannot advertise it.

When safe framing specifically requires zoom and v2 advertises `wheel=1`, the
locked episode may request one semantic signed wheel step of magnitude at most
three. This is not a generic wheel surface: the action retains the same locked
target and pre-action pose/zoom/geometry, and `SafetyGate` must prove that the
requested sign moves toward the configured desired zoom range. Unsafe or
unknown interface/text-input state, missing geometry, absent capability, or a
second still-required attempt returns the typed unavailable/blocking state.
Camera framing does not randomize direction or safety bounds; downstream bounded
aim variance remains inside geometry already accepted as safe. No planner
substitutes a route or camera strategy outside the explicit task FSM.

Staircases accept a live direct `Climb-up`/`Climb-down` action when it is the
default. If the live default is generic `Climb`, the explicit `STAIR_DIALOGUE`
state selects exactly one matching numbered up/down option and verifies the
plane change.

The task emits an `Action` and a generic `VerificationSpec`. It never sends the
action and never evaluates its own verification. It consumes only typed
outcomes from the shared verifier.

The same concrete selection/classification path also emits immutable
`DecisionEvidence`: exact selected target, eligible candidates, and rejected
candidates with stable codes. `TaskSnapshot` publishes the bound definition and
profile plus route/cycle progress. Runtime reads only these task-contract values
and never inspects mutable FSM progress.

### Safety and action

`SafetyGate` first checks non-overridable engine invariants: coherent/fresh
source evidence, loaded scene, session/process/focus binding, source tick, PIN
refusal, exact identity, geometry, canvas bounds, the fixed 16-device-pixel
gameplay pointer-safe viewport inset, and menu provenance. It then
checks immutable task constraints carried by the action: expected interface
state/plane/readability, exact dialogue choice, and permitted inventory. These
constraints can only narrow an action. Bank plane and staircase wording are not
shared safety constants.

Each public safety evaluation records its actual ordered subchecks. The action
layer carries those values, including bounded retry attempts, in
`ExecutionResult`; diagnostics do not rerun SafetyGate. If a semantic click or
key may have been written before the coordinator proof fails, the result marks
`activation_attempted`. Runtime blocks that post-activation proof failure
without retry or verification credit. The unsuccessful receipt keeps
`ExecutionResult.sent` false, while the separate activation flag prevents the
runtime's terminal reason or unsent disposition from claiming the semantic
action was safely unsent. The same flag is retained in EngineFrame diagnostics.

`CoordinatedActionInterface` and the saved-session login helper submit typed
approved intents to the sole `InputCoordinator`. The coordinator then:

1. creates one private backend and empty in-memory command ledger, acquires the
   configured cross-process lease before serial open, and keeps that lease
   through cleanup and backend close;
2. requires the exact telemetry PID/root HWND to be foreground. Gameplay waits
   only for its bounded focus interval and otherwise blocks; saved-session login
   may use one `SetForegroundWindow`-only attempt for a visible, non-minimized
   exact root, with identical outer/client geometry before and after;
3. proves a bounded physical-button quiet dwell, samples the actual current
   cursor and virtual desktop in the calling thread's PMv2 device-pixel context,
   pins the exact root HWND/PID, matches the telemetry outer size to
   `GetWindowRect`, bounds gameplay-only AWT/native origin quantization to one
   device pixel, and retains exact native outer/client plus telemetry-canvas
   geometry;
4. confines every ordinary gameplay waypoint, actual feedback/correction point,
   transit point, settled point, and activation point to one engine-owned fixed
   16-device-pixel inset of the authoritative viewport. The inset is already in
   device pixels and is not scaled again for DPI. Ordinary gameplay receives one
   initial trajectory plus at most two feedback-correction replans;
5. exposes cursor reacquisition only as a separate geometry-only transaction.
   That lane derives a neutral region inside the inset, opens and arms the same
   private Arduino transport, and uses its distinct bounded movement allowance.
   Protocol-safe ARM proves zero firmware-held keys/buttons before its first
   MOVE. RuneLite never moves or resizes, and no software cursor API, click,
   mouse button, or key is sent;
6. during reacquisition, preserves the PMv2 virtual desktop, exact PID/root HWND,
   foreground ownership, unchanged outer/client/canvas geometry, physical-
   button release, direction/gain, and bounded velocity/acceleration on every
   sample. Foreign-surface transit is allowed only before canvas entry; every
   later point remains in the canvas and belongs to the pinned root;
7. settles at the neutral canvas point, retains `cursorReacquisition` before/
   after cursor and geometry evidence, then always performs authoritative
   `STOP_ALL -> DISARM -> STATUS` cleanup and returns typed invalidation. The old
   action or login intent is discarded and can never activate merely because
   reacquisition succeeded;
8. permits this cycle only for a first typed `unexpected_direction`,
   `unsupported_transfer_gain`, `unexpected_cross_axis`,
   `outside_padded_viewport`, or `point_owner_mismatch`. It requires gameplay to
   obtain a strictly newer tick from the same PID/session whose source is fresh,
   wall-clock-fresh, and coherent, with exact unchanged geometry, then reruns
   recognition and normal SafetyGate validation. The first executable retry
   must be a pointer action. A repeated invalidation, changed PID/HWND or
   geometry, physical input, cleanup failure, or unsent/non-pointer retry is
   terminal. Login retains its separately bounded helper policy;
9. for every first MOVE, requires two identical PMv2 cursor samples one timestep
   apart, then a fresh physical-button quiet proof and a final unchanged cursor/
   foreground sample, accepting a stationary manual position as current truth
   while typing continued motion or a late prior report as cursor invalidation;
10. retains the canonical action identity/aim separately from the actual settled
    cursor, selects bounded seed-reproducible curved command-space waypoints
    toward the chosen point, and uses distance, target size, context, and screen
    bounds for velocity, acceleration, braking, approach, and duration. Each
    ordinary plan remains inside the pointer-safe inset, then accepts only a
    complete-plan settled endpoint inside the explicit activation region;
11. starts a monotonic clock before every serial MOVE and, when ordinary samples
    lack any commanded axis, discards the trajectory and uses at most ten fixed
    20 ms no-input polls; the full cumulative effect must be observed by 200 ms
    and two later identical whole-cursor samples by 240 ms, with pinned
    focus/ownership, bounds, direction, gain, and uncommanded-axis proof on every
    extended sample, then physical-button quiet plus a final unchanged owned
    sample before a fresh plan; unresolved or invalid evidence becomes typed
    cursor-state invalidation and can never be stacked with another command;
12. passes that actual stable device-pixel endpoint to the caller's lane-specific
    validator under a checked firmware-watchdog lease; if that validator outlives
    the lease, performs at most one explicit safe rearm and reruns the same
    semantic validator, while a second expiry blocks input;
13. for pointer lanes, repeatedly requires quiet physical buttons and exact
    `WindowFromPoint` root ownership around the newer menu/widget proof; typed
    key lanes instead require their exact camera/interface/dialogue constraint;
14. when the exact action is a unique lower context entry, opens the menu,
    derives that row from RuneLite menu geometry, moves to it, revalidates the
    fresh open-menu sample and pointer, and clicks it once;
15. otherwise clicks the exact default entry or submits the one approved key,
    then uses a bounded source-blind attribution window and two all-clear samples
    for the acknowledged Windows button transition; same-button human input
    during that window remains inherently best effort;
16. records each command and firmware acknowledgement without truncation plus
    bounded delayed-feedback counts, maxima, last command/points/timings, and
    outcome; and
17. ends every attempted connection with acknowledged `STOP_ALL`, `DISARM`, and
    wire `STATUS` proving disarmed with zero held inputs before closing.

Protocol-safe ARM negotiates one frozen `InputCapabilities` from exact
`IDENTIFY`, `CAPS`, and `STATUS` evidence before any activation. Every typed
pointer, short-key, camera-hold, zoom, and cleanup intent declares a matching
`RequiredInputCapabilities`; the coordinator rejects an absent operation or
smaller limit before its activation callback. Task and action code never see the
transport and never construct `CAMERA_HOLD`, `WHEEL`, or any other raw serial
command. The private firmware operations remain inside the same lease, ledger,
ARM, validation, activation, `STOP_ALL`, `DISARM`, `STATUS`, and close envelope.

`arduino_hid.v2` adds only two semantic operations. Atomic `CAMERA_HOLD` accepts
one of left/right/up/down for 1--600 ms, releases before its exact
requested/applied-duration ACK, and stays below the 1,000 ms watchdog. `WHEEL`
accepts one nonzero signed amount with magnitude at most three and returns the
exact requested/applied amount. Generic key bounds remain 250 ms. Firmware-side
new-command argument, limit, arming, or unknown-command failures release and
disarm. ACK or transport failures trigger the existing fail-closed cleanup and
grant no activation or verification credit; a state-changing rejection is never
retried.

Zoom activation has no cursor-movement sublane. It requires the actual cursor
already inside the pointer-safe world viewport and owned by the pinned root
HWND, physical input quiet, exact unchanged native/telemetry geometry, and a
fresh loaded scene with bank, PIN, dialogue, and text input inactive. A newer
verification observation must then preserve process/session and player
location, yaw, pitch, and protected UI state while changing both geometry-frame
identity and `zoom3d` in the requested direction. Unchanged or contradictory
zoom is a typed failure, not permission for another yaw/pitch or wheel loop.

The immutable `InputReceipt` is successful only when the activation and final
cleanup sequence are present in order, every command is terminal and
acknowledged, no ledger entry is unresolved, the final firmware state is safe,
every recorded cursor-feedback wait settled, and both ledger and transport
close. Current receipts also serialize the typed invalidation cause, bounded
ordered actual cursor samples, pointer geometry, activation/movement bounds, and
initial/correction-plan counts. Versioned camera transactions additionally
retain required and negotiated capabilities, the exact activation boundary,
requested/applied hold or wheel values, pre-action pose/zoom, and the eventual
typed pose/zoom verification evidence. Additive `cursorReacquisition` evidence
for movement-only external-cursor transactions: PMv2 virtual/neutral bounds,
before/after cursor, bound PID/root HWND, exact outer/client/canvas geometry,
completion, unchanged geometry, and no activation. Older additive-v1 receipts
may omit `cursorFeedback`, `cursorReacquisition`, or the versioned capability/
camera fields. The raw transport methods are
private and only the coordinator imports them. A state-changing firmware
rejection is never retried implicitly. There is no software-input fallback.

The action layer may emit typed `TARGET_EVIDENCE_INVALIDATED` when an adaptive
object/walk proposal exhausts bounded fresh hover reobservation before
activation, or `CURSOR_STATE_INVALIDATED` when typed real cursor feedback
becomes invalid. Runtime may recover only the five explicitly eligible causes
once per run,
and only when the immutable receipt proves the matching failure kind, either
complete connected cleanup or a closed empty pre-serial ledger/backend, and no
activation. Target invalidation suppresses the exact resource key for one fresh
alternate. Cursor recovery discards the old target/intent, performs movement-
only neutral reacquisition, and requires fresh recognition plus SafetyGate
before one pointer retry. Reason text is diagnostic and never selects the
transition. Any activation, incomplete cleanup, mismatch, identity/geometry
drift, physical input, non-pointer retry, or repetition blocks.

### Saved-session login assistance

`run.cmd login COMx` is a bounded helper beside the task engine, not a second
planner or recovery framework. It recognizes only the retained Play Now and
Click here to play visual templates plus the narrowly bounded historical
idle-disconnect OK geometry, binds the exact telemetry process and RuneLite
client window, submits the prompt through `InputCoordinator`, revalidates it
after moving the Arduino mouse, and verifies a telemetry transition afterward.
It never types text. Continue,
credential, MFA, unknown, and ambiguous surfaces fail closed.

If the normal prompt matcher reaches its work cap on a coherent loaded scene, a
larger but still bounded read-only fallback scans only the two exact retained
templates. It excludes the broad disconnect heuristic and cannot authorize
input. Template absence can support loaded-scene PASS only after two increasing
ticks from the same PID/session; a scan-aged observation is refreshed and must
be coherent and no older than two seconds.

### Verification and runtime

`Verifier` evaluates only observations later than the action baseline and fails
at the declared tick deadline. After a sent action, runtime shifts that baseline
to the final preactivation observation so bounded pointer motion and semantic
revalidation do not consume the post-action proof window. A passing result must
include one stable typed outcome. Task-specific item IDs and dialogue
expectations are supplied in the verification specification rather than
embedded in shared control flow. The runtime adds a wall-clock and observation
bound so a frozen client cannot wait forever.

`RuntimeConfig` separately validates endpoint/token/Arduino/polling and hard
observation/action/runtime/verification limits. Its finite engine-owned caps
cannot be changed by a profile or task definition. The default action budget is
100, above the frozen 64-action ideal and observed 82-action complete cycle,
under the unchanged hard maximum of 500.

Walk verification passes after authoritative movement closer or arrival. Route
movement owns a 20-tick definition deadline, distinct from the eight-tick
ordinary action deadline. The task then waits until player location is stable
for four game ticks before it advances the waypoint, preventing repeated clicks
while pathing.

When the bank-close widget lacks usable geometry, the task may emit only the
exact Escape close intent and only when RuneLite explicitly reports keyboard
close support. The normal post-action bank-closed verification still applies.

`python -m osrs_bot task` stops at the first proposal. `--execute` is the only
task mode that constructs an `InputCoordinator`. The runtime stops on completion,
block, transport failure, verification failure, or a configured bound.

### Golden replay

`run.cmd replay` executes the sanitized cycle fixture in
`tests/fixtures/golden_lumbridge_cycle.json` and the retained camera-analysis
fixture in `tests/fixtures/retained_camera_79_trace.json`. The first freezes the
final task route, action kinds, typed verification sequence, and terminal cycle
state. Its provenance records hashes of the bounded live component traces and
explicitly states that those traces were stitched and do not contain complete
raw sensor or SafetyGate evidence. The second recomputes the retained camera
bursts, target switches, direction reversals, pitch-limit attempts, and response
rates. Its target-locked two-action result is a comparison envelope only and does
not claim counterfactual interaction success.

## EngineFrame and passive diagnostics

The real runtime publishes one immutable `EngineFrame` after observation,
decision, execution, verification, and terminal boundaries. It contains
task/state, definition/profile IDs, polyline route progress and selected
lookahead target, camera framing/action, authoritative geometry and inset aim
candidates, selected seed/decision, pointer motion evidence, selected timing,
eligible and rejected candidates with codes, decision reason, typed action
key/hold evidence, ordered safety checks, pending and last verification, typed
outcome, execution receipt, final cleanup state, and current blocker. The atomic
publisher retains only the latest monotonic frame; it is not a history store.

The optional overlay consumes this exact frame. It may show the route corridor,
mandatory/skipped route points, selected long target, desired framing region,
authoritative and inset target shapes, aim candidates/selection, and recent
pointer path in addition to green selected, amber eligible, and optional red
rejected targets. It suppresses geometry when source tick/geometry provenance
no longer matches the displayed Observation. The actual root top-level host owns
the verified Win32 click-through, non-focusable, layered, and tool-window-only
styles; Tcl creation and teardown remain on that host thread. It has no input
handlers, target selection, SafetyGate calls, or Arduino imports, and an overlay
failure cannot alter runtime control.

The recorder, diagnostic CLI, and implemented GUI consume immutable read
contracts. Readers may format or filter them but may not reselect a target,
recalculate safety, mutate the FSM, import Arduino control, or authorize input.
The live recorder's EngineFrame listener performs only a nonblocking enqueue to
a 256-frame queue. One daemon writer owns JSON encoding, the open file, and
flushes off the publication path. Queue high-water, dropped frames, bounded
writer errors, and bounded-finish writer shutdown are explicit receipt evidence;
slow storage cannot turn a subscriber callback into runtime latency.

## Demonstration evidence

`osrs_bot.demonstration` is a read-only evidence path over the existing snapshot
endpoint. It requires a coherent loaded `Observation`, then binds every poll to
the same RuneLite session/process. Atomic-frame provenance also covers the
bounded NPC census and collision window. Client, menu, and semantic-click tails
share one Java-assigned monotonic sequence; overlap is deduplicated and a reset
stops capture. Pointer samples are bounded to 20 Hz. Screenshots are read-only,
bounded inside verified canvas geometry, and suppressed for a bank PIN.

Finalization produces portable JSONL, manifest, semantic summary, concise
timeline, optional crops, and SHA-256/size evidence. Inspection verifies the
complete declared file set, paths, symlinks, limits, hashes, schemas, and
contiguous recorder sequence before deriving anything. Invalid evidence yields
no suggestions. Scene/NPC/collision/menu cap metadata remains explicit. Valid
candidates contain authoritative world anchors, actions, plane transitions,
known game-object IDs, or NPC IDs correlated through an exact same-tick census.
Walk, player, widget, incomplete, and uncorrelated menu identifiers cannot
become entity candidates. Input coordinates are omitted and every candidate is
review-only and never automatically active. The recorder imports no runtime,
safety, task, input, login, or Arduino authority.

The trusted review model keeps three distinctions explicit. First, sampled
player-world `routePoints` are outcomes, while manual `Walk here` targets are
intent evidence with each RuneLite tile representation preserved separately.
Second, keyboard/middle/mixed is an observed camera-input method, while
action-linked, exploratory/unassociated, cancelled, and no-pose-effect describe
association or outcome. Third, exact TileObject identity can come from one
same-ID object whose scene footprint matches the clicked menu coordinates, but
only a direct authoritative-shape containment is object aim-point evidence. A
fresh exact context-menu tuple instead labels the final pointer as a menu-row
activation and never projects that row into the object clickbox.

`EngineApplication` owns the ephemeral manual-versus-definition route review.
It compares plane-supported manual targets against both fixed route directions,
selects a direction only from forward-progress evidence, retains ambiguity when
direction is not unique, and exposes per-plane manual, observed, definition,
and mandatory-point layers to the GUI. It is explicitly review-only, is labeled
with the current definition version, and cannot mutate either the hashed
artifact or task data. Additive manifest feature flags preserve byte-exact
derivation of older finalized summaries and timelines.

## Application facade and frontend

`EngineApplication` is the implemented composition root. It lists the exact one
task/definition, returns the frontend-safe profile schema, reuses authoritative
profile validation, creates a fresh task/runtime/control for each start, returns
the exact latest EngineFrame and runtime-owned statistics, reports exact
blockers, and owns mutually exclusive run/demonstration worker lifecycles.

Run and capture commands carry monotonic local IDs, so delayed UI commands
cannot affect a later operation. Pause is acknowledged only at a no-input
boundary; an Observation held across pause is discarded. Once decision emits
an executable action, Arduino transaction, cleanup, bounded verification, and
typed transition finish before pause/safe-stop acknowledgement. Pause never
extends the hard runtime bound. No thread is killed and safe stop opens no extra
hardware session.

The Tkinter GUI consumes only facade and EngineFrame-facing contracts through a
thread-safe controller. Bounded operator services beneath the facade reuse the
existing RuneLite launch, login helper, overlay, demonstration inspector, and
test/replay paths. They add no endpoint, daemon, IPC, web server, or alternate
input path. Harmless settings are revalidated on startup; active run/capture,
verification, target, cursor, PID/session, and input state are never restored.

The facade imports the concrete task only to compose the sole supported engine.
It has no target selection, SafetyGate, Verifier, InputCoordinator, Arduino, or
raw-input calls. `run.cmd gui` launches the operator frontend. `run.cmd app`
retains catalog/profile and foreground run commands as diagnostics; Ctrl+C
becomes cooperative safe stop. There is no daemon or IPC layer. See
`docs/FRONTEND_CONTRACT.md`.

## Vision and LLM boundaries

RuneLite remains authoritative for identity and semantic state. The implemented
dependency-free `VisionEvidence` seam contains capture time, exact
crop/transform, model/version, class/confidence, bounds or mask, and
occlusion/image-quality status. It is frozen, explicitly non-authoritative, and
has no runtime consumer or model dependency. Vision may later supplement or
veto an unsafe condition, or propose a point inside API-confirmed geometry; it
may not overwrite session, tick, player, inventory, entity, menu, or widget
facts.

No LLM participates in runtime control. A future offline assistant may read
immutable definitions, demonstration artifacts, run history, and diagnostic
evidence. It may not emit executable input, mutate an active profile, or bypass
the FSM, `SafetyGate`, `InputCoordinator`, or `Verifier`.

## Memory and restart

Static definition knowledge is immutable and version controlled. Current task
state belongs only to the active FSM. Run history and demonstration evidence are
separate append-only records and cannot authorize behavior.

After restart, never restore pending verification, source/client ticks, menu
samples, old screen coordinates/clickboxes, session-bound targets, or armed
input state. Reobserve the game, validate a fresh profile/definition binding,
and reconcile the FSM before any new action.

The narrow reconciliations derive new state from current evidence rather than
restoring it. A full inventory may resume only the furthest matching
anchor/radius on the exact outbound route. A structurally empty inventory
outside the work area may resume only the furthest matching anchor/radius on the
exact return route, or the configured bank anchor on the matching plane within
its interaction radius while the current bank state is known; that last lane
closes an open interface first or begins return step 0 when it is already
closed. An open interface is closed even where a return step overlaps the bank
area. With a known-closed interface, the furthest exact return-step match takes
precedence and the bank-area fallback applies only when no step matches.
Unknown bank state inside the interaction area cannot use an overlapping route
match because `bankOpen=false` is not closure proof unless `bankKnown=true`.
Any reported bank PIN state outranks both open/closed route precedence and
blocks before reconciliation or input.
Partial, off-route, wrong-plane, or unknown-bank states receive no
restart-reconciliation shortcut; ordinary FSM and safety checks then apply
without historical cycle credit. Because the open-bank branch can only close,
bank contents need not be readable; an open PIN blocks before input, and exact
widget or keyboard-close support is still required.
Completing any historical return grants no cycle credit; the active process
must perform a new full cycle.

## Bounded phase and wait observability

Observability follows existing ownership instead of introducing another
controller. `TaskRuntime` owns observation, decision, post-action observation,
verification, and final frame publication timing. The action layer owns its
SafetyGate-call timing. `InputCoordinator` owns lease, connection/negotiation/
arm, pointer planning/settlement, serial transaction, and cleanup timing. The
private Arduino transport contributes only sanitized write and ACK durations
from its existing command ledger. These measurements are diagnostic outputs;
no task, SafetyGate, retry, pointer, camera, input, verification, or cleanup
branch may consult them for authority.

The immutable additive wire contracts are `engine_phase_timing.v1` and
`engine_observability.v1`, described in `docs/ENGINE_FRAME.md`. EngineFrame and
execution/receipt evidence can merge owner-produced phase aggregates without
reconstructing elapsed time from GUI polling or frame age. Existing
`input_transaction_receipt.v1` and EngineFrame readers must accept artifacts
that omit the new fields. Public evidence contains bounded numeric durations,
counts, enumerated phases, and enumerated wait states only; it excludes raw
typed text, secrets, session tokens, serial payloads, and raw ACK lines.

The GUI and overlay remain presentation-only. Exact engine/safety and
presentation classification is published immediately. Expected waits display
immediately as neutral wait states. A GUI-only hysteresis may hold the prior
rendered value across a momentary passive stale classification for no more than
500 ms; it cannot delay `ARDUINO_COMMAND_FAILED` or change a Start/Resume gate,
blocker, safety result, receipt, or cleanup classification.
`lastExecution.activationAttempted` remains an enclosing EngineFrame execution
fact beside `lastExecution.receipt`; it is not an `InputReceipt` member.

This increment adds no live cycle, Arduino firmware change, software-input
fallback, window movement, cursor injection, transport caller, or alternate
runtime path. Regression and replay acceptance are recorded in
`docs/ENGINE_STATUS.md`; live gameplay remained out of scope.
