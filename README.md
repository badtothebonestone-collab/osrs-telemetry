# OSRS Telemetry

This repository proves one deliberately narrow vertical slice:

> Lumbridge west ordinary Trees -> Lumbridge Castle bank -> return to Trees.

It is not a general bot framework. The supported design is:

```text
RuneLite plugin -> snapshot -> Observation -> WoodcutBankTask
               -> SafetyGate -> ArduinoActionInterface -> Verifier
```

There is one command surface: `run.cmd`.

## Quick start

Requirements:

- Windows with PowerShell
- Java 21
- Python 3.11 or newer
- RuneLite account/session already configured
- An Arduino HID bridge only when live execution is explicitly requested

Launch the development client:

```powershell
.\run.cmd plugin
```

If the saved session stops at a recognized idle-disconnect OK, Play Now, or
Click here to play screen, the bounded Arduino-only helper can advance it
without entering any credentials:

```powershell
python -m pip install -r requirements.txt
.\run.cmd login COM6
```

Unrecognized, ambiguous, Continue, credential, and MFA screens
fail closed and require manual handling.

In another terminal, prove that the game scene is observable:

```powershell
.\run.cmd observe
```

The command exits successfully only for a fresh, loaded scene. Its JSON output
contains the player location, inventory/log count, nearby-object count, menu
count, bank state, timestamp freshness, and tick.

Ask the task engine for its first action without sending input:

```powershell
.\run.cmd task
```

Live execution is opt-in and Arduino-only:

```powershell
python -m pip install -r requirements.txt
.\run.cmd execute COM6
```

No software mouse/keyboard fallback exists. The live runner is bounded, stops
after one cycle, and fails closed when observation, geometry, target identity,
hover menu, cleanup, or verification evidence is missing.

Run all tests with the client closed:

```powershell
.\run.cmd test
```

Replay the committed deterministic cycle fixture without RuneLite or Arduino:

```powershell
.\run.cmd replay
```

The replay drives the final task FSM through 28 verified log gains, the fixed
outbound route, bank open/deposit/close, the fixed return route, and `COMPLETE`.
It is a sanitized semantic regression derived from the bounded live proof; it
does not pretend that the ignored live traces contain full raw observations.

## Repository map

- `src/main/java/com/osrstelemetry/`: RuneLite sensor and read-only snapshot endpoint.
- `osrs_bot/model.py`: immutable observation and action contracts.
- `osrs_bot/observation.py`: the only snapshot-to-Observation adapter.
- `osrs_bot/task.py`: the only supported task state machine.
- `osrs_bot/safety.py`: pre-move and post-move validation.
- `osrs_bot/action.py`: the only live input pathway.
- `osrs_bot/verification.py`: the only post-action verifier.
- `osrs_bot/runtime.py`: bounded orchestration of those components.
- `osrs_bot/login.py`: bounded saved-session prompt assistance, outside the task engine.
- `arduino/ArduinoHIDBridge/`: retained HID firmware.
- `tests/`: focused Python tests for the active baseline.
- `tests/fixtures/golden_lumbridge_cycle.json`: sanitized cycle provenance,
  route contract, and deterministic replay facts.
- `docs/ARCHITECTURE.md`: contracts and state transitions.
- `docs/RESCUE_AUDIT.md`: archaeology findings and removal rationale.

## Current limits

- Only ordinary Logs (item `1511`) are supported; other inventory items fail closed.
- The character must have a usable axe equipped.
- Bank PIN entry, credential entry, recovery planning, and generalized tasks are out of scope.
- Login assistance covers only the recognized idle-disconnect OK, saved-session Play Now,
  and Click here to play prompts.
- The physical cycle was proven through bounded continuation runs while the
  route was being hardened. That evidence is stitched rather than one
  uninterrupted process, and it did not retain complete raw Observation frames.
- The committed golden replay freezes the final-code FSM and verification
  sequence; it is not a replacement for future bounded live regression proof.
- The current task performs exactly one full-inventory bank cycle and stops.

See `docs/RESCUE_CONTRACT.md` before extending the product.
