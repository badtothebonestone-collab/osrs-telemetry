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

Every coordinator transaction is non-reentrant and follows one bounded lane:

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
planner exact, bounded command-space waypoints, lets each plan finish at rest,
then replans from actual cursor feedback. The complete Arduino transaction,
including context-row movement, is capped at 64 plans and 512 MOVE commands.
Only a settled endpoint inside an explicit caller-
approved activation region may authorize a click; a transient crossing cannot.
An already-stable point in that region is represented by a complete zero-step
plan and still requires fresh actual-point validation. Gameplay regions are the
verified target and canvas intersection clipped to three device pixels around
the SafetyGate-approved point; saved-session login may use the complete freshly
recognized prompt bounds inside the exact client.

Cursor position is external mutable state, not coordinator history. Every
backend cursor sample establishes per-monitor-v2 awareness on the calling
thread immediately before `GetCursorPos`; point ownership independently does
the same before `WindowFromPoint`. A fresh CLI thread and an application worker
therefore see the same physical device-pixel point. Missing APIs, an ineffective
thread-context change, or a failed cursor read blocks and never substitutes
`(0,0)` or the last commanded location.

Every initial pointer intent has a no-input preflight before serial connect.
The first physical-button sample consumes only historical released-button bits,
then two bounded all-clear samples must prove no held or new button activity.
The exact foreground root HWND/PID is pinned, and native PMv2 Win32 geometry is
split deliberately: gameplay's telemetry `clientWindow*` envelope must equal
`GetWindowRect`, its canvas must be contained by the current
`GetClientRect`/`ClientToScreen` rectangle, and login must match both its exact
outer and exact client rectangles. A same-PID window with different geometry
does not qualify.

If the stationary cursor is beyond that verified outer window, the coordinator
does not move it through the desktop. Instead, while the mouse remains quiet,
it issues one no-resize/no-z-order/no-activation `SWP_ASYNCWINDOWPOS` for the
exact non-minimized, non-maximized RuneLite HWND on the cursor's monitor. The
window must fit the work area and converge within 750 ms so the translated
strict movement region contains the unchanged cursor with 32 device pixels of
headroom. Cursor, focus, buttons, final root ownership, and two settled plus one
final exact window rectangles must all pass. The old intent is then always
returned as typed safely unsent: login re-finds the window and re-screens the
whole client even though login tick remains zero; gameplay waits for a newer
tick from the same PID/session. A post-`SetWindowPos` error or contradictory
handoff evidence is terminal and cannot consume the retry path. An async request
that times out cannot be canceled and may move the window later, but no input or
automatic retry follows it and every future run rechecks exact geometry.

An unknown axis begins with one HID-count probe. Before every MOVE, all four
directions on both screen axes must contain an explicit envelope of eight device
pixels per HID count across the complete planner path; this also contains a
reversed or cross-axis response within that declared envelope. Observed
transaction transfer must not exceed four. A missing, reversed, uncommanded, or
larger response aborts before activation. Containment remains conditional on
the declared eight-pixel physical-transfer envelope; an unbounded or faulty
external transfer cannot be made safe by software alone.

If the ordinary post-MOVE sample is unchanged on any commanded axis, the
coordinator waits one more deterministic timestep and samples again without
sending another MOVE. Direction, gain, uncommanded-axis, movement bounds, and
foreground checks apply independently to both the first prefix and the
incremental second sample, so unrelated/manual motion cannot mask a bad report.
Only a combined zero-effect observation enters the existing isolated retry
lane: that axis remains uncalibrated and a needed correction plan uses a larger
bounded probe. A second consecutive zero-effect sample on the same axis aborts.
Successful transfer resets that axis's consecutive count, but the complete
transaction may contain at most eight isolated zero-effect events; the ninth
aborts. These retries consume the same 64-plan and 512-MOVE transaction caps and
never authorize activation without a settled point and fresh validation.

Normal gameplay transit is confined to the loaded-scene telemetry canvas in
Win32 device pixels. The optional telemetry `clientWindow*` bounds are the outer
window rectangle and form only a movement-only reacquisition region. A freshly
sampled cursor just outside the canvas may enter when it is still owned by the
exact pinned RuneLite root HWND/PID, lies outside on exactly one axis, is no more
than 64 device pixels from the canvas, and has the required cross-axis and
four-sided transfer headroom.
At most 72 one-count inward MOVEs may reach a stable eight-pixel inset. Cross-
axis motion, wrong direction, excess gain, no progress/effect, ownership/focus
change, multiple outside axes, or insufficient outer-edge headroom blocks before
activation. Only then does the ordinary canvas planner start from the newly
observed point. This supports a manual cursor handoff or window resize without
turning the whole desktop into an input region.

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
acknowledged Arduino `MOUSE_UP`, one bounded reader attributes and consumes only
that owned button's possibly delayed Windows transition, rejects other/held/new
activity, and leaves an all-clear baseline for a context row or the next
transaction. Context-menu failures attempt an acknowledged Escape before normal
cleanup.

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

The adaptive gameplay action layer has two typed, safely unsent dispositions.
`TARGET_EVIDENCE_INVALIDATED` means fresh exact hover proof changed before any
activation; the resource task may suppress that exact pending key for one fresh
alternate selection. `CURSOR_STATE_INVALIDATED` means the observed physical
cursor/ownership/bounds state changed; it permits one fresh reobservation but
does not suppress the target. Runtime accepts either only when the receipt is
blocked, the failure kind matches the typed disposition, and either connected
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
