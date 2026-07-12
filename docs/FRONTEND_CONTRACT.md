# Frontend and Application Facade Contract

`EngineApplication` is the one thin composition boundary for the desktop
frontend. It constructs the existing task/runtime path and exposes immutable
catalog, lifecycle, diagnostic, statistics, blocker, and demonstration values.
It does not select targets, interpret routes, run safety checks, verify actions,
or own input.

The implemented Tkinter/ttk GUI runs in-process through `run.cmd gui`. It adds
no service, IPC protocol, plugin loader, web server, or long-lived command
daemon.

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

The additive EngineFrame presentation values now retain exact same-Observation
game state, loaded-scene/focus/freshness/coherence, player location/plane, and
immutable inventory. Task-owned evidence additionally retains current route
step, simultaneous route/cycle progress, and selected target world
location/distance. The GUI formats these values; it never queries or
reconstructs them independently.

## Presentation lifecycle

The frontend derives one immutable presentation-only classification from the
current `ApplicationSnapshot`, its run ID, the accepted latest `EngineFrame`,
the latest accepted `ConnectionSnapshot`, and current time:

```text
CONNECTING READY OBSERVING RUNNING PAUSED COMPLETE BLOCKED SAFE_STOPPED
DISCONNECTED STALE ERROR
```

This classifier owns no worker, endpoint, lifecycle transition, task state, or
control decision. Every view retains the underlying lifecycle, run/frame
association, EngineFrame stage/task status, PID/session, source tick/capture
time and age, runtime reason, blocker/diagnostic, typed outcome, and cleanup.
It may decide only whether evidence is current, historical, terminal, eligible
for display geometry, or sufficient to enable Start Live.

Frame age and connection age are evaluated separately against the exact
`maxSourceAgeMillis` carried by their Observation facts. A frame becomes STALE
as wall time advances even if no later HTTP response arrives. A freshly rendered
widget or a fresh connection probe cannot refresh the older frame.

Current live evidence requires matching run ID, exact PID/session, healthy
endpoint, loaded coherent Observation, and in-contract frame/connection age.
Anything else is retained only as a clearly labeled Last known frame. A newer
fresh connection may become READY while an older terminal frame remains a
separate historical summary; the old frame cannot overwrite the new connection
or restore geometry.

Start Live is allowed only from a fresh, loaded, coherent, exact current
PID/session connection. An active run pins its first exact coherent
PID/session. Runtime blocks if later Observations change that identity, and
Resume refuses a replacement identity. Reconnection therefore requires an
explicit new run after a fresh coherent Observation rather than attaching the
old run to a new client.

Terminal COMPLETE, BLOCKED, SAFE_STOPPED, and ERROR presentations retain the
runtime reason, final typed outcome, exact receipt counters, and cleanup proof.
They never retain active target geometry. `Keep terminal summary visible` may
retain that GUI text; `Clear Historical Display` clears only frontend-retained
frame/target detail while a bounded receipt-only terminal summary remains; it
cannot alter the application, runtime, receipt, or cleanup owners.

## Operator services

High-level operator operations remain beneath `EngineApplication`:

```python
app.refresh_connection()
app.launch_or_connect_runelite()
app.set_arduino_port("COM6")
app.login_or_recover()
app.arduino_readiness("COM6")
app.prepare_live_handoff()
app.set_overlay_enabled(True)
app.overlay_snapshot()
app.inspect_demonstration(path)
app.diagnostics()
app.run_quick_self_test()
app.run_golden_replay()
```

They reuse the one ObservationClient, the existing `run.cmd plugin` launch,
saved-session `LoginPromptHelper`/`InputCoordinator`, passive overlay, trusted
demonstration inspector, and public test/replay commands. The GUI controller
does not import login, task, safety, verification, overlay, Arduino, transport,
or raw-input modules. Readiness checks never open the Arduino port, and
operator status is not input authorization.

Immediately before Start Live and Resume, `prepare_live_handoff()` binds and
focuses only the exact telemetry-owning RuneLite process/root window, then
waits boundedly for foreground telemetry. It neither moves/resizes RuneLite nor
sends gameplay input. Start Live additionally requires the presentation
freshness gate before and after handoff. Resume requires the handoff identity to
equal the active run binding. A failed or changed handoff blocks the GUI
operation. Pause and Safe Stop set the current runtime control synchronously
before their result-adapter workers wait, so clicking the GUI cannot race the
worker's foreground check.

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

## Implemented GUI screen

The screen consumes these exact contracts:

- **Task dropdown:** values from `list_tasks()`.
- **Task/site definition dropdown:** definitions filtered by selected task.
- **Resource/tree and bank/location:** display the selected definition; keep
  disabled because the current profile schema exposes no choices.
- **Goals:** show cycle goal `1`; hide or disable duration, target level, item
  count, and other unsupported goals until a validated profile field exists.
- **Arduino port:** runtime configuration shown only for execute mode.
- **Start:** revalidate profile and runtime configuration, focus the exact
  RuneLite binding for live mode, require a fresh coherent loaded Observation,
  then retain `run_id` and the bound PID/session.
- **Pause/Resume:** send only the current `run_id`; distinguish requested from
  acknowledged pause, and repeat the exact focus handoff before live Resume.
- **Safe Stop:** send only the current `run_id` and display requested state until
  `SAFE_STOPPED` or another terminal result.
- **Live state and safety:** render exact EngineFrame sequence, task state,
  selected/eligible/rejected evidence, ordered safety checks, pending/last
  verification, blocker, receipt, and cleanup; label non-current evidence as
  Last known rather than live.
- **Overlay toggle:** requests only the existing passive overlay owner, bound to
  the current run's EngineFrame publisher. Fresh live frames may show existing
  geometry; stale, disconnected, terminal, identity-mismatched, or old-run
  frames are text-only. It never changes engine control.
- **Record/End Demonstration:** use `capture_id` and remain mutually exclusive
  with automation.
- **Recent run summary:** render stored runtime statistics/result, not a frame
  reconstruction.

After restart, the GUI must never restore an active run ID, capture ID, pending
verification, old coordinates/clickboxes, menu sample, source tick, session
target, or input state. It must reobserve and revalidate.

Long work runs on non-daemon worker threads behind a result queue. Only the Tk
thread applies results to widgets. Per-channel generation tokens discard stale
callbacks; monotonic run/capture IDs and publisher-local frame sequence reject
older snapshots, including delayed callbacks after a new run. Connection
snapshots are retained monotonically by their existing capture time. Important
events are bounded to 300 entries.

The ignored `.osrs-telemetry/gui-settings.json` stores only revalidated profile
ID, Arduino port, overlay preference, terminal-summary visibility, window
geometry, and last demonstration directory. It never stores a run/capture ID,
frame, PID/session, target, or geometry. A Windows process mutex prevents a
second operator GUI instance.
Closing during a run requests cooperative Safe Stop and waits up to the bounded
frontend shutdown interval; it never kills an unresolved engine worker.

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
