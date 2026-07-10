# Rescue Contract

> Historical baseline contract. This document freezes the proven regression
> slice. `docs/PRODUCT_VISION.md`, `PLANS.md`, and `docs/ENGINE_STATUS.md` now
> govern active modular-engine development.

## Product

Prove one reliable cycle:

1. Observe a fresh loaded RuneLite scene near Lumbridge west ordinary Trees.
2. Select an exact `Tree` with exact `Chop down` semantics.
3. Chop and verify an increase in ordinary Logs (item `1511`).
4. Repeat until the 28-slot inventory is full.
5. Follow one fixed route to the Lumbridge Castle bank on plane 2.
6. Open the exact bank booth, deposit an all-log inventory, and verify zero logs.
7. Close the bank, follow the fixed return route, and stop after one cycle.

This is not a generalized bot framework.

## Architecture

```text
RuneLite Plugin
    -> Telemetry Snapshot
    -> Observation
    -> WoodcutBankTask
    -> SafetyGate
    -> ArduinoActionInterface
    -> Verifier
```

Each arrow has one production implementation. Raw plugin payloads stop at the
observation adapter. The task cannot send input or verify its own actions.

## Safety invariants

- A live action requires a fresh `PASS` observation from a loaded game scene.
- Object actions require exact key, name, ID, option, and live screen geometry.
- The cursor move is Arduino HID only.
- A fresh post-move observation must expose the exact expected hover menu.
- Connected attempts always end with `STOP_ALL`, `DISARM`, and port close.
- Every sent action has one bounded, later-observation verification.
- Bank PINs, credentials, MFA, non-log deposit-all, and uncertain route objects
  are terminal blockers.

## Extension rule

Improve this vertical slice before adding another task. New work should remove
a proven limitation, preserve the single pathway, and include focused tests plus
read-only or bounded live evidence appropriate to its risk.
