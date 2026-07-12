# Operator GUI Quick Start

The operator GUI is the normal way to run the stabilized OSRS engine. It is a
small Windows desktop application over the existing `EngineApplication` and
`EngineFrame`; it does not select targets, run safety checks, verify actions,
or control the Arduino itself.

## Start the application

From the repository folder, run:

```powershell
.\run.cmd gui
```

The window has four tabs: **Run**, **Live Status**, **Demonstrations**, and
**Diagnostics**. Only one GUI instance is allowed at a time.

## Readiness indicators

The Run tab shows text and color for each preflight item:

- **Repository / application:** the checkout and operator application are
  readable.
- **RuneLite found:** a visible RuneLite client exists.
- **Endpoint healthy:** the existing snapshot endpoint on port 8893 answered.
- **Loaded scene:** RuneLite reports a logged-in, playable game scene.
- **Coherent fresh Observation:** freshness, wall-clock freshness, and source
  coherence all pass on the same Observation.
- **Supported 175% fixed layout:** the current proven canvas and client sizes
  match exactly. Other layouts are not yet claimed as supported.
- **Exact process / session binding:** the visible RuneLite window belongs to
  the exact telemetry PID and session.
- **Arduino port available:** the selected COM port is present.
- **Arduino lease available:** another engine process does not own the shared
  input lease. This check never opens the port.
- **Overlay:** disabled, starting, active, or failed.
- **Current blocker:** plain English followed by the exact engine code.
- **Latest cleanup:** safe, unresolved, or not required for Observe Only.

`UNKNOWN` means the application does not yet have enough evidence. It does not
mean ready.

The badge at the top is a presentation classification over the application,
connection, and latest EngineFrame facts: **CONNECTING**, **READY**,
**OBSERVING**, **RUNNING**, **PAUSED**, **COMPLETE**, **BLOCKED**,
**SAFE_STOPPED**, **DISCONNECTED**, **STALE**, or **ERROR**. The screen also
shows the underlying run ID, EngineFrame stage/task status, source tick,
PID/session, last live update, and frame age; the friendly badge never replaces
the original blocker or diagnostic code.

## Connect RuneLite

1. Select **Launch / Connect RuneLite**.
2. If the exact client is already running, the GUI connects to it and refuses
   to launch a duplicate.
3. Otherwise, the GUI uses the existing `run.cmd plugin` launch path and waits
   boundedly for endpoint and process binding.
4. The GUI refreshes the existing connection status automatically. **Refresh
   Status** remains available when an immediate operator check is useful.

**Login / Recover Session** invokes the existing saved-session login helper.
It can handle only the supported authenticated prompts and uses the configured
Arduino port through `InputCoordinator`. It never types credentials, MFA, or a
bank PIN. Handle those sensitive surfaces yourself, then refresh status.

## Current profile

The only supported profile is shown exactly as the catalog reports it:

- task: `woodcut_bank`;
- definition: `lumbridge_west_trees_v1`;
- resource: ordinary Tree (`1276`);
- site: Lumbridge west Trees to Lumbridge Castle bank and return;
- bank: Bank booth;
- goal: one complete bank cycle;
- profile: `default_lumbridge_west_trees_v1`.

Resource, area, bank, and goal are display-only because no other validated
choice exists. The authoritative profile validator runs again before every
start.

## Observe Only

1. Leave **Observe Only — no gameplay input** selected.
2. Select **Start**.
3. Watch **Live Status** for the exact latest EngineFrame.

Observe Only never sends gameplay input and never opens an Arduino session.
Cleanup therefore displays **Not required in Observe Only**, not failure.

## Start Live

1. Select **Start Live — Arduino production input**.
2. Choose the real Arduino COM port.
3. Confirm the profile, task/site, port, action/runtime limits, loaded-scene
   state, and current blocker in the single confirmation.
4. Select **Yes** to start.

**Start Live** remains disabled until the existing endpoint reports one loaded,
coherent Observation that is still inside its authoritative source-age limit
and is bound to one exact current RuneLite PID/session. A newly assembled GUI
update does not make an old Observation fresh. After an endpoint, process, or
session change, wait for the new identity and a fresh loaded Observation; the
old frame remains historical and cannot satisfy the gate.

For Start Live and Resume, the GUI first focuses only the exact
telemetry-owning RuneLite window and waits briefly for foreground proof. A
failed handoff blocks instead of sending gameplay input.

The GUI sends only the high-level start command. Production actions still pass
through the task FSM, SafetyGate, `InputCoordinator`, Arduino, typed verifier,
and authoritative cleanup.

## Pause, Resume, and Safe Stop

- **Pause** first shows *requested*. It becomes *paused* only after the runtime
  acknowledges a no-input boundary.
- **Resume** uses the exact current run ID and refuses a RuneLite PID/session
  different from the one bound to that run. Restart only after fresh explicit
  reconciliation; an old run is not attached to a replacement client.
- **Safe Stop** is cooperative. If an action is in flight, the GUI waits for
  its input transaction, verification, typed transition, and cleanup.
- **Safe Stop completed** means the engine reached a terminal safe-stop state.
  For a connected input transaction, the latest receipt must prove
  `STOP_ALL`, `DISARM`, safe zero-held `STATUS`, zero unresolved commands, and
  closed ledger/backend.

Closing the window during a run requests the same Safe Stop and leaves the GUI
visible while it waits. A bounded timeout is shown as an unresolved failure;
the GUI never silently kills the worker.

## Current, historical, and terminal status

- **Current live frame** means run ID, PID/session, endpoint health, loaded
  scene, coherence, and source age all still match. Only this category may
  show live target geometry.
- **Last known frame** is retained text from an older, stale, disconnected, or
  identity-mismatched frame. Its target, safety, verification, and location are
  labeled historical and do not authorize a current action.
- **Terminal summary** retains COMPLETE, BLOCKED, SAFE_STOPPED, or ERROR reason,
  final typed outcome, and final cleanup after target geometry is cleared.

**DISCONNECTED** means the endpoint, process, exact identity, or loaded scene
is unavailable. **STALE** means the displayed Observation aged beyond the same
source-freshness contract used by the engine, even if no new HTTP response
arrived. Both states disable Start Live and clear actionable overlay geometry.
Use **Clear Historical Display** to remove retained Last known frame/target
detail; a compact receipt-only terminal summary remains. It does not mutate
engine truth. **Keep terminal summary visible** controls GUI text retention and
never keeps target geometry active.

## Passive overlay

Enable **Passive EngineFrame overlay** on the Run tab. The existing overlay
draws only the current EngineFrame:

- green: selected target;
- amber: eligible alternatives;
- optional red: rejected candidates;
- text: current state, safety, verification, outcome, and blocker.

Disabling or failing the overlay does not stop or change engine control.
For a fresh current OBSERVING/RUNNING frame, the overlay may draw the existing
target/candidate rectangles. PAUSED is text-only. STALE or DISCONNECTED clears
all rectangles and shows an age-bearing text banner. COMPLETE, BLOCKED,
SAFE_STOPPED, or ERROR clears all rectangles immediately, shows a compact
terminal banner for a bounded period, and then leaves only a non-target
terminal indicator. Closing the GUI or disabling the overlay stops its sole
window/worker; terminal cleanup remains in the GUI status even after that.

## Demonstrations

1. Open **Demonstrations**.
2. Enter a short name and maximum duration.
3. Select **Record Demonstration**.
4. Perform the short manual action you want recorded.
5. Select **Stop Recording**.

Recording and automation are mutually exclusive. Recording is read-only and
does not inject input. To review an existing artifact, select **Inspect
Demonstration** and choose its directory. The trusted existing inspector shows
validity, semantic timeline, interacted entities, movement facts, gaps,
ambiguities, and review-only suggestions. It never replays coordinates or
activates a suggestion.

## Diagnostics and evidence

The Diagnostics tab shows commit and schema versions, Python, Java/Gradle,
RuneLite PID/session, Arduino/lease state, and latest artifact paths.

- **Run Quick Self-Test** runs a bounded focused contract suite.
- **Run Golden Replay** invokes the existing committed replay.
- Detailed logs stay in `_run_proofs/gui_diagnostics/<timestamp>/`.
- GUI acceptance screenshots and summaries stay in
  `_run_proofs/gui_acceptance/<timestamp>/`.
- Demonstrations stay in `demo_runs/<timestamp>_<name>/`.

Use **Copy Current Status**, **Save Current Status**, and **Open Latest Proof
Folder** on Live Status when support evidence is needed.

## Common blockers

- **Disconnected:** launch or reconnect RuneLite, then wait for an exact new
  PID/session and one fresh coherent loaded Observation.
- **Stale frame:** do not use the last-known target or safety result; wait for a
  new in-contract Observation.
- **RuneLite is not running:** use Launch / Connect RuneLite.
- **Log in and enter a loaded scene:** finish saved-session setup, then Refresh
  Status.
- **Bring RuneLite to the foreground:** Start Live and Resume make one bounded
  exact-window focus attempt; if it fails, focus that client and refresh.
- **Current client layout is unsupported:** use the retained 175% fixed-client
  layout; do not treat another size as proven.
- **Arduino COM port is unavailable:** select a port that appears in
  Diagnostics.
- **Another process owns the Arduino lease:** safely stop that owner first.
- **Bank PIN or ambiguous interface detected:** resolve it manually; the engine
  will not enter a PIN.
- **Cursor could not be safely reacquired:** leave RuneLite foreground and
  retry only after current geometry is stable.
- **Safe Stop is still finishing:** wait for transaction, verification, and
  cleanup.
- **Cleanup could not be confirmed:** do not assume the device is safe; inspect
  the receipt and proof log.
