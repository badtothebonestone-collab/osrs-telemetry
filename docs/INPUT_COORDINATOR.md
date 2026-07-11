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
only relative deltas inside the verified RuneLite canvas, with bounded
per-command displacement, velocity, acceleration, and target-aware braking.
It reaches the exact target at rest and contains no randomization or transport
access. The private transport independently rejects zero moves and deltas over
20 pixels on either axis.

The coordinator checks focus/PID and actual cursor feedback throughout the
trajectory. Any correction is another bounded deterministic plan. Immediately
before activation, the caller's fresh validator must still prove the exact
hover/default action or open-menu row. Context-menu failures attempt an
acknowledged Escape before normal cleanup.

## Supported callers

- `CoordinatedActionInterface` converts SafetyGate-approved gameplay actions,
  including bank-close Escape, into coordinator intents.
- `LoginPromptHelper` converts only recognized already-authenticated prompts
  into coordinator intents and never submits text.
- `TaskRuntime` creates one coordinator only for explicit live execution.

Replay, dry-run, observation, diagnostics, overlay, and demonstration capture
must not construct the coordinator or open hardware.
