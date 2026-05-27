# Live Outputs

Live outputs are generated state from the Python sidecar or plugin endpoint/cache
paths. They are useful for diagnostics and visual QA, but the daemon session and
current daemon status remain the action source of truth.

The old append-only live packet archive has been removed from runtime. Normal
live operation must not create `live_packets\`, `live-*.ndjson`, or
`live-*.jsonl`. Historical files are legacy cleanup only and should be reported
with `python telemetry-viewer\maintenance.py --live-packets-report`.

Plugin configuration follows the current pipeline manifest. The normal settings
surface exposes only Core, Snapshot Endpoint, and Overlay controls; developer
diagnostics are hidden from the normal UI. Retired workflow, raw recording,
frame capture, compact packet file/stream, and live packet archive keys are not
normal UI controls. Check the current key registry and runtime status with:

```powershell
python telemetry-viewer\context_service.py --pipeline-health
```

## Current Files

| File | Writer | Readers | Required | Freshness | Missing behavior | Source-of-truth role |
| --- | --- | --- | --- | --- | --- | --- |
| `live_baseline_state.json` | `live_target_processor.py` in file-output modes | `live_context_query.py`, `context_service.py`, diagnostics | Optional in snapshot-no-files daily mode | Should match latest processed tick when file mode is enabled | Context diagnostics WARN/FAIL depending on query; action path should prefer daemon status | Context/debug summary |
| `live_context_index.json` | `live_target_processor.py` | `live_context_query.py`, `context_service.py`, overlay diagnostics | Optional in snapshot-no-files daily mode | Same tick family as candidates/status | Context queries may lose candidate counts/index answers | Debug/context index |
| `live_candidates.jsonl` | `live_target_processor.py` | `live_context_query.py`, `context_service.py`, candidate diagnostics, overlay diagnostics, inspector | Optional when daemon uses snapshot-no-files and file output is disabled | Candidate tick should be fresh relative to daemon latest tick | Candidate diagnostics WARN unless file output is expected; readiness can still use daemon/highlighter markers | Candidate debug/file source, not action truth by itself |
| `live_status.json` | `live_target_processor.py` | `live_context_query.py`, diagnostics | Optional in snapshot-no-files daily mode | Latest processed tick/status age should be current | Context diagnostics report missing status; daemon `/status` is preferred for action | Debug status |
| `live_activity_state.json` | `live_target_processor.py` | `live_context_query.py`, context service, activity/liveness diagnostics | Optional in snapshot-no-files daily mode | Should track latest processed activity/inventory tick | Activity/liveness answers may be incomplete | Context/debug state |
| `live_navigation_summary.json` | `live_target_processor.py` | `live_context_query.py`, pathing/navigation diagnostics, inspector | Optional but useful for pathing QA | Should match current local collision/window tick when available | Navigation readiness becomes unknown or summary-only | Navigation debug state |
| `live_event_timeline.jsonl` | `live_target_processor.py` when explicit file-output debugging is enabled | `live_context_query.py`, context service | Optional; not created by normal daemon runtime | Recent events should trail current live tick only by expected processing delay | Event-only context is empty | Bounded debug timeline |
| `overlay_debug_state.json` | `live_core_daemon.py` / overlay state writer when enabled | `diagnose_live_readiness.py`, `diagnose_woodcutting_candidates.py`, `diagnose_overlay_state.py`, `diagnose_overlay_geometry.py`, `target_geometry_inspector.py` | Required for resource-target execution readiness when `--write-overlay-state` is expected | Must align with daemon/highlighter session and selected target | Readiness FAIL for target execution; visual diagnostics explain marker gaps | Highlighter/debug proof, not independent action truth |
| `last_action_trace.json` | Executor/action lifecycle path when trace writing is explicitly enabled | Action lifecycle diagnostics and manual review | Optional | Last action only; not a rolling source | Diagnostics omit prior action trace | Debug trace |
| `client_tick_hot.jsonl` | `execute_next_action.py --record-client-hot` only | Manual review, future training/debug analysis | Optional and off by default | Contains only compact samples from the current action run | No file effect on readiness; live readiness uses endpoint `client_tick_hot.v1` freshness when plugin-snapshot input is active | Debug recording, not source of truth |
| `live_packets\live-*.ndjson`, `live_packets\live-*.jsonl`, `live_packet_index.json`, `latest_segment.txt` | Removed | `maintenance.py` report/prune only | Never required | Legacy files may exist in old sessions | Runtime ignores them; maintenance can report/prune | Legacy cleanup only |
| `overlay_state.json` / intent overlay state if present | Daemon overlay writer | Mission Control, overlay diagnostics | Optional unless a live run explicitly asks for overlay state | Should reflect current daemon intent and selected target | Overlay diagnostics WARN; action should use readiness gate | Human/debug overlay state |
| `debug_bundles/<timestamp>_<reason>/bundle.json` plus optional `screenshot.png` | `execute_next_action.py` only when screenshot debug flags are supplied | Manual QA / Codex visual audit | Optional and off by default | Event-triggered snapshot of one action/transition/failure | Missing bundles have no effect on readiness or execution | Sparse evidence bundle, never runtime click truth |

## Rules

- Do not add new continuous JSON or NDJSON runtime outputs during cleanup.
- The live packet archive is removed. Do not reintroduce writers, readers,
  config switches, or CLI fallbacks for `live_packets` or `live-*.ndjson/jsonl`.
- Prefer daemon status and in-memory context for action decisions.
- `127.0.0.1:8893` is not a live output file. It is the opt-in RuneLite Java `PluginSnapshotEndpoint`; snapshot-no-files daemon runs require it while `inputSourceActive=plugin-snapshot`.
- `client_tick_hot.v1` is an endpoint payload, not a rolling file. It is sampled from the plugin hot cache and appears in `/snapshot` and daemon `/status` as `clientTickHot`; explicit need `client_tick_tail` returns bounded recent samples. For plugin-snapshot live actions, readiness requires this hot state to be present and fresh before execution can move/click. Stale-hot readiness reports include `gameState`, logged-in status, PostMenuSort/click ages, `staleReason`, and a recovery hint.
- Optional `--record-client-hot` writes compact JSONL only when requested by the action command. It must remain bounded and must not become a default continuous runtime output.
- `last_action_trace.json` / `action_trace.v2` may include `safeAimPoint`, `rawAimPoint`, sampled/rejected aimpoints, clicked-menu proof, target suppression/reacquisition, final reconciliation, pacing delay fields, `dialogue`, `humanInput`, and `cameraInput` when an action command explicitly records or prints the trace.
- Use `live_file_core.py` only for explicit bounded debug/latest-state files.
- Use `live_session_core.py` for latest-session vs latest-live-session vs daemon-session selection.
- Treat missing candidate files differently when snapshot-no-files disables file output.
- Treat `overlay_debug_state.json` as required for resource-target execution readiness because it proves selected target/highlighter agreement.
- `live_readiness.v2` is intent-aware. Overall context may warn when a resource target is absent from the current highlighter source, but `navigation_waypoint_action` can still have `actionReadiness.status=PASS` when route waypoint, input geometry, session freshness, plugin snapshot, and fresh client-tick hot-state requirements are satisfied. The report separates `applicableWarnings` from `nonApplicableContextWarnings`; stale resource target freshness is non-applicable during return navigation and applicable again for resource-object actions.
- A newer filesystem `--latest-session` can be stale while the daemon/latest-live/highlighter session is fresh. Readiness reports this as `staleFileSessionContext` and keeps daemon/plugin state as the action source of truth.
- Visible service-route objects can intercept waypoint walking. A live candidate or fresh hover such as `Climb-up Staircase` becomes a route-transition proposal before another `Walk here` waypoint. Generic `Climb Staircase` is allowed only as a route-transition dialogue opener when the current route step expects an up/down staircase prompt; final route success still requires a later plane/location/route change.
- `dialogue_state.v1` is an endpoint/cache payload, not a rolling file. It exposes bounded chatbox prompt/options state for route-transition resolvers. When active, `interface_dialogue_choice_action` can select the route-correct option through `HumanInputController`, preferring number keys such as `1` for `Climb up the stairs.` and `2` for `Climb down the stairs.`.
- `return_route_context.v1` is daemon status/brain context, not a new runtime
  output file. After banking completes and the bank UI is closed, it can turn a
  remembered or profile resource anchor into a staged Lumbridge return route:
  down-stair transitions first, then ground-floor/courtyard waypoints, then
  resource reacquisition. Route-relevant `Climb-down` objects outrank generic
  return waypoints.
- Adaptive route waypoint selection uses structured path/route tiles from the current pathing context. It may choose a farther open-corridor waypoint, but transition/tight-geometry steps remain short and precise. Reacquisition does not fall back to arbitrary dense pixel probing.
- Route tile projection is a live capability, not proof of clickability by
  itself. Degenerate origin projections and tiny/off-viewport tile polygons are
  non-actionable. The executor may try structured path/route alternates, but it
  must not click the canvas origin or a border sentinel.
- Route waypoint target explanations include `route_projection_status.v1` after
  plugin projection. It classifies projected tiles as visible, offscreen,
  degenerate, tiny, occluded, missing, or non-actionable, and records whether
  the canvas projection is currently action-ready.
- After a confirmed `Walk here` navigation click, the loop may hold
  `navigationInProgress` and suppress replanning while pathing still reports
  `moving` or `recently_moved`. The summary separates these motion waits from
  actual clicks with `navigationInProgressWaits` and
  `routeReplanSuppressedWhileMoving`.
- Repeated immediate waypoint cycles are guarded. The executor records
  `route_oscillation_detected`, `route_backtracking_detected`, or
  `route_wall_hugging_detected` and stops safely rather than clicking the same
  wall-adjacent corridor again.
- If pre-click hover predicts the current navigation action but the actual
  `MenuOptionClicked` event reports a different action, the command records
  `menu_flip_mismatch` and does not count the click as route progress.
- Navigation hover confirmation requests a bounded recent `postMenuSortTail`
  when using plugin snapshots. If recent samples at the same waypoint contain
  volatile NPC/object/widget actions while the current intent expects `Walk
  here`, the executor records `volatileHoverZone`, skips the click before
  mouse-down, and reports `volatileHoverSkips` separately from actual clicks.
- Executors gate live input on `actionReadiness.executionAllowed`, not on resource-only selected-target checks.
- Action target source/actionability fields separate advice from execution.
  `static_route_prior` and `route_context_goal` are advisory only; they can
  guide pathing, but cannot be clicked. A live action must be upgraded to a
  source such as `local_frontier_waypoint` plus fresh projection,
  `live_projected_waypoint`, `live_route_object`, `live_service_object`,
  `live_resource_candidate`, or `hover_discovered_object`.
- When a stale/static proposal is encountered, the loop waits/reacquires once
  within the relevant budget and reports `staleProposalDetected`,
  `staleProposalSource`, `reacquireAttempted`, `reacquireResult`,
  `freshTargetFound`, `freshTargetSource`, and `reasonIfNoFreshTarget`. If no
  fresh executable target appears, the decision is wait/skip, not click.
- Hover confirmation is keyed to the action intent. Navigation expects
  `Walk here`; resources expect Tree/Oak `Chop`/`Chop down`; route transitions
  expect the route-step climb/open option; service objects expect bank/use/deposit
  options; dialogue choices expect the selected option/index. Structured
  mismatch reasons include `hover_option_mismatch`,
  `hover_target_mismatch`, `wrong_intent_matcher`, `stale_hover_sample`,
  `menu_flip_mismatch`, and `target_source_mismatch`.
- Resource proposals can report `resourceSelectionReason` such as
  `preferred_skill_eligible_resource_candidate` when the active target is
  replaced by a more appropriate live candidate. With no Woodcutting level
  telemetry, a basic `Tree` outranks higher-level Oak/Willow candidates when
  both are available; a known sufficient level allows the higher-level target
  to rank normally.
- VM coordinate resolution metadata is included in proposals/traces/bundles:
  `coordinateSpace`, `scaleX`, `scaleY`, `screenPointBeforeScaling`,
  `screenPointAfterScaling`, `windowBoundsSource`, and `canvasBoundsSource`.
  `scaled_logical_to_physical` is the Windows/AWT logical window to pyautogui
  physical pixel conversion used in the VMware guest.
- Player location fields prefer authoritative baseline/player telemetry. If
  only collision-window center is available, status/bundles label it as
  `collision_window_center_proxy` with lower confidence.
- `target_geometry_inspector.py --from-daemon --live` can use `overlay_debug_state.json` directly for visual/highlighter inspection when `live_candidates.jsonl` is intentionally absent.
- JSON diagnostics should print to stdout and avoid writing new files unless the user supplies an explicit output path.

## Retired Packet Archive Replacement

- Current state: `context_service.py --query current-debug-context`
- Current blocker: `context_service.py --query explain-current-blocker`
- World/candidates: WorldModelCache plus Knowledge Fabric queries
- Historical replay: `replay_scenario.v1`
- Future script context: `script_authoring_context.v1`
- Debug evidence: sparse visual debug bundles
- Retained observations: bounded `session_memory.v1`
- Legacy cleanup: `maintenance.py --live-packets-report` and
  `maintenance.py --prune-legacy-live-packets --dry-run`

## World Model Endpoint Payloads

- `world_model_snapshot.v1` is served by the Java plugin snapshot endpoint when
  `/snapshot` requests include world-model needs. It is endpoint data, not a
  live output file.
- The model covers the currently loaded local scene only. Out-of-scene
  navigation still uses route priors, learned anchors, service anchors, and
  local frontier progress until the next object/anchor enters the loaded scene.
- Default live use should request compact payloads such as
  `world_model_summary`, `route_object_census`, `resource_object_census`,
  `service_object_census`, `pathing_frontier`, `projection_audit`, and
  `view_quality_inputs`. `full_world_model_debug` is reserved for explicit
  debug bundles or one-shot inspection.
- Query responses are bounded by object/collision/projection caps. Cap and
  freshness fields such as `worldModelAvailable`, `worldModelAgeMs`,
  `objectCensusCapHit`, `collisionAvailable`,
  `projectionAuditAvailable`, and `projectionCapHit` must be reported instead
  of silently treating missing objects as absent from the game.
- Projection budget is applied after the loaded-scene census is built. The
  cache prioritizes nearby route/resource/service objects before lower-value
  scene clutter, so a visible Tree or route transition should still receive
  projection status even when the scene object census itself is capped.
- Route, resource, and service object censuses are independent lanes. A route
  staircase or bank object can be present in the world model even when resource
  candidate caps or filters are saturated, and a basic Tree/Dead tree can remain
  executable while Oak is visible but skill-locked.
- Sparse visual debug bundles may copy the current compact evidence into
  `world_model_summary.json`, `route_object_census.json`,
  `resource_object_census.json`, `service_object_census.json`,
  `projection_audit.json`, and `collision_frontier.json`. These files exist for
  human/Codex comparison with screenshots, overlay state, and action traces;
  they do not authorize clicks.
- If daemon status does not already contain the world-model payloads, the bundle
  writer may perform one compact read-only world-model query to populate these
  evidence files. This remains debug evidence only and does not become a motor
  or readiness path.

## Knowledge Fabric Outputs

- Knowledge Fabric is normally in memory. It is not a new continuous live file
  stream and it does not change the action source of truth.
- `knowledge_fabric_status.v1` summarizes whether the world model is fresh,
  session memory is current, static libraries loaded, indexes built, caps hit,
  and what query capabilities are available.
- `knowledge_fabric_current_debug_context.v1` is the compact aggregate that
  Codex should request first for live debugging. It includes live status,
  readiness, world model summary, Fabric status, current blocker, action
  proposal, resource/route/service candidates, pathing frontier, view quality,
  overlay health, input integrity, latest trace/bundle summaries, session
  memory, and static profile summary.
- `knowledge_fabric_current_blocker_explanation.v1` is the compact blocker
  diagnosis. It returns phase/state, intent, location, inventory, a primary
  category, one-sentence summary, evidence, a recommended next step, and whether
  a bounded live action is currently safe.
- `live_world_index.v1` summarizes spatial, object/action, route object,
  resource, service, projection, collision/frontier, and view-quality indexes
  built from the current world-model payloads.
- `session_memory.v1` may be stored under the current session at
  `interaction_geometry/live/session_memory.json` only when explicitly updated
  by a query/helper. It stores advisory resource areas, service anchors, route
  objects, waypoint outcomes, menu-flip zones, camera outcomes, and area labels.
  Old-session memory must not be used as executable live truth.
- `static_knowledge_library.v1` is a compact summary of `service_routes.json`,
  `target_profiles.json`, `target_library.json`, known skill requirements, area
  hints, and advisory service anchors.
- The optional MCP adapter is local/read-only and exposes tools/resources for
  inspection. It does not expose live input, click, key, or camera execution.
  The context service can expose the same aggregate through
  `knowledge_current_debug_context` and `knowledge_current_blocker` needs.
- `script_authoring_context.v1` is an explicit, user-triggered bundle under
  `interaction_geometry/live/script_authoring_context/`. It contains a
  manifest plus compact query outputs, object censuses, route/service/resource
  evidence, pathing frontier, collision/projection/view-quality summaries,
  overlay state, input-integrity status, trace excerpts, session memory, and
  static library excerpts for future script/profile writing.
- `replay_scenario.v1` is an explicit offline replay artifact under
  `interaction_geometry/live/replay_scenarios/`. Replaying it runs candidate,
  proposal, readiness, blocker, pathing, and view-quality reasoning without
  live input or RuneLite.
- `data_quality_report.v1` is included in script-authoring/debug evidence and
  reports freshness, object count, collision/projection availability, caps,
  truncation, missing sections, query failures, response sizes, query times,
  confidence, and recommended fixes.
- `debug_context_diff.v1` compares two bundle/context files for phase, route,
  candidate, blocker, pathing, view-quality, cap, and memory changes.
- `knowledge_fabric_handoff_summary.v1` is a concise current-state handoff for
  the next Codex turn or human review.

## Visual Debug Bundles

`--capture-debug-screenshots` enables sparse visual debug bundles for action
runs. The executor writes them under `interaction_geometry/live/debug_bundles/`
inside the daemon session when a session path is known, or under the same
relative path in the current workspace when no session path is available.

Bundles are event-triggered and capped by `--max-debug-screenshots`. Trigger
flags include `--screenshot-on-failure`, `--screenshot-on-camera-recovery`,
`--screenshot-on-timeout`, `--screenshot-on-edge-reject`, and
`--screenshot-on-lifecycle-transition`. Capturable reasons include route source
mismatches, goal-directed fallback start, wall-hugging/path-blocked route
states, alternate approach selection, service-anchor arrival, route-object
reacquisition, live target reacquisition after stale/static proposals,
stale/static route-target blocks, hover-intent mismatches, target-source
mismatches, stale proposal reacquire failures, route no-progress timeouts,
edge-rejected waypoints, camera/resource recovery start/end, menu flip mismatch,
unexpected current area, and final summary. A bundle contains `bundle.json`, the captured
`screenshot.png` when screen capture succeeds, `daemon_status.json`,
`overlay_debug_state.json` when available, and a compact action-trace excerpt.

`bundle.json` is shaped for Codex/user review. In addition to the screenshot
path it records route mode, current route node/edge, selected service anchor,
selected approach node, selected waypoint, route-source mismatch details,
pathing reason, wall-loop classification, projection/safe-aimpoint summaries,
resource view score/classification, worksite context when available,
target source/actionability, stale proposal/reacquire status, client-tick
hover/clicked-menu summaries, coordinate-scaling metadata,
HumanInputController metrics when present, Knowledge Fabric evidence paths, and
the final decision (`clicked`, `skipped`, `waited`, `camera adjusted`, or
`stopped safely`).
Knowledge Fabric bundle evidence includes `current_debug_context.json`,
`explain_current_blocker.json`, resource/service/route candidates,
`pathing_frontier.json`, `view_quality.json`, session memory, and static library
summaries, plus `data_quality_report.json` and `handoff_summary.json`.

Screenshots are evidence for humans and later debugging only. Runtime decisions
still come from projection geometry, safe aimpoints, client-tick hover,
`MenuOptionClicked`, route/service state, and `HumanInputController`. Screenshot
capture failure is recorded in the bundle and summary counters; it does not
crash or unblock execution.

## Safe Visible Aimpoints

- Candidate validity is not the same as actionability. Diagnostics may still show a partially visible Tree/Oak candidate even when its raw clickbox center is outside the usable viewport.
- The action path uses `safe_aimpoint.v1` to clip candidate geometry to the current canvas/viewport with a small edge margin.
- Projection sentinel coordinates, such as extremely large `2147483647` canvas values from pending live-object projection, are invalid aim points. They may remain visible as diagnostic candidates/markers, but readiness must not treat them as click-ready.
- Off-viewport raw centers are never clicked directly. If clipped visible geometry exists, the proposed canvas point is moved inside that region; if no safe region exists, the candidate is skipped as `safe_aimpoint` unavailable.
- `overlay_debug_state.json` separates `targetsWritten` and hull counts from actionability with `safeAimpoints`, `executableTargets`, `invalidAimpointTargets`, `edgeClippedCandidates`, `selectedTargetPresent`, and `selectedSafeAimPoint`.
- Hover confirmation remains the final fast check before a click: a safe point must still produce the expected top menu action when `--hover-confirm-target` is enabled.
- A true `Cancel` hover is distinct from `Walk here`: if the sampled left-click entry is only `Cancel`, the executor skips the click and can suppress that target/aimpoint briefly. If `Cancel` is only a lower sentinel entry while the left-click entry is `Chop down`, it is ignored.

## Target Suppression And Reacquisition

- Repeated no-click hover failures for the same target/aimpoint can be suppressed with `--target-hover-failure-limit` and `--target-suppression-ms`.
- Suppression is target-local and short-lived. It records cancel, walk-here, stale, position-mismatch, and generic hover mismatch failure counts.
- While a target is suppressed, the action proposer can choose the next unsuppressed resource candidate already present in daemon/profile/overlay context. Suppressed targets remain visible in diagnostics, but they are not clicked.
- If every candidate is suppressed or unsafe, the loop waits/reacquires instead of clicking. These waits are reported separately from action attempts.
- Suppression clears after successful progress by default so a target that reappears with fresh geometry can be considered again.

## Camera-Guided Waypoint Exposure

- Service-route navigation uses world tiles as the stable target, not the first projected screen pixel. Camera movement shifts the projected canvas point, so `--camera-reacquire-waypoint` reprojects the same tile continuously while held camera input is active.
- `camera_exposure_score.v1` is computed from plugin tile projection, camera viewport, and fresh `PostMenuSort` hover state. `Walk here` at the projected point is `exposed_walk_here`; foreground object actions such as `Chop down Tree` are `occluded_by_object`.
- `action_trace.v2.reacquisition.cameraExposureAttempts` records bounded camera attempts with target world tile, camera method, held keys or drag pulse, projected canvas before/after, per-sample hover/projection state, exposure score before/after, yaw/pitch/projection deltas, and follow error.
- Camera-guided reacquire is navigation-only. It is not used for resource targets or route transition objects, and it does not click unless hover confirmation predicts `Walk here`.
- Poor projections can also trigger camera reacquire. With
  `--reject-edge-route-clicks --camera-reacquire-on-edge-projection`, an
  edge-clipped, partially offscreen, or low-visible-area route tile is treated
  as `waypoint_edge_projection` rather than as a click target. Trace fields
  record `cameraTriggeredBy=edge_projection`, projection/edge metrics before
  and after, and whether the camera improved the projection.
- The fallback remains bounded by `--camera-exposure-max-ms`, `--camera-sample-interval-ms`, and `--camera-max-direction-switches`. Older `--camera-reacquire-timeout-ms` and `--camera-probe-ms` aliases still work. It must not create dense pixel scans or unbounded mouse sweeps, and loop summaries count a camera adjustment only when yaw/pitch or target projection changed.
- `--camera-self-test --camera-method auto --camera-test-return` performs a one-shot held-input method check and writes `interaction_geometry/live/camera_calibration.json`; this calibration helps pick a method but does not replace closed-loop exposure.
- `--allow-minimap-navigation` is reserved for a future explicit navigation-only minimap fallback; it is not a default click path until reliable minimap coordinate telemetry is available.

## Route Object Acquisition

- Service-navigation status now reports route-transition/service object state separately from resource target state. The overlay can show resource `safe 0/N` while the route census still has actionable stairs, doors, or bank objects.
- `routeObjectCensus` is bounded and uses the existing status/debug path. It reports route transition candidates, service object candidates, route-relevant/actionable counts, visible-but-route-irrelevant counts, source lane counts, and rejection reasons such as `wrongRouteStep`, `wrongPlane`, `outsideRouteCorridor`, or `randomTransitionObject`.
- `route_relevance.v1` prevents random stairs/ladders from intercepting a service route. A `Climb-up Staircase` candidate wins over waypoint walking only when it matches the active route id/step, expected action/target, plane or plane transition, and Lumbridge route corridor/search area. Generic `Climb Staircase` is treated as a dialogue opener only when the route step declares it as such; otherwise it remains non-final and must not advance the route by itself.
- Hover-discovered route objects are useful evidence when scene/projection scanning misses an object. If hover sees `Climb-up Staircase` but route relevance cannot be established, the action layer treats it as `hover_confirmed_but_route_unresolved` and does not click it from hover alone.

## Goal-Directed Service Navigation

- `route_context.v1` is nested under `serviceRouteContext` and explains how the
  daemon selected the current service-route mode. It reports the player
  location/plane, current-area source, `routeSourceStatus`, selected Lumbridge
  service anchor, selected approach node, and route-source mismatch details.
- If the player is at the configured west-tree source, `routeMode` remains
  `explicit_route` and the staged Lumbridge Castle bank route is used normally.
  If the player is in an unmapped nearby resource area but the Lumbridge bank
  anchor is known, `routeMode=goal_directed_fallback`.
- In goal-directed fallback, the bank/service anchor is a destination goal and
  is not clicked directly. The route picks a destination-centered approach node
  such as castle entrance/courtyard, then pathing exposes a safe
  `localFrontierWaypoint` inside the current collision window.
- Approach nodes are not reselected after the player arrives at them or passes
  them along the service-bound corridor; this prevents bridge and entrance
  staging markers from pulling the route backward.
- Pathing summaries may include `fallbackGoal`, `fallbackApproachNode`,
  `localFrontierWaypoint`, `frontierDistanceBefore`,
  `frontierDistanceAfterEstimate`, and `progressScore`. These fields show
  whether a local scout waypoint makes meaningful progress toward service.
- Tree/Oak hover around a local frontier while inventory is full is a
  service-navigation volatility problem. The executor should skip or suppress
  that frontier, try an alternate/camera/approach path, or stop with a clear
  route-context blocker rather than counting a resource/object hover as service
  progress.
- Visual debug bundles for `route_source_mismatch`,
  `goal_directed_fallback_started`, `local_frontier_volatile_hover`,
  `tree_hover_frontier_blocked`, and `goal_directed_path_blocked` carry the
  selected service anchor, approach node, waypoint, pathing reason, and
  screenshot evidence for review.

## Bank-Floor Service Acquisition

- `serviceObjectCensus` is the bank-floor/service-stage companion to `routeObjectCensus`. It reports Bank booth, Banker, Deposit box, and Bank chest candidates even when resource safe-target counts are zero or stair-transition candidates are absent.
- At `lumbridge_castle_bank`, a route-relevant actionable service object becomes `visibleServiceTarget`; `selectedServiceAction` prefers route-expected actions such as `Bank`, `Deposit`, or `Use` over less useful options such as `Collect`.
- If a bank/deposit object click starts movement toward the object but the UI is not open yet, action lifecycle returns `service_object_pathing_to_object` and keeps the next action blocked while the game finishes path-to-interact. Bank/deposit UI open or resource deposit evidence is still required for service completion.
- Deposit-inventory remains protected by bank operation context. When non-resource/protected inventory is present, the proposer uses targeted resource-slot depositing instead of the generic deposit-inventory button.
- Targeted resource-slot depositing uses bank-side `inventorySlots` widget
  telemetry when available. The bank operation context exposes
  `resourceItemSlotBounds`, `resourceItemWidgets`, and `resourceDisplayName` so
  the proposer can click a log/resource slot without using the generic
  deposit-inventory button. After the bank closes, a retained inventory summary
  showing zero target resources is enough to keep the service cycle complete
  instead of reopening the bank.

## Return-To-Resource Lifecycle

- `resource_return_context.v1` supplies the destination anchor. Sources can be
  a last successful tree, a learned collection area, or the low-confidence
  profile anchor for Lumbridge west trees when no live memory survived the bank
  phase.
- `return_route_context.v1` supplies the route leg. On Lumbridge bank floor it
  searches for a route-relevant down staircase before any plain waypoint click;
  on the ground floor it uses staged return nodes toward the west tree area.
- If the bank UI is still open, `close_bank` remains the first action. World
  route clicks are not proposed until the UI no longer blocks the canvas.
- Down-stair steps accept direct `Climb-down Staircase`, right-click row
  selection when available, or the existing dialogue resolver with the
  `Climb down the stairs.` option for generic staircase prompts.
- Once the player is near the resource anchor or Tree/Oak candidates become
  visible/actionable, normal resource target selection takes over and the loop
  returns to collecting.
- Reacquire budgets are phase-scoped. Suppressed targets from service,
  resource, navigation, or an earlier route phase are cleared when the lifecycle
  phase, intent, plane, or route node changes. A visible/actionable return stair
  is treated as a route-transition budget item, so exhausted resource or
  waypoint retry state must not block `return_transition_action`.
- If a route-relevant return stair is visible/actionable but its first hover
  sample temporarily reports `Walk here`, the executor may clear that stale
  suppression once within the route-transition budget and retry normal hover
  confirmation. The hover gate still protects execution; no click is sent
  unless the current menu confirms the expected route transition.

## Resource Projection Recovery

- `resource_projection_status.v1` is attached to resource target summaries when
  a selected or backup Tree/Oak candidate has projection context. It reports
  `classification`, `projectionSentinel`, `projectionAvailable`,
  `safeAimPointAvailable`, `edgeClipped`, `offscreen`, `tinyProjection`,
  `degenerateProjection`, cap-hit flags, and `recoverySuggested`.
- Overlay summaries expose `invalidAimpointTargetsByReason`,
  `projectionSentinelTargets`, `recoverySuggested`, `recoveryActionReady`,
  `selectedRecoveryTarget`, `bestLogicalResourceTarget`, and
  `selectedExecutableResourceTarget`.
- `resource_view_recovery` is a non-click action. It is used when a resource
  candidate exists but every current aimpoint is unsafe for recoverable
  projection/view reasons. It can rotate/reacquire with held camera input, then
  waits for the normal candidate projection path to produce a safe aimpoint.
  If projection remains unchanged, the verifier reports
  `resource_projection_recovery_failed`; repeated camera recovery is not treated
  as successful progress.
- `2147483647`-style sentinel geometry is classified as
  `projection_sentinel`, not as an edge-clipped executable target. Sentinel
  candidates remain diagnostic only until recovery or fresh projection produces
  real canvas geometry.

## Resource View Score

- `resource_view_score.v1` describes whether the current camera is good enough
  for resource candidate selection and chopping. It reports `worksiteId`,
  `playerLocation`, `cameraYaw`, `cameraPitch`, visible/executable resource
  counts, safe and central aimpoint counts, edge-clipped/offscreen/occluded
  candidate counts, candidate spread, selected target world tile, worksite
  distance, viewport-edge distance, visible-area ratio, hover readiness,
  worksite-drift flag, score, and classification.
- Classifications are `good_resource_view`, `usable_resource_view`,
  `poor_edge_resource_view`, `poor_occluded_resource_view`,
  `poor_single_candidate_view`, `needs_resource_camera_reacquire`,
  `needs_worksite_recenter`, and `no_executable_resource_view`.
- Poor resource views should produce a non-click `resource_view_recovery`
  action before a chop click. Recovery keeps the target as the resource
  cluster/worksite, sends bounded camera input through `HumanInputController`,
  then re-reads daemon/plugin state and re-scores candidates.
- Visual debug bundles for `poor_resource_view`,
  `resource_camera_reacquire_start`, `resource_camera_reacquire_end`,
  `resource_target_edge_rejected`, `worksite_drift_detected`,
  `post_depletion_reacquire`, and `no_executable_resource_view` include the
  score, classification, worksite context, safe aimpoint summary, and selected
  target source/actionability.

## Pacing And Reconciliation

- `--pacing-profile instant_debug` preserves the fastest loop for tests and focused debugging.
- `--pacing-profile steady` applies a bounded target-switch delay, for example `--target-switch-min-ms 400 --target-switch-max-ms 1400`.
- `--pacing-profile natural` uses the same bounds plus optional idle settings, and records the applied delay in `action_trace.v2`.
- `--no-safe-target-wait-ms` and `--suppressed-target-wait-ms` bound the small reacquisition waits used when no immediate safe target is available.
- `--final-reconcile-ms` performs a short bounded post-loop status check after an actual click so delayed inventory/resource progress can be folded into the final classification before the command exits.
- `--final-reconcile-game-ticks` can extend that reconcile window by a small number of game ticks, which is useful when an expected menu click starts a resource action but the inventory update lands after a short wall-clock timeout.
- `--resource-reconcile-ms`, `--resource-reconcile-game-ticks`, and
  `--post-click-progress-tail-ticks` extend that bounded reconcile specifically
  for `select_resource_target`. If delayed inventory/resource evidence appears
  after an initial resource timeout, the action records
  `delayedProgressReconciliation=true` and
  `resourceProgressClassification=resource_timeout_reconciled_success` instead
  of remaining a timeout.
- If a resource target is freshly reacquired after a no-progress timeout, the
  loop can continue a bounded observation window before clicking another target,
  so late inventory changes are reconciled instead of being left as unresolved
  timeouts.
- Route-transition clicks can also reconcile from later evidence. If an
  initial `Climb-down` or generic stair interaction verifier times out but a
  later daemon sample proves plane change, route-node advancement, or
  path-to-interact progress, the action records
  `routeTransitionProgressClassification=return_transition_reconciled_success`
  or `route_transition_reconciled_success` instead of remaining an unresolved
  timeout.
- Route-transition verification distinguishes pending movement, retry-required
  attempts, retry successes, and true timeouts. `route_transition_action_ledger.v1`
  records per-action evidence such as clicked-menu match, local destination
  change, pathing start, player movement, route-node advance, plane change,
  dialogue open, and service-state advance. A no-evidence stair attempt becomes
  `return_transition_retry_required`; a later same-object retry can be counted
  as `return_transition_retry_success` without pretending the first attempt
  succeeded.
- Loop summaries also expose `timeoutReasons`, `timeoutActionTypes`,
  `timeoutRecoveredBy`, and `evidenceAfterTimeout`. These fields make it clear
  whether a timeout was a true no-progress result, a resource delay later
  reconciled by evidence, or a route/service pending condition.

## Full Lifecycle Soak Output

Loop summaries include lifecycle counters for end-to-end woodcut-bank-return
soaks:

- `lifecycleCyclesStarted` / `lifecycleCyclesCompleted`
- `collectionPhasesStarted`
- `inventoryFullEvents`
- `serviceRoutesStarted` / `serviceRoutesCompleted`
- `bankOpenEvents`
- `depositSuccesses`
- `serviceCompleteEvents`
- `returnRoutesStarted` / `returnRoutesCompleted`
- `resourceReacquisitions`
- `postServiceResourceCollections`
- `postServiceLogsCollected`
- `consecutiveNoProgress`
- `consecutiveTimeouts`
- `edgeRouteClicksRejected`
- `cameraReacquireOnEdgeCount`
- `unresolvedTimeouts`
- `trueUnresolvedTimeouts`
- `timeoutReasons`
- `timeoutActionTypes`
- `timeoutsByIntent`
- `resolvedByRetry`
- `resolvedByLateEvidence`
- `pendingButSafe`
- `routeTransitionAttempts`
- `routeTransitionFirstTrySuccesses`
- `routeTransitionPending`
- `routeTransitionRetryRequired`
- `routeTransitionRetrySuccesses`
- `routeTransitionTrueTimeouts`
- `routeTransitionReconciledSuccesses`
- `timeoutRecoveredBy`
- `reacquireBudgetType`
- `reacquireAttemptsUsed`
- `reacquireLimit`
- `budgetResetReason`
- `stoppedByReacquireLimit`

A cycle is complete only after post-service resource collection, not merely
after banking. The CLI stop flags `--stop-after-lifecycle-cycles`,
`--stop-after-service-cycles`, and `--stop-after-post-service-logs` use these
counters. `--max-total-actions`, `--max-wall-time-minutes`,
`--max-consecutive-no-progress`, and `--max-consecutive-timeouts` bound soak
runs that otherwise remain healthy.

Post-bank summaries should treat `bankingComplete=true` with zero held target
resources as stronger than stale service proximity signals. If the bank UI is
still open, the next action is `close_bank`; if it is closed, return/resource
reacquisition logic runs rather than reopening a visible bank or deposit object.

## Human Input Governor

- `--input-profile instant_debug|steady|natural|manual_calibrated` controls the motor-output envelope for live actions.
- Fast perception remains separate from motor timing: client-tick polling, hover confirmation, projection updates, and scoring can remain quick while mouse/camera output is paced through `HumanInputController`.
- Mouse movement is distance and target-size aware. `steady` uses a Fitts-style smooth path; `natural` uses a more variable path while keeping endpoint variation inside the safe target radius.
- Clicks include bounded profile-driven settle and hold timing unless an explicit compatible click-hold option is supplied.
- Camera exposure still holds keys and samples projection/hover in a closed loop, but key holds and middle-mouse drag pulses are issued through the same governor.
- Loop summaries and `action_trace.v2` report `inputProfile`, average mouse move time, average click hold, average reaction delay, camera hold min/avg/max, direction switches, and `directBackendBypassCount`.
- Live summaries also report `liveInputBackend`,
  `liveInputBackendRequired`, `softwareInputAllowed`, backend command counts,
  and blocked command counts. Normal live execution should show Arduino
  required, software not allowed, and `directBackendBypassCount=0`.
- `ArduinoHIDBackend` reports `arduino_hid_backend_status.v1` with port,
  baud, connected/identified/armed state, session token hash, command count,
  ACK failures, timeouts, identity, capabilities, firmware protocol,
  watchdog milliseconds, `stopAllSent`, and latest Arduino error. Live Arduino
  output requires firmware protocol `arduino_hid.v1` with `stopAll=1`,
  `watchdog=1`, and `resetSafe=1`; protocol mismatch is a fail-closed state.
- Monitor-required live runs include `arduino_hid_monitor_status.v1` with
  Raw Input keyboard/mouse proof, expected VID/PID match, injected and
  lower-integrity-injected counters, latest Arduino event age, and
  `monitorPass`/`monitorBlockReason`.
- The local development indicator is
  `telemetry-viewer\input_control\arduino_monitor.py`. It can run as a small
  always-on-top overlay with:
  `python telemetry-viewer\input_control\arduino_monitor.py --show-overlay --overlay-passive --overlay-no-focus --status-output interaction_geometry\live\input_integrity_status.json --vid VID_2341 --pid PID_8036 --com-port COM6`.
  The same script also supports `--status-loop` or `--once` for terminal-only
  checks, plus `--no-overlay` when a visible window might interfere with VM
  focus.
- The overlay/status file uses `input_integrity_status.v1`. It separates Raw
  Input device identity from low-level hook injection flags:
  `LLMHF_INJECTED`, `LLMHF_LOWER_IL_INJECTED`, `LLKHF_INJECTED`, and
  `LLKHF_LOWER_IL_INJECTED`. PASS means the expected Arduino is present,
  live backend is Arduino, bypass count is zero, and injected/lower-IL counts
  are zero. It also shows firmware/protocol status, reset safety, `STOP_ALL`
  availability, watchdog state, and firmware-reported keys/buttons currently
  down. WARN keeps the overlay visible for development issues; FAIL blocks
  monitor-required live execution.
- Arduino status now includes `firmwareSafety` and `vmInputFocusSafety`.
  Firmware safety is about the bridge releasing keys/buttons and supporting
  STOP_ALL/watchdog/reset-safe behavior. VM focus safety is about whether the
  overlay/monitor/VMware capture state may still be blocking normal guest
  mouse control. A safe firmware state with unknown VM focus recovery remains
  a WARN for post-test recovery.
- `execute_next_action.py --input-integrity-self-test-no-move --backend
  arduino --arduino-port COMx --no-overlay` performs STOP_ALL, handshake,
  STATUS, ARM, STOP_ALL, DISARM, and final STATUS without sending MOVE, CLICK,
  or KEY commands. Use it before tiny-move tests when VMware guest input focus
  appears wedged.
- `execute_next_action.py --arduino-pointer-calibration-test --backend
  arduino --arduino-port COMx --allowed-window calibration --no-click` performs
  a no-click closed-loop cursor calibration. It uses a RuneLite or dedicated
  calibration allowed region, reads the Windows cursor position before and
  after each relative HID chunk, stops if the cursor leaves the region or the
  foreground window changes, and reports `arduino_pointer_calibration_test.v1`
  plus per-target `arduino_closed_loop_move.v1` traces. It sends no clicks or
  keys and always cleans up with STOP_ALL/DISARM.
  The closed-loop mover now waits through a bounded settle/poll window after
  each `OK MOVE`, records cursor and Raw Input timing, retries an ACKed
  no-effect chunk a small bounded number of times, and judges calibration by
  final target accuracy plus aggregate no-effect/retry counts rather than
  requiring every individual chunk to land perfectly.
- `execute_next_action.py --arduino-movement-diagnostics --backend arduino
  --arduino-port COMx` runs a no-click/no-key sequence of small relative moves
  (`+5/-5/+10/-10/+15/-15` on both axes), reports per-move ACK, Raw Input,
  cursor delta, settle time, and reliability classification. Cursor movement
  with coalesced Raw Input counters is reported separately from true no-effect
  movement.
- `execute_next_action.py --input-integrity-self-test --backend arduino
  --arduino-port COMx --arduino-require-monitor --arduino-monitor-status
  interaction_geometry\live\input_integrity_status.json` sends `STOP_ALL`,
  verifies `PING`/`IDENTIFY`/`CAPS`/`STATUS`, arms the Arduino, sends only a
  tiny safe pulse, sends `STOP_ALL`, checks Raw Input/injected-count deltas,
  disarms in cleanup, restores focus to the requested target, and can require
  user confirmation with `--require-user-control-confirmation`. It does not run
  a game action.
- `execute_next_action.py --arduino-usb-diagnostics --arduino-port COM6
  --arduino-bootloader-port COM4` prints guest-visible Arduino USB/COM state
  and host-side VMware `.vmx` autoconnect lines. Normal validation should not
  reset or re-upload firmware; reconnect prompts are handled by host VMware
  configuration, not by guest-side clicking.
- The panic command
  `python telemetry-viewer\execute_next_action.py --backend arduino --arduino-port COMx --arduino-stop-all`
  opens the serial bridge, sends `STOP_ALL`, prints the firmware response, and
  exits. If the VM cannot run that command, use the reset button, then physical
  USB unplug, then VMware Ctrl+Alt / Removable Devices recovery.
- `manual_calibrated` is reserved for future manual-baseline data; it currently uses the natural envelope rather than reading a calibration file.

Arduino RuneLite movement is fail-closed after any wild cursor behavior. Live
`--execute` and `--hover-only` with `--backend arduino` now report
`arduino_pointer_calibration_required` until the no-click calibration has been
reviewed or an explicit unsafe override is supplied. Jagex Launcher automation
is disabled by default; bootstrap/login output reports
`launcherAutomationAllowed`, `launcherAutomationBlockedReason`, and
`loginRecoveryMode`, and stops at manual login when launcher or credential
surfaces are reached without `--allow-jagex-launcher-automation`.

## External Knowledge And Authoring Outputs

External OSRS knowledge is stored under:

```text
%USERPROFILE%\.osrs-telemetry\external_knowledge_cache
```

The cache contains bounded JSON files such as `item_id_map.json`,
`item_name_map.json`, `object_knowledge.json`, `npc_knowledge.json`,
`skill_requirements.json`, `location_knowledge.json`, `source_status.json`, and
small `wiki_page_cache\` entries from explicit searches. These files are
advisory static enrichment and do not replace live RuneLite truth.

Useful commands:

```powershell
python telemetry-viewer\context_service.py --external-knowledge-status
python telemetry-viewer\context_service.py --external-lookup-item-id 1511
python telemetry-viewer\context_service.py --external-get-skill-requirement Oak
python telemetry-viewer\context_service.py --probe-task "woodcutting and bank logs"
```

The cache has a default size budget of 500 MB. External API calls are disabled
by default, require explicit refresh/search permission, use a descriptive
User-Agent, and are never made from executor hot loops.
