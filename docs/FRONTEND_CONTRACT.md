# Frontend and Application Facade Contract

`EngineApplication` is the one thin composition boundary for a future desktop
frontend. It constructs the existing task/runtime path and exposes immutable
catalog, lifecycle, diagnostic, statistics, blocker, and demonstration values.
It does not select targets, interpret routes, run safety checks, verify actions,
or own input.

There is no GUI, service, IPC protocol, plugin loader, or long-lived command
daemon in this milestone.

## Catalog and profile contract

The catalog contains exactly one task and one definition:

- task: `woodcut_bank`;
- definition: `lumbridge_west_trees_v1`.

The profile schema exposes only:

- `profileId`: validated lowercase identifier;
- `definitionId`: exactly `lumbridge_west_trees_v1`;
- `cycleGoal`: exactly `1`.

`profileMayOverrideEngineInvariants` is always false. Endpoint, auth token,
Arduino port, polling, and hard runtime bounds remain `RuntimeConfig`, not
profile fields. Start validates the profile again through `Profile` and
`bind_builtin_profile`; inspecting the schema never authorizes a later start.

The in-process contract is:

```python
app.list_tasks()
app.list_definitions("woodcut_bank")
app.profile_contract("woodcut_bank", "lumbridge_west_trees_v1")
app.validate_profile(values)
```

There is no discovery, dynamic loader, external profile file, second task, or
generic task schema.

## Run lifecycle

Start is asynchronous and creates a fresh task, runtime, frame publisher, and
control for every run:

```python
started = app.start(profile_values=values, execute=False)
run_id = started.run_id
```

Every command must carry that exact `run_id`:

```python
app.request_pause(run_id)
app.resume(run_id)
app.request_safe_stop(run_id)
finished = app.wait(run_id)
```

A delayed command for an older run is rejected and cannot affect a newer run.
Two concurrent starts cannot create two workers.

Pause and safe stop are cooperative:

- a request is visible as `pause_requested` or `safe_stop_requested`;
- `paused` means the runtime acknowledged a no-input boundary;
- an Observation held across a pause is discarded and re-fetched before task
  decision;
- once a task emits an executable decision, decision, Arduino transaction,
  cleanup receipt, bounded verification, and typed task transition remain one
  indivisible unit;
- a stop requested during that unit is acknowledged only after verification;
- pause cannot extend `max_runtime_seconds`;
- safe stop while paused wakes the worker;
- no extra Arduino connection is opened merely to stop: every completed input
  transaction already requires `STOP_ALL -> DISARM -> STATUS` cleanup.

`SAFE_STOPPED` is a successful operator-requested termination, distinct from
task `COMPLETE`. It never means a pending verification was abandoned.

## Read surfaces

`read_engine_frame()` returns the exact object from
`EngineFramePublisher.latest()`. The facade does not copy, recalculate, or
reinterpret target, safety, verification, receipt, cleanup, or blocker facts.

`read_statistics()` returns the runtime-owned immutable counter snapshot:

```text
active, status, reason, observations, actions, lastTick
```

Counts update at the runtime's actual observation/action mutations and close on
every terminal result or unexpected worker error. They are not inferred from
EngineFrame sequence numbers.

`read_blockers()` reports exact current EngineFrame blockers, terminal
error/blocked/limit reasons, or facade worker/capture failures. It does not
derive a blocker from task-state text.

`snapshot()` retains lifecycle references under the application lock and
samples each exact owner-produced frame/statistics/control value. Those owners
publish under their own locks, so the aggregate is a frontend convenience, not
a cross-owner atomic sensor frame. The application lifecycle and EngineFrame
stage are intentionally separate: lifecycle says whether a frontend command is
pending or acknowledged; EngineFrame remains engine diagnostic truth.

## Demonstration lifecycle

Manual demonstration capture and automation are mutually exclusive:

```python
started = app.begin_demonstration("castle-stairs")
capture_id = started.capture_id
finished = app.end_demonstration(capture_id)
```

Every end command requires the exact current `capture_id`. End sets the
recorder's read-only stop predicate, waits boundedly for finalization, and runs
the existing tamper-verifying inspector. It never kills a thread or injects
input. The snapshot retains only a frozen artifact reference and verified
status; full semantic suggestions remain in the inspected artifact.

An active or paused engine run blocks demonstration start, and an active
demonstration blocks engine start. Demonstration inspection can never activate
a profile or definition.

## Minimal CLI

The short-lived CLI exposes honest operations that do not pretend separate
processes can pause one another:

```powershell
.\run.cmd app catalog
.\run.cmd app profile-schema
.\run.cmd app validate-profile
.\run.cmd app run
.\run.cmd app run --execute --arduino-port COM6
```

`app run` stays in the foreground. `Ctrl+C` requests safe stop and waits for the
runtime to acknowledge it; it does not kill an Arduino transaction. In-process
pause/resume is proven by the facade API and reserved for the future GUI.

The existing read-only demonstration commands remain:

```powershell
.\run.cmd record-demo castle-stairs --duration-seconds 45
.\run.cmd inspect-demo .\demo_runs\20260710T170000000000Z_castle-stairs
```

## Future GUI screen

The future screen consumes these exact contracts:

- **Task dropdown:** values from `list_tasks()`.
- **Task/site definition dropdown:** definitions filtered by selected task.
- **Resource/tree and bank/location:** display the selected definition; keep
  disabled because the current profile schema exposes no choices.
- **Goals:** show cycle goal `1`; hide or disable duration, target level, item
  count, and other unsupported goals until a validated profile field exists.
- **Arduino port:** runtime configuration shown only for execute mode.
- **Start:** revalidate profile and runtime configuration, then retain `run_id`.
- **Pause/Resume:** send only the current `run_id`; distinguish requested from
  acknowledged pause.
- **Safe Stop:** send only the current `run_id` and display requested state until
  `SAFE_STOPPED` or another terminal result.
- **Live state and safety:** render exact EngineFrame sequence, task state,
  selected/eligible/rejected evidence, ordered safety checks, pending/last
  verification, blocker, receipt, and cleanup.
- **Overlay toggle (future, not facade v1):** construct or close only the
  existing passive overlay reader; it must not change engine control.
- **Record/End Demonstration:** use `capture_id` and remain mutually exclusive
  with automation.
- **Recent run summary:** render stored runtime statistics/result, not a frame
  reconstruction.

After restart, the GUI must never restore an active run ID, capture ID, pending
verification, old coordinates/clickboxes, menu sample, source tick, session
target, or input state. It must reobserve and revalidate.

## Vision and LLM boundary

`VisionEvidence` is only a frozen dependency-free future seam. It records an
aware capture timestamp, exact window/canvas/crop transform, model/version,
class/confidence, model-space bounds or mask, and occlusion/image-quality
status. It is explicitly non-authoritative and cannot authorize input. No model
dependency, capture loop, or runtime consumer exists.

RuneLite remains authoritative for session/ticks, player location/plane,
inventory, equipment, entity identity/actions, menus/widgets, and game values.
Vision may later supplement or veto a visual condition or propose a point
inside API-confirmed geometry; it may not overwrite those facts.

No LLM participates in the facade or runtime. An offline assistant may read
immutable definitions, demonstration artifacts, run history, and diagnostics,
but cannot issue input, mutate an active profile, or bypass the task FSM,
SafetyGate, InputCoordinator, or Verifier.
