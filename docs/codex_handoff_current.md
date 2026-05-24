# Current Codex Handoff

Repo:
`C:\Users\stone\osrs-telemetry\example-plugin`

New Codex chats should read `AGENTS.md` first, then this file. Do not treat
older chat history as source of truth. Use the current repo, current tests,
current diagnostics, `AGENTS.md`, and this handoff.

## Current Daily Path

Snapshot No-File is the daily path.

Daily daemon command:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

Architecture:

```text
RuneLite plugin
-> plugin snapshot endpoint (127.0.0.1:8893)
-> live_core_daemon.py
-> analyzers
-> Mission Control / diagnostics / overlay
```

## Current Completed Baseline

- Snapshot No-File is the daily path.
- No continuous runtime JSON/NDJSON output should be added.
- Scanner/checker/filter field-name systems were removed and should not return.
- Do not strip useful telemetry fields because of names like `actions`,
  `menuActions`, `clickbox`, `target`, `path`, `interaction`, `destination`,
  or `waypoint`.
- Filtering should only happen for explicit performance, size, display, or
  task-selection reasons.
- Service selection works.
- Service Route Context v1 works for service-needed/no-visible-bank cases. It
  uses low-confidence static route priors plus live telemetry to expose
  a bounded route graph, scouting anchors, visible stair/ladder/door transition
  targets, observed anchors, completed route steps, and expected plane changes.
  Static priors are not truth; live RuneLite telemetry remains authoritative.
- Bank booth wins over Deposit Box when visible.
- Retained Bank booth blocks Deposit Box fallback.
- Collision window cache works.
- OSRS-like path prediction works.
- Tile overlay works.
- Path intent stabilization works.
- Arrival/serviceReady works.
- Bank UI / Service State Context v1 works.
- Bank Operation Context v1 works.
- Return-to-Resource Context v1 works.
- Post-bank World Reacquisition Context v1 works.
- Close-bank Readiness / Return Control Context v1 works.
- Cycle History / State Transition Trace v1 works.
- Resource Return Destination / Resource Area Memory v1 works and has live QA.
- Full Woodcut Bank Cycle QA Harness v1 works.
- Full Cycle Synthetic Scenario Suite v1 works.
- Full Cycle Live QA Runner v1 works.
- Mission Control Cycle Summary v1 works and has live panel QA.
- Unified Input Control Package v1 works.
- Dynamic RuneLite Canvas Geometry v1 works.
- Action Lifecycle Cooldown / Single-Action Loop v1 works.
- Client-tick interaction layer v1 works: the plugin exposes bounded
  `client_tick_hot.v1` state from `ClientTick`, `PostMenuSort`, and
  `MenuOptionClicked`; Python uses `client_tick_core.py` for hover
  confirmation, clicked-menu classification, and `action_trace.v2`.
- Camera-guided waypoint exposure works for service navigation: when a
  navigation waypoint is visually occluded by a foreground object, the executor
  can keep the same target world tile, nudge the camera, re-project that tile,
  move the mouse to the updated projection, and click only after fresh
  `Walk here` hover confirmation.
- Human Input Governor v1 works: `HumanInputController` is the normal live
  motor-output boundary for mouse movement, click timing, camera key holds or
  middle-mouse drag pulses, and bounded reaction timing. Fast client-tick
  perception remains separate from human-paced motor output.
- Intent-aware Readiness v2 works: the readiness report separates overall
  context warnings from `actionReadiness.executionAllowed`, so service-route
  navigation can proceed through route/waypoint checks while resource clicks
  still require selected Tree/Oak/highlighter/safeAimPoint agreement.
- Live liveness recovery is explicit: stale `client_tick_hot.v1` blockers now
  report `gameState`, logged-in status, hot-state ages, a `staleReason` such as
  `login_screen` or `plugin_hot_state_not_advancing`, and a recovery action.
- Lumbridge service routing is staged through west approach, entrance/courtyard,
  first-stairs search, live stair transitions, second-stairs transition, and
  bank service. Route object acquisition now has its own bounded census and
  `route_relevance.v1` gate. A correct visible `Climb-up Staircase` can
  intercept waypoint walking, while random stairs/ladders are kept as
  visible-but-route-irrelevant diagnostics and are not clicked.
- Route-transition dialogue resolving works for staircase prompts. The plugin
  exposes bounded `dialogue_state.v1` from live widgets; Python can propose
  `interface_dialogue_choice_action` after generic `Climb Staircase` opens the
  Lumbridge up/down prompt. The resolver chooses `Climb up the stairs.` for
  `planeChange="+1"` and `Climb down the stairs.` for `planeChange="-1"`,
  preferring number keys through `HumanInputController`.
- Hover-discovered route objects are evidence, not truth. If `client_tick_hot`
  sees `Climb-up Staircase` but scene/projection route relevance is unresolved,
  the action layer records `hover_confirmed_but_route_unresolved` and continues
  with safe route navigation instead of clicking hover alone.
- Service-route waypoint selection is adaptive: open corridor movement can
  choose a farther structured route/path waypoint, while transition/tight
  geometry stays precise. Structured alternates, camera exposure, or a safe stop
  replace arbitrary probing around an occluded tile.
- If a static service-route prior is outside the local collision window,
  pathing now emits a bounded `local_frontier_waypoint` toward that destination
  instead of proposing the far anchor as a no-click executable target. This is
  still only local scouting; if the route source area is wrong or the character
  reaches a wall/fence corridor, `route_wall_hugging_detected` remains a safe
  blocker rather than permission to keep clicking.
- Service routing now has a destination-centered fallback for unexpected
  collection areas. `route_context.v1` distinguishes known west-tree source,
  nearby known source, unmapped source, and wrong-route-source states. When full
  inventory occurs outside the west-tree source but the Lumbridge Castle bank
  anchor is known, the daemon selects `goal_directed_fallback`, chooses a
  service anchor and approach node, and lets pathing expose a reachable local
  frontier waypoint toward that approach.
- Tree/Oak hover near a local frontier while service is needed is treated as a
  service-navigation blocker for that waypoint. It should produce
  tree-hover/volatile-frontier diagnostics, try another local frontier or
  approach when available, or stop safely instead of clicking a resource object
  with a full inventory.
- Degenerate or off-viewport route tile projections are non-actionable even
  when the plugin can compute a world/local tile. A projected `(0,0)` or tiny
  degenerate tile polygon must not become a click target; the executor should
  try structured route/path alternates before stopping safely.
- Route waypoint proposals now expose `route_projection_status.v1`, which
  classifies the projected route tile as visible, offscreen, degenerate, tiny,
  occluded, missing, or non-actionable before canvas execution.
- Edge-safe route clicks are enabled with `--reject-edge-route-clicks`. The
  default live margin is 12 px and the default minimum visible-area ratio is
  0.45; edge-clipped or half-offscreen route tiles are rejected or sent through
  alternate waypoint/camera reacquire rather than clicked directly.
- Service-route execution uses a navigation-in-progress motion lock. After a
  confirmed `Walk here` route click, the loop can wait while pathing still says
  the player is moving or recently moved instead of immediately replanning a
  sideways/backtracking waypoint. Immediate A-B-A waypoint cycles and repeated
  wall-adjacent clicks are reported as route stability skips.
- Menu mismatch safety is explicit: if fresh hover predicts `Walk here` but
  `MenuOptionClicked` reports another action, the result is
  `menu_flip_mismatch`, not navigation progress.
- Volatile navigation hover zones are guarded: route waypoint hover
  confirmation requests a bounded recent `postMenuSortTail`; if the same
  waypoint has recent NPC/object/widget actions near the `Walk here` samples,
  the executor records `volatileHoverZone` and skips the click before
  mouse-down instead of relying on one unstable menu sample.
- RuneLite Dev Bootstrap / Login Flow Helper v2 is implemented. Current live
  run confirms launch, secondary-monitor placement, bounded startup clicks,
  `LOGGED_IN` detection, daemon start/reuse, and live QA handoff. The bootstrap
  waits after `Play Now` for the server transition before clicking the final
  `CLICK HERE TO PLAY` panel.
- Stabilization suite currently passes 158/158 and reports behavior categories.

## Woodcut Bank Cycle Summary

The woodcut_bank loop is modeled from resource collection through service,
banking, close-bank/world reacquisition, and return-to-resource:

- Collect resources until inventory state requires service.
- Select full-bank service targets with Bank booth / banker / bank chest as
  primary targets and Deposit Box / deposit chest as fallback targets.
- If inventory is full near Lumbridge and no service target is visible, use
  `serviceRouteContext` to reason about the multi-plane Lumbridge Castle bank
  route: navigate/scout toward the route anchor, live-confirm stairs with
  `Climb-up` / `Climb up`, verify plane/location changes, reacquire the next route step, and
  then fall back to normal `open_service` once a bank booth/banker is visible.
  Previously observed service anchors can be reused as navigation targets, but
  only visible live service targets or route objects can be clicked.
- At `lumbridge_castle_bank`, bank-floor service acquisition uses its own
  `serviceObjectCensus`: Bank booth, Banker, Deposit box, and Bank chest
  candidates are counted separately from resources and stair/door transitions.
  Route-relevant actionable service objects propose `open_service`; clicks that
  start pathing to the object are held as
  `service_object_pathing_to_object` until the bank/deposit UI opens or a
  bounded no-progress result is observed.
- Preserve retained Bank booth context so a temporary missing booth candidate
  does not incorrectly fall back to Deposit Box.
- Use pathing and arrival/serviceReady context to distinguish walking to bank
  from being ready to interact with the service.
- Observe bank UI state after serviceReady:
  bankOpen, bankReadable, bankPinOpen, widget visibility, close capability, and
  compact inventory/bank summaries.
- Evaluate bank operation state:
  resources held, resource slots/quantity, non-resource item count, deposit
  availability, operationNeeded, operationType, and bankingComplete.
- Protect non-resource inventory during service: deposit-inventory is preferred
  only when the bank operation context reports no non-resource/protected items;
  otherwise select resource/log slots for targeted depositing.
- Targeted resource/log depositing uses bank-side `inventorySlots` widget
  bounds when present and surfaces them as `resourceItemSlotBounds` /
  `resourceItemWidgets`. This lets the loop deposit logs while preserving tools
  and other non-resource inventory. If the bank is already closed and retained
  inventory summary shows zero target resources, keep the bank operation marked
  complete instead of reopening service.
- When bankingComplete=true and bankOpen=true, defer target candidates as
  bank UI still open / close bank needed rather than reporting a resource
  targeting failure.
- A stale `serviceNeeded=true` or visible bank/deposit object at the bank floor
  must not suppress `bankingComplete=true` when the bank operation reports zero
  held target resources and inventory has free slots. Close the bank first;
  do not reopen service after the deposit is complete.
- When bankingComplete=true and bankOpen=false, resume resource targeting.
- If no resource target is visible after banking and valid resource memory
  exists, use the remembered resource area as a return destination.
- If no live resource memory is available after banking, `woodcutting_bank` /
  `woodcut_bank` may use the profile Lumbridge west-tree anchor as a
  low-confidence return destination. This is a route prior only; live telemetry
  still decides each stair, waypoint, and resource reacquisition step.
- Return routing uses `returnRouteContext` to reverse the Lumbridge bank route:
  bank floor stairs down, first-floor stairs down, ground-floor/courtyard exit,
  west approach, then the west-tree resource area. Down-stair transitions expect
  `Climb-down` or the generic up/down staircase dialogue with the down option.
- If a resource target becomes visible, normal target selection wins over the
  remembered return destination.

## Current Diagnostics

One-command live QA:

```powershell
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
```

RuneLite dev bootstrap:

```powershell
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --dry-run
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --start-daemon --run-live-qa
python telemetry-viewer\run_runelite_bootstrap.py --skip-runelite-launch --execute --start-daemon --run-live-qa
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --move-to-secondary-monitor --start-daemon --run-live-qa --print-candidates --timeout-seconds 180
```

The bootstrap helper is only for already-authenticated startup flow. It may
launch `.\gradlew.bat run`, focus a RuneLite/Jagex/Java window, optionally move
the client to the secondary monitor, click deterministic Play/Continue/CLICK
HERE TO PLAY candidates, wait for `LOGGED_IN`, start/reuse the daily daemon,
and run the live QA runner. It must not type passwords, change account settings,
store credentials, select worlds, or change worlds; if a real login/account
confirmation prompt appears, stop and let the user handle it.

Synthetic scenario suite:

```powershell
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --scenario bank_closed_return_memory
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --json
```

Full cycle and history:

```powershell
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
```

Focused live diagnostics:

```powershell
python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting
python telemetry-viewer\diagnose_woodcutting_candidates.py --latest-session --profile woodcutting --top 20 --show-rejections
python telemetry-viewer\diagnose_service_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
python telemetry-viewer\diagnose_bank_ui_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_bank_operation_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_return_to_resource_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_post_bank_reacquisition_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_close_bank_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent
```

Daily gauntlet:

```powershell
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

Plugin snapshot endpoint check:

```powershell
$request = @{
  schema = "plugin_snapshot_request.v1"
  needs = @("baseline", "writer_health")
  maxAgeTicks = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
```

## Synthetic Scenario Suite

`diagnose_woodcut_bank_scenarios.py` validates fixed in-memory states without
RuneLite, a live daemon, sessions, compact packets, or rolling files.

Current scenario names:

- `collecting_resources`
- `inventory_full_needs_service`
- `pathing_to_service`
- `service_ready_bank_closed`
- `bank_open_resources_held`
- `bank_open_after_deposit`
- `bank_closed_return_memory`
- `bank_closed_tree_visible`
- `bank_closed_no_memory_no_target`
- `bank_pin_blocked`
- `retained_booth_blocks_deposit`
- `remembered_return_cross_plane`

## Mission Control

`live_control_panel.py` shows a compact Mission Control cycle summary sourced
from daemon `/health`, `/status`, and `/control` fields. It should display:

- Cycle: cycleStage, phase, activeIntent, stable-for ticks, last transition.
- Inventory: inventory full/free slots and progress.
- Service / Path: selected service target, serviceReady, pathing needed and
  completed, plus service route id/step/action-ready when a route prior is in
  use. Also surface the current route node, next edge type, and completed route
  steps when available.
- Bank: bankOpen, bankReadable, bankPinOpen, operationNeeded, operationType,
  bankingComplete, closeBankNeeded, closeBankReady.
- Return: post-bank reason, return-to-resource reason, resource-return reason,
  returnDestinationAvailable.
- Health: overlay/live QA/gauntlet state when available, warning count, missing
  capability count, and noActionEmitted.

Live panel QA confirmed the panel fields match current daemon cycle diagnostics.
Live QA and gauntlet fields may show `unknown` unless those checks were run from
within the panel/runtime status path.

## Live QA Workflow

Codex may run terminal commands needed for live QA.

Preferred live QA flow:

1. Launch RuneLite dev when needed:

   ```powershell
   .\gradlew.bat run
   ```

   Or use the bootstrap helper:

   ```powershell
   python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --move-to-secondary-monitor --start-daemon --run-live-qa --print-candidates --timeout-seconds 180
   ```
## Current Live QA Entry Point

Preferred full live QA/bootstrap command:

```powershell
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --move-to-secondary-monitor --start-daemon --run-live-qa --print-candidates --template-confidence 0.85 --timeout-seconds 180
```

Before any real input action, confirm live readiness:

```powershell
python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting
python telemetry-viewer\diagnose_woodcutting_candidates.py --latest-session --profile woodcutting --top 20 --show-rejections
python telemetry-viewer\target_geometry_inspector.py --from-daemon --daemon-url http://127.0.0.1:8890 --live
```

If file-output inspection requires `live_target_processor.py`, bind it to the
daemon session:

```powershell
python telemetry-viewer\live_target_processor.py --from-daemon --daemon-url http://127.0.0.1:8890 --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

First run hover-only confirmation with the readiness gate:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --input-profile steady --movement-profile linear_debug --hover-only --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3
```

Then run a bounded action or short validation loop with readiness and
client-menu confirmation:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --input-profile steady --movement-profile linear_debug --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --click-hold-ms 60 --stop-after-inventory-changes 5 --summary-every-action --final-reconcile-ms 2000 --final-reconcile-game-ticks 6 --pacing-profile natural --target-switch-min-ms 400 --target-switch-max-ms 1400 --target-hover-failure-limit 2 --target-suppression-ms 2500
```

For service-route movement where a waypoint can be hidden behind a foreground
object, enable camera-guided waypoint exposure:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --input-profile steady --camera-self-test --camera-method auto --camera-test-return
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --input-profile steady --movement-profile linear_debug --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --click-hold-ms 60 --summary-every-action --final-reconcile-ms 2000 --final-reconcile-game-ticks 6 --pacing-profile natural --target-hover-failure-limit 2 --target-suppression-ms 2500 --nav-verify-game-ticks 8 --nav-verify-ms 2500 --max-waypoint-alternates 7 --max-navigation-reacquire-rounds 3 --camera-reacquire-waypoint --camera-method auto --camera-exposure-max-ms 2000 --camera-sample-interval-ms 20 --camera-max-direction-switches 2 --camera-allow-diagonal --camera-allow-pitch-adjust --camera-debug-summary --route-waypoint-lookahead-tiles 12 --route-waypoint-max-horizon-tiles 25 --min-route-progress-tiles 3 --max-route-waypoint-distance 30 --prefer-long-visible-waypoint --route-waypoint-distance-mode adaptive
```

For route stability validation, keep motion-lock defaults explicit:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --input-profile steady --movement-profile linear_debug --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --summary-every-action --final-reconcile-ms 2000 --final-reconcile-game-ticks 6 --pacing-profile natural --target-hover-failure-limit 2 --target-suppression-ms 2500 --nav-verify-game-ticks 8 --nav-verify-ms 2500 --max-waypoint-alternates 7 --max-navigation-reacquire-rounds 3 --camera-reacquire-waypoint --camera-method auto --camera-exposure-max-ms 2000 --camera-sample-interval-ms 20 --camera-max-direction-switches 2 --camera-allow-diagonal --camera-allow-pitch-adjust --camera-debug-summary --route-waypoint-lookahead-tiles 12 --route-waypoint-max-horizon-tiles 25 --min-route-progress-tiles 3 --max-route-waypoint-distance 30 --prefer-long-visible-waypoint --route-waypoint-distance-mode adaptive --nav-replan-while-moving false --nav-min-game-ticks-between-clicks 3 --nav-stuck-game-ticks 6
```

The action readiness gate must pass before execution. `live_readiness.v2`
reports overall context status plus the current intent and
`actionReadiness.executionAllowed`. Resource actions check daemon `/status`,
daemon/latest-live session agreement, `overlay_debug_state.json`, highlighter
markers, selected target/highlighter source agreement, target freshness, target
aim/geometry/on-screen state, and RuneLite input geometry. Navigation waypoint
actions check the service route/path waypoint, input geometry, and fresh
`client_tick_hot.v1` interaction state when plugin-snapshot input is active
instead; a resource target/highlighter mismatch can remain a context warning
without blocking `navigation_waypoint_action`.
If hot client-tick state is stale, diagnose readiness before clicking:
`LOGIN_SCREEN` or logged-out states are recovery/bootstrap issues, while a
logged-in stale hot state points at plugin hot-state or daemon refresh.
The hover gate then requires fresh `PostMenuSort` state at the intended canvas
point before clicking, and the action result records the actual
`MenuOptionClicked` option/target afterward.

Fast perception stays fast, but live motor output should use
`--input-profile steady` or `--input-profile natural` unless a focused test needs
`instant_debug`. The governor records `humanInput` and `cameraInput` in
`action_trace.v2`; live summaries should normally report
`directBackendBypassCount=0`.

For navigation waypoints, `Walk here` is valid only under
`navigation_waypoint_action`. If hover says a foreground object action such as
`Chop down Tree`, the executor first tries structured route/path alternates.
If a visible route object such as `Climb-up Staircase` is already
hover-confirmed, the proposer uses that route object before another waypoint.
With camera reacquire enabled, it then keeps the same target world tile and uses
a closed-loop camera exposure controller. The default `auto` method tries held
arrow-key camera input first because WASD may be swallowed by chat focus. While
the camera key is held, the executor repeatedly reads plugin snapshot
projection/client-tick state, reprojects the same world tile, moves the mouse to
the updated projection, and releases the camera key immediately when fresh
`PostMenuSort` predicts `Walk here`. Middle-mouse drag is a fallback pulse mode:
the mouse is released before reprojecting and checking hover. A camera adjustment
is counted only when yaw/pitch or the target projection actually changes. If the
tile never exposes `Walk here`, no click is sent. Right-click walk fallback
remains deferred until reliable menu row geometry is available; minimap
navigation is an explicit reserved navigation-only fallback, not a default.

The action proposer now uses `safeAimPoint` for resource targets. Valid but
partially clipped edge candidates are skipped if no visible/interactable point
can be selected inside the viewport margin. Projection sentinel canvas values
such as `2147483647` are invalid aim points: they may remain visible in overlay
diagnostics, but resource clicks must not execute against them. When live
plugin-snapshot context is fresh and the candidate has a recoverable projection
failure, the proposer may emit the non-click `resource_view_recovery` action.
That action holds bounded camera input through `HumanInputController`, refreshes
projection, and then lets normal resource selection retry. It is only progress
if projection improves or a safe aimpoint appears; unchanged sentinel geometry
now stops as `resource_projection_recovery_failed` rather than looping camera
input. Overlay summaries separate `bestLogicalResourceTarget` from
`selectedExecutableResourceTarget` and report `invalidAimpointTargetsByReason`
so "trees found but not safely projectable" is distinct from "no trees found."
Loop summaries separate proposed actions, hover checks, unsafe/hover skips,
actual clicks, expected menu clicks, and game-progress successes.
`--final-reconcile-ms` captures delayed inventory/resource progress before exit,
and `--pacing-profile steady|natural` records bounded target-switch delays in
`action_trace.v2`. Resource timeout reconciliation also keeps a bounded watch
when a resource target is reacquired immediately after a no-progress timeout.

Sparse visual debug bundles are available when explicitly requested with
`--capture-debug-screenshots` and event triggers such as
`--screenshot-on-failure`, `--screenshot-on-camera-recovery`,
`--screenshot-on-timeout`, and `--screenshot-on-edge-reject`. They are capped by
`--max-debug-screenshots` and written under
`interaction_geometry/live/debug_bundles/` for the active session when
available. Each bundle pairs a screenshot, if capture succeeds, with daemon
status, overlay debug state, proposal/action-trace summaries, mouse position,
route mode, current route node/edge, selected service anchor, selected approach
node, selected waypoint, route/source mismatch details, pathing reason,
wall-loop classification, projection/safe-aimpoint summaries, client-tick
hover/clicked-menu summaries, human-input metrics, and final decision.
Screenshots are audit evidence only; runtime decisions remain based on
telemetry and HumanInputController. Screenshot capture failures are recorded in
the bundle and summary counters without crashing or unblocking execution.

Goal-directed service navigation now handles nearby unmapped Lumbridge resource
areas instead of assuming the west-tree source route. When inventory is full and
the current position is outside the known source area, `route_context.v1`
selects the retained Lumbridge Castle bank service anchor and enters
`goal_directed_fallback`. The fallback uses destination-centered approach nodes
(`lumbridge_bridge_east_approach`, `lumbridge_bridge_west_approach`,
`lumbridge_castle_south_entrance_approach`, then
`lumbridge_castle_entrance_or_courtyard`) and marks an approach complete either
on arrival or after the player has passed it along the bankward corridor. This
prevents bridge/approach nodes from being reselected behind the player after
useful progress. Live validation from the unexpected east-side tree area around
`3254,3240,0` reached the normal Lumbridge route context, reacquired the
ground-floor Staircase route object, opened bank service, deposited 15 logs,
closed the bank, and entered `return_to_resource` with `directBackendBypassCount=0`.
The follow-up return validation from session
`2026-05-24_14-57-35` then reacquired the post-service return stairs, descended
to plane 0, returned to the learned resource area, reacquired Tree/Oak targets,
and collected post-service logs. The final observed state was
`resource_target_selected` / `select_target`, bank closed, 12 free slots, 3 logs,
near `3195,3219,0`, with `directBackendBypassCount=0`.
Post-service return-stair reacquisition is phase-scoped: service/resource/
waypoint suppression is reset when the lifecycle phase, intent, plane, or route
node changes, and a visible/actionable down staircase uses the
`route_transition` reacquire budget. If an initial stair verifier times out but
later plane, route-node, or path-to-interact evidence proves progress, the
action records
`routeTransitionProgressClassification=return_transition_reconciled_success`
instead of remaining an unresolved timeout.
Route-transition verification now also records a per-action
`route_transition_action_ledger.v1`. It separates `return_transition_pending`
when pathing/local-destination evidence exists, `return_transition_retry_required`
when a confirmed click has no completion evidence before the timeout,
`return_transition_retry_success` when a later same-object retry succeeds, and
`return_transition_reconciled_success` only when later evidence proves the
original attempt advanced the route. Live soak summaries should therefore read
route transition counters before treating every timeout as a true failure.

For full lifecycle soaks, use lifecycle stop flags rather than only a raw
action count:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --input-profile steady --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --summary-every-action --final-reconcile-ms 3000 --final-reconcile-game-ticks 8 --resource-reconcile-ms 4000 --resource-reconcile-game-ticks 8 --pacing-profile natural --target-hover-failure-limit 2 --target-suppression-ms 2500 --nav-verify-game-ticks 8 --nav-verify-ms 2500 --max-waypoint-alternates 7 --max-navigation-reacquire-rounds 3 --camera-reacquire-waypoint --camera-method auto --camera-exposure-max-ms 2000 --camera-sample-interval-ms 20 --camera-max-direction-switches 2 --camera-allow-diagonal --camera-allow-pitch-adjust --camera-debug-summary --route-waypoint-lookahead-tiles 12 --route-waypoint-max-horizon-tiles 25 --min-route-progress-tiles 3 --max-route-waypoint-distance 30 --prefer-long-visible-waypoint --route-waypoint-distance-mode adaptive --reject-edge-route-clicks --camera-reacquire-on-edge-projection --route-click-edge-margin-px 12 --route-min-visible-area-ratio 0.45 --nav-replan-while-moving false --nav-min-game-ticks-between-clicks 3 --nav-stuck-game-ticks 6 --stop-after-lifecycle-cycles 1 --stop-after-post-service-logs 2 --max-total-actions 150 --max-consecutive-timeouts 3
```

`loopSummary` separates executed clicks from lifecycle outcomes with
`lifecycleCyclesStarted`, `lifecycleCyclesCompleted`, `inventoryFullEvents`,
`serviceRoutesStarted`, `serviceRoutesCompleted`, `bankOpenEvents`,
`depositSuccesses`, `serviceCompleteEvents`, `returnRoutesStarted`,
`returnRoutesCompleted`, `resourceReacquisitions`,
`postServiceResourceCollections`, and `postServiceLogsCollected`. A cycle is
complete only after service completion, return-route resource reacquisition,
and at least one post-service resource/log collection.

Resource-click reconciliation is resource-specific. `--resource-reconcile-ms`,
`--resource-reconcile-game-ticks`, and `--post-click-progress-tail-ticks` extend
the bounded reconcile for `select_resource_target`. If inventory/progress
evidence arrives after an initial timeout, the observed result records
`delayedProgressReconciliation=true` and
`resourceProgressClassification=resource_timeout_reconciled_success`.
Loop summaries now also report `unresolvedTimeouts`, `timeoutReasons`,
`timeoutActionTypes`, `timeoutsByIntent`, `timeoutRecoveredBy`,
`resolvedByRetry`, `resolvedByLateEvidence`, `pendingButSafe`, and
`evidenceAfterTimeout` so a soak can distinguish true no-progress from delayed
evidence, route-transition pending states, and retry-resolved transitions.

Repeated true `Cancel` hovers or other no-click hover mismatches can now
suppress the specific target/aimpoint for a short window and reacquire the next
safe candidate from the existing daemon/profile/overlay context. Suppressed
targets are reported as skips, not action attempts; if all candidates are
suppressed or unsafe, the loop waits instead of clicking.

2. If RuneLite requires login, account confirmation, or anything
   credential-related, stop and ask the user to handle it.
3. Once the user confirms the client is logged in, continue automatically.
4. Wait for plugin snapshot endpoint `127.0.0.1:8893`.
5. Confirm plugin snapshot reports `LOGGED_IN`.
6. Start or restart `live_core_daemon.py` with the daily daemon command.
7. Run the live QA runner and relevant diagnostics.
8. Inspect whether live values look correct.
9. If values are wrong, make focused fixes based on the diagnostic fields and
   rerun the focused tests/diagnostics.

If Computer Use can operate the RuneLite dev window, Codex may click simple
already-authenticated buttons such as Play / Log in / Continue. Codex must not
handle credentials or account settings. If Computer Use cannot access the
RuneLite window, ask the user to click/log in manually, then continue with
endpoint checks and diagnostics.

Preferred live retest commands:

```powershell
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

## Verification Commands

Always run focused tests for files changed.

Always run:

```powershell
python telemetry-viewer\run_stabilization_suite.py
```

If Java/plugin code changed, also run:

```powershell
.\gradlew.bat test
.\gradlew.bat build
```

If Python daemon/analyzer/diagnostic files changed, run relevant `py_compile`
checks, for example:

```powershell
python -m py_compile telemetry-viewer\live_core_daemon.py
python -m py_compile telemetry-viewer\diagnose_woodcut_bank_cycle.py
```

## Current Next Milestone

No next implementation milestone is set in this handoff. Keep future changes
focused on the user's current task and preserve the completed baseline above.
