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

An unknown axis begins with one HID-count probe. Before every MOVE, all four
directions on both screen axes must contain an explicit envelope of eight device
pixels per HID count across the complete planner path; this also contains a
reversed or cross-axis response within that declared envelope. Observed
transaction transfer must not exceed four. A missing, reversed, uncommanded, or
larger response aborts before activation. Containment remains conditional on
the declared eight-pixel physical-transfer envelope; an unbounded or faulty
external transfer cannot be made safe by software alone.

Gameplay transit is confined to the loaded-scene telemetry canvas in Win32
device pixels. Before a game tick can provide canvas geometry, saved-session
login transit may use only the exact visible PID-owned Win32 RuneLite client
bounds. A single supported prompt must be detected and revalidated from that
same client screenshot, its point must still belong to that window, and the
cursor must already be inside the client. This pregame exception never applies
to gameplay, credentials, MFA, text entry, or a bank PIN.
The helper verifies that its active Windows thread is per-monitor-v2 DPI aware
before trusting native bounds, screenshots, or cursor feedback; inability to
prove that coordinate context blocks before any hardware connection.

The coordinator checks focus/PID and actual cursor feedback throughout the
trajectory. Every correction is another bounded deterministic plan. Immediately
before activation, it passes the actual settled device-pixel point to the
caller's fresh validator. The cursor must remain unchanged and inside both the
verified transit and activation bounds before and after that validation. The
validator must still prove the exact hover/default action or open-menu row at
that actual point. Context-menu failures attempt an acknowledged Escape before
normal cleanup.

## Supported callers

- `CoordinatedActionInterface` converts SafetyGate-approved gameplay actions,
  including bank-close Escape, into coordinator intents.
- `LoginPromptHelper` converts only recognized already-authenticated prompts
  into coordinator intents and never submits text.
- `TaskRuntime` creates one coordinator only for explicit live execution.

Replay, dry-run, observation, diagnostics, overlay, and demonstration capture
must not construct the coordinator or open hardware.
