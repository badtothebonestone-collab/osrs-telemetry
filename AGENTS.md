# Engineering Rules

## Governing context

Read in this order:

1. `AGENTS.md`
2. `docs/PRODUCT_VISION.md`
3. `PLANS.md`
4. `docs/ENGINE_STATUS.md`
5. `docs/ARCHITECTURE.md`
6. `docs/SENSOR_CONTRACT.md`
7. `docs/TASK_CONTRACT.md`
8. `docs/DEFINITIONS_AND_PROFILES.md`

`docs/RESCUE_CONTRACT.md` freezes the proven regression slice; it is no longer
the active development phase.

## Product boundary

Build a small, modular, OSRS-specific automation engine. The only implemented
task/site remains Lumbridge west ordinary Trees -> Lumbridge Castle bank ->
return. New flexibility comes from validated profiles and immutable task/site
definitions, while every task retains its own explicit FSM.

Never introduce a generic game-agent framework, planner, task language, task
DSL, behavior-tree framework, knowledge fabric, learned control policy,
automatic learning, MCP surface, compatibility layer for deleted architecture,
or second telemetry endpoint. Do not add another task or site during the active
mission.

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
- one immutable diagnostic `EngineFrame` with no control authority.

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
- Require exact target identity, verified canvas geometry, exact post-move menu
  evidence, and a later typed verification for every action.
- Every connected attempt must end with confirmed `STOP_ALL`, `DISARM`, and
  authoritative safe firmware status.
- Fail closed. Missing proof is not permission to add a fallback.
- Dry-run, replay, overlay, diagnostics, and demonstration capture must not
  inject input or open hardware sessions.

`run.cmd login COMx` may click only the retained idle-disconnect OK,
saved-session Play Now, and Click here to play surfaces. All other
login/recovery surfaces fail closed.

## Architecture and development

- Use `run.cmd` as the public entrypoint until the application facade replaces
  it without duplicating engine logic.
- Everything downstream of the plugin consumes immutable engine contracts; do
  not read plugin caches or raw response dictionaries elsewhere.
- Keep task logic, safety, RuneLite parsing, Arduino control, verification, and
  state ownership out of future GUI/overlay code.
- Runtime control may use task contracts but must not know concrete task phases,
  item IDs, route facts, or mutable progress internals.
- Demonstrations and historical runs are append-only evidence. They may suggest
  reviewed definition/fixture changes but never authorize input or raw replay.
- An LLM may read immutable definitions and evidence offline; it must never emit
  runtime input or bypass the FSM, safety gate, input coordinator, or verifier.
- Prefer deletion and direct OSRS-specific code over speculative abstraction.
- Preserve the Arduino firmware/backend unless hardware evidence proves a
  change is necessary.

## Work and validation discipline

- Keep `PLANS.md` and `docs/ENGINE_STATUS.md` current after every milestone.
- One phase, one coherent diff, one checkpoint commit. Do not push unless asked.
- Run focused tests, the full Python/Java suites, and `run.cmd replay` after each
  phase. Inspect the diff for prohibited expansion before committing.
- Use live validation only for evidence replay cannot provide. Keep it bounded;
  after a repeated equivalent failure, preserve evidence and patch the failing
  boundary before another run.
- After any live input, prove cleanup with `STOP_ALL`, `DISARM`, and safe status.

With RuneLite closed:

```powershell
.\run.cmd replay
.\run.cmd test
```

Read-only live commands are `run.cmd observe` and `run.cmd task`. Gameplay is
explicitly opt-in through `run.cmd execute COMx`.
