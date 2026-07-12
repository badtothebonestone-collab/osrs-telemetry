# OSRS Telemetry

This repository builds a small OSRS-specific automation engine around one
proven regression baseline:

> Lumbridge west ordinary Trees -> Lumbridge Castle bank -> return to Trees.

It is not a general bot framework, planner, task language, learned policy, or
anti-detection system. The current engine shape is:

```text
RuneLite plugin -> atomic SensorFrame -> snapshot v2 -> Observation
               -> validated default Profile + Lumbridge definition
               -> Task protocol -> explicit WoodcutBankTask FSM
               -> Action + typed constraints -> SafetyGate
               -> InputCoordinator -> Arduino -> Verifier -> typed Outcome
               -> immutable EngineFrame -> optional passive overlay
manual control -> read-only demo evidence -> verified review suggestions
thin EngineApplication facade -> catalog/profile/lifecycle/status/demo contracts
```

There is one command surface: `run.cmd`.

## Quick start

Requirements:

- Windows with PowerShell
- Java 21
- Python 3.11 or newer
- RuneLite account/session already configured
- An Arduino HID bridge only when live execution is explicitly requested

Launch the operator desktop application:

```powershell
.\run.cmd gui
```

The GUI presents the real one-option profile, RuneLite connection and preflight,
Observe Only and explicit Start Live modes, tokenized Pause/Resume/Safe Stop,
the passive EngineFrame overlay, demonstration recording/inspection, and
bounded diagnostics. Start Live still uses the existing Arduino-only
`InputCoordinator`; the GUI has no direct input authority. See
[`docs/QUICKSTART.md`](docs/QUICKSTART.md) for the non-programmer walkthrough.

The following commands remain useful diagnostic surfaces. Launch the
development client directly with:

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

The command exits successfully only when the current baseline checks report a
fresh, loaded scene. Its JSON output contains the player location,
neutral inventory items, nearby-object count, menu count, bank state, source and
assembly timestamps, frame identity/coherence, and tick. Freshness is derived
from the RuneLite capture time rather than the HTTP response time.

Ask the task engine for its first action without sending input:

```powershell
.\run.cmd task
```

Live execution is opt-in and Arduino-only:

```powershell
python -m pip install -r requirements.txt
.\run.cmd execute COM6
```

Add the passive, click-through diagnostics without changing engine control:

```powershell
.\run.cmd task --overlay
.\run.cmd execute COM6 --overlay
```

Record a short manual route or interaction without injecting input, then verify
and summarize the portable artifact:

```powershell
.\run.cmd record-demo castle-stairs --duration-seconds 45
.\run.cmd inspect-demo .\demo_runs\20260710T170000000000Z_castle-stairs
```

The recorder uses the same snapshot endpoint, writes hashed JSONL/manifest/
timeline/screenshots, and emits only review-required semantic suggestions. It
never replays coordinates or activates task data. See
`docs/DEMONSTRATIONS.md` for the evidence and trust boundary.

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

Inspect the frontend-ready catalog/profile contract or run the same engine
through the thin foreground facade:

```powershell
.\run.cmd app catalog
.\run.cmd app profile-schema
.\run.cmd app validate-profile
.\run.cmd app run
# Explicit Arduino live mode:
.\run.cmd app run --execute --arduino-port COM6
```

`Ctrl+C` during `app run` requests safe stop and waits for any in-flight action
cleanup plus typed verification. In-process pause/resume and manual-demo
begin/end are also consumed by the GUI. There is still no daemon, web server,
or IPC service.

## Repository map

- `src/main/java/com/osrstelemetry/`: RuneLite sensor and read-only snapshot endpoint.
- `osrs_bot/model.py`: immutable observation and action contracts.
- `osrs_bot/observation.py`: the only snapshot-to-Observation adapter.
- `osrs_bot/definition.py`: the one immutable/versioned built-in task/site definition.
- `osrs_bot/profile.py`: the minimal validated profile and default binding.
- `osrs_bot/configuration.py`: bounded machine/session runtime configuration.
- `osrs_bot/task_contract.py`: the minimal task/runtime protocol.
- `osrs_bot/task.py`: the only supported explicit task state machine.
- `osrs_bot/safety.py`: non-overridable engine invariants followed by typed
  task-constraint validation.
- `osrs_bot/action.py`: gameplay validation and typed intent construction.
- `osrs_bot/input_coordinator.py`: sole Arduino session owner and immutable
  command/ACK/cleanup receipts for gameplay and login.
- `osrs_bot/pointer.py`: pure deterministic bounded relative-motion policy.
- `osrs_bot/verification.py`: the only post-action verifier and typed outcomes.
- `osrs_bot/runtime.py`: task-agnostic bounded orchestration.
- `osrs_bot/application.py`: tokenized thin composition/lifecycle facade.
- `osrs_bot/application_cli.py`: catalog/profile and foreground facade CLI.
- `osrs_bot/gui_controller.py`: thread-safe, facade-only GUI operation boundary.
- `osrs_bot/gui.py`: Tkinter/ttk operator presentation with no domain authority.
- `osrs_bot/operator_services.py`: bounded launch/preflight/login/overlay and
  diagnostic delegation beneath the application facade.
- `osrs_bot/engine_frame.py`: the immutable latest runtime diagnostic truth.
- `osrs_bot/debug_overlay.py`: optional passive EngineFrame-only Windows overlay.
- `osrs_bot/demonstration.py`: bounded read-only recorder and tamper-verifying
  semantic inspector.
- `osrs_bot/screen_capture.py`: read-only, verified-canvas screenshot crops.
- `osrs_bot/vision.py`: frozen advisory-only future evidence type; no model.
- `osrs_bot/login.py`: bounded saved-session prompt assistance, outside the task engine.
- `arduino/ArduinoHIDBridge/`: retained HID firmware.
- `tests/`: focused Python tests for the active baseline.
- `tests/fixtures/golden_lumbridge_cycle.json`: sanitized cycle provenance,
  route contract, and deterministic replay facts.
- `docs/PRODUCT_VISION.md`: governing product scope and future user experience.
- `docs/ARCHITECTURE.md`: contracts and state transitions.
- `docs/SENSOR_CONTRACT.md`: atomic frame, freshness, geometry, and menu provenance.
- `docs/TASK_CONTRACT.md`: minimal task seam, typed outcomes, and safety ownership.
- `docs/DEFINITIONS_AND_PROFILES.md`: definition/profile/configuration ownership.
- `docs/INPUT_COORDINATOR.md`: sole input owner, pointer, receipt, and cleanup contract.
- `docs/ENGINE_FRAME.md`: diagnostic publication and passive overlay contract.
- `docs/DEMONSTRATIONS.md`: artifact, inspection, and no-replay contract.
- `docs/FRONTEND_CONTRACT.md`: implemented facade and future GUI screen contract.
- `docs/QUICKSTART.md`: non-programmer operator GUI guide.
- `docs/ENGINE_STATUS.md`: completed milestone, evidence boundary, and blockers.
- `PLANS.md`: active phases, acceptance criteria, and decision log.
- `docs/RESCUE_AUDIT.md`: archaeology findings and removal rationale.

## Current limits

- Only ordinary Logs (item `1511`) are supported; other inventory items fail closed.
- The character must have a usable axe equipped.
- Bank PIN entry, credential entry, recovery planning, and generalized tasks are out of scope.
- Login assistance covers only the recognized idle-disconnect OK, saved-session Play Now,
  and Click here to play prompts.
- The original physical baseline was proven through bounded continuation runs
  while the route was being hardened. That historical evidence is stitched and
  did not retain complete raw Observation frames. A separate 2026-07-11
  current-checkpoint process later completed the default profile uninterrupted
  in 82 actions with safe terminal cleanup; its compact artifact retains only
  the final gameplay transaction rather than every intermediate receipt.
- The committed golden replay freezes the final-code FSM and verification
  sequence. It complements, rather than replaces, the bounded live proof.
- The current task performs exactly one full-inventory bank cycle and stops.
- Exactly one built-in definition/default profile is supported; there is no
  user-editable profile loader or second site.
- Raw mouse-button and keyboard transitions are not available from RuneLite;
  demonstration manifests state that gap and retain semantic menu-click proof.
- A demonstration is one observed evidence bundle, not proof of a generally
  correct route and never an executable replay.
- The facade supports exactly one task/definition/profile. Resource/bank choices
  and alternate goals remain disabled because the schema does not expose them.
- The first Tkinter operator GUI is implemented over `EngineApplication` and
  `EngineFrame`. There is no daemon/IPC surface, vision model, or LLM runtime.
  The `VisionEvidence` type is a non-authoritative future seam only.

Read `docs/PRODUCT_VISION.md`, `PLANS.md`, and `docs/ENGINE_STATUS.md` before
extending the engine. `docs/RESCUE_CONTRACT.md` remains the frozen baseline
contract, not the active development phase.
