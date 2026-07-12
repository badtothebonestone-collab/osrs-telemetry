# Input Coordinator Contract

## One automated-input owner

`osrs_bot.input_coordinator.InputCoordinator` is the only production owner of
Arduino sessions. Gameplay and saved-session login keep their distinct fresh
validators, but both submit immutable `ApprovedPointerIntent` or
`ApprovedKeyIntent` values to the same coordinator. They never receive the
transport object.

The Arduino implementation is the private `_ArduinoHIDTransport`. Its connect,
arm, movement, button, key, cleanup, status, ledger, and close operations are
internal. There is no public backend compatibility alias and no software-input
fallback. Static boundary tests reject another production importer or caller.

## Transaction and receipt

Every coordinator transaction is non-reentrant. It creates one private backend,
begins an empty command ledger, and acquires the shared cross-process input
lease before any serial connection or pointer preflight. Pointer lanes then
sample current cursor, focus, virtual-desktop, and exact RuneLite geometry. If
the cursor is outside the canvas, the same transaction connects and arms the
existing Arduino transport, performs movement-only reacquisition to a neutral
canvas region, sends no click or key, cleans up, and returns typed invalidation
so the caller must rebuild the intent from fresh evidence. Every connected
input transaction follows this bounded lane:

```text
begin command ledger
  -> connect
  -> protocol-safe ARM
  -> bounded input with fresh validation
  -> optional acknowledged context-menu Escape cancellation
  -> acknowledged STOP_ALL
  -> acknowledged DISARM
  -> acknowledged wire STATUS
  -> close ledger
  -> close transport
  -> immutable InputReceipt
```

An input is successful only when the receipt contains ordered, unique command
records with stable IDs and terminal firmware acknowledgements. The final
cleanup proof must end with `STOP_ALL -> DISARM -> STATUS`, and STATUS must say
`armed=false`, `keysDown=0`, and `mouseButtonsDown=0`. Missing acknowledgement,
rejection, timeout, unresolved command, malformed status, incomplete ledger,
or close failure makes the receipt unsuccessful. ARM secrets are redacted.
The transport never retries a state-changing command after firmware rejection;
in particular, `ERR NOT_ARMED` records one rejected write, performs fail-safe
cleanup, and returns an unsuccessful receipt.

Even after a blocked validator or action failure, any attempted connection
runs all available cleanup operations and records their individual results.
The receipt is carried by gameplay execution results, runtime output, and login
click evidence rather than summarized into a mutable backend dictionary.
`input_transaction_receipt.v1` now adds `cursorFeedback`: bounded wait/settled
counts, maximum extra polls and elapsed milliseconds, and the last event's
plan/step, command, before/final points, first/complete effect times, and
outcome. Movement-only recovery also retains additive `cursorReacquisition`
(`cursor_reacquisition.v1`): PMv2 coordinate space, virtual-desktop and neutral
bounds, before/after cursor, exact bound PID/root HWND and outer/client/canvas
geometry, completion, unchanged-geometry, and no-activation proof. Every
recorded wait must settle for a successful receipt. Older v1 artifacts may omit
these additive fields. `EngineFrame` intentionally retains only the latest
execution receipt, so a later retry can replace an earlier transaction's
evidence in terminal run output.

## Pointer policy

`osrs_bot.pointer.plan_pointer_motion` is a pure deterministic policy. It emits
only relative deltas inside the caller's verified transit region, with bounded
per-command displacement, velocity, acceleration, and target-aware braking.
It reaches the exact target at rest and contains no randomization or transport
access. The private transport independently rejects zero moves and deltas over
20 pixels on either axis.

The coordinator aims at the center/proposed safe point but separately evaluates
observed arrival because integer Arduino HID counts and Win32 device pixels need
not share a one-pixel lattice at scaled display settings. It feeds the pure
planner exact, bounded command-space waypoints and normally lets each plan finish
at rest before replanning from actual cursor feedback. If bounded delayed-
feedback settlement interrupts a trajectory, the coordinator discards its
remainder and requires a fresh correction or zero-step confirmation plan. The
complete Arduino transaction, including context-row movement, is capped at 64
plans and 512 MOVE commands.
Only a settled endpoint inside an explicit caller-
approved activation region may authorize a click; a transient crossing cannot.
An already-stable point in that region is represented by a complete zero-step
plan and still requires fresh actual-point validation. Gameplay regions are the
verified target and canvas intersection clipped to three device pixels around
the SafetyGate-approved point; template-backed saved-session login uses a tight
cursor-safe activation footprint inside the freshly recognized prompt bounds.

Cursor position is external mutable state, not coordinator history. Every
backend cursor sample establishes per-monitor-v2 awareness on the calling
thread immediately before `GetCursorPos`; point ownership independently does
the same before `WindowFromPoint`. A fresh CLI thread and an application worker
therefore see the same physical device-pixel point. Missing APIs, an ineffective
thread-context change, or a failed cursor read blocks and never substitutes
`(0,0)` or the last commanded location.

Every initial pointer intent has a no-input preflight before serial connect.
The backend first acquires the same cross-process port lease that protects the
later Arduino session, without opening or arming the device. The lease remains
held through preflight, any connected cursor movement, authoritative cleanup,
and backend close. Contention blocks with an empty closed ledger and performs
no serial open, MOVE, activation, software cursor call, or RuneLite geometry
mutation.
The first physical-button sample consumes only historical released-button bits,
then two bounded all-clear samples must prove no held or new button activity.
The exact foreground root HWND/PID is pinned, and native PMv2 Win32 geometry is
split deliberately. Gameplay's telemetry `clientWindow*` envelope must have the
exact `GetWindowRect` size; its AWT-derived origin may differ from the native
origin by at most one device pixel per axis to reconcile display-scale
quantization. Its exact canvas must still be contained by the current
`GetClientRect`/`ClientToScreen` rectangle. Login must match both its exact outer
and exact client rectangles. A resize, a larger origin difference, or failed
canvas containment does not qualify.

RuneLite remains stationary. Normal production cursor recovery contains no
window-position/size mutation and no software cursor operation. Gameplay
requires the telemetry-owning RuneLite root to be foreground and waits only for
the existing bounded focus-wait interval before blocking. Saved-session login may
call `SetForegroundWindow` once for the exact visible, non-minimized root, but
must sample and prove identical PID/HWND and outer/client geometry before and
after; it never restores, moves, or resizes the window. Failed focus or a
minimized window is an actionable manual-attention blocker.

If the freshly sampled cursor is outside the canvas, its start must be inside
the freshly proven PMv2 virtual desktop. The coordinator derives a central
neutral canvas region with a safe inset, connects and arms the one Arduino
transport under the already-held lease, and moves only the cursor toward that
region. Protocol-safe ARM first proves firmware `keysDown=0` and
`mouseButtonsDown=0`. Reacquisition sends no click, mouse button, or key. Every movement sample preserves
the exact PID/root HWND, foreground ownership, virtual-desktop bounds,
outer/client/canvas geometry, physical-button release, bounded direction/gain,
and velocity/acceleration limits. Foreign-surface transit is permitted only
before canvas entry; after entry, every sample must stay inside the canvas and
belong to the exact pinned root. Completion requires a stable cursor inside the
neutral region and bit-for-bit identical retained geometry.

Whether reacquisition succeeds or fails, the connected attempt ends with
`STOP_ALL -> DISARM -> STATUS`, closes its ledger/backend, releases the lease,
and sends no activation. Success still returns typed cursor-state invalidation:
the old action or login intent is discarded rather than activated. Gameplay
waits for a strictly newer tick from the same PID/session whose source is
fresh, wall-clock-fresh, and coherent, plus exact unchanged geometry, before
target recognition and normal SafetyGate validation. Login
re-fetches telemetry, re-finds the exact window, and re-screens the entire
client at tick zero before prompt recognition and validation.

An unknown axis begins with one HID-count probe. Before every MOVE, all four
directions on both screen axes must contain an explicit envelope of eight device
pixels per HID count across the complete planner path; this also contains a
reversed or cross-axis response within that declared envelope. Observed
transaction transfer must not exceed four. A missing, reversed, uncommanded, or
larger response aborts before activation. Containment remains conditional on
the declared eight-pixel physical-transfer envelope; an unbounded or faulty
external transfer cannot be made safe by software alone.

Before the first MOVE in each transaction, the coordinator samples the current
PMv2 cursor twice with one deterministic timestep between reads and rechecks the
pinned foreground window. After those reads agree, it re-proves physical-button
quiet, rejecting any button transition consumed since the earlier baseline, and
takes one final unchanged cursor/foreground sample. A manually
displaced but now stationary cursor is therefore the new starting truth;
continuing movement, button activity during the dwell, or a late report from a
prior cleaned transaction becomes typed cursor-state invalidation before any
new MOVE. This no-input quiescence gate also precedes the one allowed
lane-specific retry: login re-finds and re-screens the exact current client,
while gameplay requires a newer fresh/wall-clock-fresh/coherent tick from the
same PID/session.

The coordinator starts a monotonic cursor-feedback clock immediately before
each serial MOVE, so write/ACK latency counts. If either ordinary post-MOVE
sample still lacks a commanded axis, it discards the remaining trajectory and
enters a no-input settlement loop. The loop uses a fixed 20 ms interval, at
most ten extra polls, and sends no MOVE, STATUS, ARM, or watchdog refresh. A
complete cumulative effect on every commanded axis must first be observed by
200 ms. Two later identical whole-cursor samples must fit by the absolute
240 ms cursor-stability deadline. The subsequent physical-button quiet proof
and final unchanged cursor sample do not extend command credit or authorize a
new MOVE.

Every extended sample must retain the pinned foreground HWND/PID, current PMv2
coordinate space, applicable virtual-desktop/canvas bounds, exact unchanged
outer/client/canvas geometry, and cumulative direction, gain, and uncommanded-
axis rules. Once inside the canvas it must also retain exact point ownership.
The final sample repeats foreground, owner, geometry, and bounds proof. A stable stationary manual
takeover is current truth, so the final stable point may differ from the point
where the Arduino effect was first observed; the old trajectory is always
discarded and a fresh correction or zero-step plan is required. Same-direction,
in-gain buttonless human motion is inherently source-indistinguishable. Fresh
point ownership, physical-button quiet, and the lane's exact semantic validator
remain final vetoes rather than retrospective source attribution.

Effect not observed by 200 ms, instability at 240 ms, focus/owner/bounds drift,
or invalid transfer becomes typed cursor-state invalidation before activation.
The gameplay runtime may reobserve once; login permits at most two total cursor-
recovery attempts, including the first, and then returns an explicit manual-
attention blocker. A separate later user attempt always samples the user's
current cursor anew. The receipt retains success and failure timing, including
an effect first observed
after the deadline, without misclassifying safe firmware cleanup as an input
error. No path may send another MOVE while the prior effect remains unproved.

Normal action transit is confined to the loaded-scene telemetry canvas in Win32
device pixels. The optional telemetry `clientWindow*` bounds provide expected
outer-window geometry for PID/HWND binding; they are not movement or activation
authority. The external-cursor lane is a distinct movement-only transaction
bounded by the verified virtual desktop until it enters the canvas, then by the
canvas and its exact root owner. It terminates at the neutral inset and never
continues into the stale action's target. Only a later fresh intent may use the
ordinary canvas planner and semantic activation path.

Before a game tick can provide canvas geometry, saved-session login uses the
same exact visible PID-owned Win32 RuneLite client boundary. A single supported
prompt must be detected and revalidated from that same client screenshot, and
the final point must still belong to that window. This pregame exception never
applies to credentials, MFA, text entry, or a bank PIN. The helper also verifies
its active Windows thread is per-monitor-v2 DPI aware before trusting native
bounds or screenshots; inability to prove that context blocks before hardware.
Both the initial and post-move login checks still scan the complete configured
zones for every supported authenticated template, so ambiguity is never
narrowed to an earlier candidate. Within one fresh screenshot only, the matcher
indexes each zone's bright pixels once and reuses that bounded index across its
allowed template scales. An invalid or greater-than-four-million-pixel search
zone, more than 20,000 high-anchor-score origins, or excessive first-anchor
density blocks instead of becoming a no-match or disconnect candidate. These
are fail-closed work caps; supported live geometry is separately measured
inside the firmware lease rather than claimed as a universal latency bound.
Login cursor-state recovery may create at most two automated attempts. After
the second unsuccessful attempt it returns
`manual_attention_required_after_two_login_recovery_attempts`; it does not
retain or fight an older cursor coordinate. Credentials, MFA, text entry, bank
PINs, minimized windows, and unsupported prompts always require manual handling.

When that normal matcher caps on an otherwise coherent loaded scene, the helper
may perform one larger but still bounded scan for the two exact retained login
templates. This fallback is read-only and absence-only: it excludes the broad
disconnect-dialog heuristic, cannot authorize input, and can contribute to PASS
only with two increasing loaded ticks from the same PID/session. If the scan
ages its observation beyond two seconds, the loaded proof is refreshed and must
remain current, coherent, same-identity, and non-regressing.

The coordinator checks focus/PID and actual cursor feedback throughout the
trajectory. Every correction is another bounded deterministic plan. Immediately
before activation, it passes the actual settled device-pixel point to the
caller's fresh validator. The cursor must remain unchanged and inside both the
verified transit and activation bounds before and after that validation, the
physical mouse must remain quiet, and `WindowFromPoint` must still resolve to
the exact pinned root HWND/PID. The validator must still prove the exact
hover/default action or open-menu row at that actual point. After each
acknowledged Arduino `MOUSE_UP`, one bounded reader runs attribution for that
button's possibly delayed Windows transition. It permits that owned
button's high/low state to settle for at most 500 ms, requires two consecutive
all-clear samples, and rejects any other-button or persistent owned-button
activity. Windows exposes aggregate, source-blind button state, so attribution
of same-button human input anywhere in the 500 ms window is necessarily best
effort. The host-side reader sends no firmware command and does not renew the
watchdog; existing STATUS/rearm/revalidation gates remain mandatory before any
later movement or activation. The bounded window and final all-clear proof
prevent residual held or queued button state from contaminating a context row
or the next transaction; they cannot distinguish or undo an independent human
same-button activation.
Context-menu failures attempt an acknowledged Escape before normal cleanup.

RuneLite exposes its menu mouse position as integer source-canvas pixels. At
the retained scaled layout, mapping that value back to PMv2 device pixels has a
separately bounded four-device-pixel correlation allowance. That allowance is
used only to bind a fresh exact hover/default or context-row menu sample to the
settled Win32 cursor. It does not enlarge the three-pixel coordinator activation
region, source/fresh canonical aim checks, canvas/shape containment, or menu
identity requirements; a five-pixel menu-coordinate disagreement still blocks.

The coordinator treats the firmware watchdog as a short activation lease. It
checks or explicitly refreshes that lease before each lane-specific validator,
then checks it again after validation. If the validator outlives the lease, the
coordinator may perform one explicit protocol-safe rearm and must rerun the
same pointer, key, activation-choice, or context-row validator on fresh
evidence. If that revalidation also outlives the lease, activation blocks. The
final preactivation check is status-only: it cannot rearm or retry beneath
already-validated semantics. Context-menu Escape cancellation may explicitly
rearm because it releases an opened menu before normal cleanup. An accepted or
ambiguously completed written context-menu button-down marks the menu as
possibly open, so a rejected or unacknowledged release still triggers that
cancellation path; an explicit button-down rejection does not send Escape.

## Unsent target invalidation

The adaptive gameplay action layer has two typed unsent dispositions.
`TARGET_EVIDENCE_INVALIDATED` means fresh exact hover proof changed before any
activation; the resource task may suppress that exact pending key for one fresh
alternate selection. `CURSOR_STATE_INVALIDATED` means the observed physical
cursor/ownership/bounds state changed or a no-click reacquisition completed; it
permits one fresh reobservation but does not suppress the target. Runtime
accepts either only when the receipt is blocked, the failure kind matches the
typed disposition, and either connected
cleanup is authoritative and safe or a pre-serial receipt proves an empty closed
ledger and closed backend. Any ledger commands must remain preactivation/
cleanup-only--never mouse activation or a key press. Neither lane is input
success or a verification result. Any activation command, incomplete terminal
proof, mismatched denial, identity change, or second consecutive invalidation
fails closed instead of replanning.

## Supported callers

- `CoordinatedActionInterface` converts SafetyGate-approved gameplay actions,
  including bank-close Escape, into coordinator intents.
- `LoginPromptHelper` converts only recognized already-authenticated prompts
  into coordinator intents and never submits text.
- `TaskRuntime` creates one coordinator only for explicit live execution.

Replay, dry-run, observation, diagnostics, overlay, and demonstration capture
must not construct the coordinator or open hardware.
