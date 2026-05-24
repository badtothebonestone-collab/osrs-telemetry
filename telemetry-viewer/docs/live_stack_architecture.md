# Live Stack Architecture

This document defines the intended shape of the live Python sidecar stack. It is a guardrail for cleanup and future work; it is not a request to add new runtime behavior.

## A. Runtime Stack

1. RuneLite plugin: read-only telemetry source
   - Produces plugin snapshot payloads and, in non snapshot-no-files modes, live packet/session artifacts.
   - `127.0.0.1:8893` is the opt-in Java `PluginSnapshotEndpoint` (`/health`, `/schema`, `/snapshot`, and preset helper endpoints). It is localhost/read-only by default and is required when the Python daemon is started with `--input-source plugin-snapshot`.
   - Java behavior should not change during Python cleanup unless the task explicitly requires it.

2. Live target processor
   - Canonical candidate generation and live file writer.
   - Owns target profile filtering, dedupe, scoring, liveness interpretation, overlay debug state generation, and live file/cache materialization.
   - Current implementation: `live_target_processor.py` plus `target_library.json` and `target_profiles.json`.
   - For active live validation, bind it to the daemon session with `--from-daemon --daemon-url http://127.0.0.1:8890`; do not use blind `--latest-session` when a newer empty filesystem session exists.

3. Context daemon/service
   - Provides compact current state from plugin snapshots and analyzers.
   - Owns daemon `/status`, `/health`, `/control`, context service state, Mission Control state, and optional overlay writing.
   - Current implementation: `live_core_daemon.py`, `context_service.py`, `live_context_query.py`, and analyzer modules.
   - Service navigation can attach a `service_route_context.v1` route prior when service is needed but no bank target is visible. Route priors are low-confidence hints; live RuneLite telemetry remains authoritative for object ids, plane, geometry, menu actions, and whether an interaction is currently possible.

4. Action proposer
   - Builds the next proposed action from current context without executing input.
   - Current canonical code: `input_control/action_proposal.py`; compatibility import: `action_proposal_core.py`.
   - Shared target explanation comes from `candidate_core.explain_candidate`.

5. Executor
   - Optional input execution only after readiness passes.
   - Current canonical code: `input_control/executor.py`, `input_control/action_lifecycle.py`, input geometry, and backends.
   - `execute_next_action.py` is the canonical CLI for dry-run/explanation and bounded execution.
   - The executor uses `client_tick_core.py` for fast hover/menu confirmation. It does not parse RuneLite menu strings or clicked-menu events directly.
   - Live motor output goes through `input_control/human_input_controller.py` before it reaches a backend. Backends remain the low-level adapters for `pyautogui` and `pydirectinput`.

6. Client-tick interaction layer
   - Source: the RuneLite plugin samples `ClientTick`, `PostMenuSort`, and `MenuOptionClicked` into a bounded in-memory hot cache.
   - `PostMenuSort` predicts what the next left click will do at the current mouse position.
   - `MenuOptionClicked` proves what action the client actually accepted after a click.
   - The Java endpoint exposes compact `client_tick_hot.v1` state through `/snapshot` as `clientTickHot`, while preserving legacy `hoverMenu` and `lastMenuOptionClicked` top-level fields.
   - Python uses this layer for hover confirmation, clicked-menu classification, daemon status summaries, and `action_trace.v2`.

7. Human input governor
   - Fast perception stays fast: plugin snapshot, client ticks, hover confirmation, projection updates, and action scoring may poll at tight intervals.
   - Motor output is profile-driven: mouse movement, click timing, camera key holds/drags, and reaction/reacquisition delays use `HumanInputController`.
   - Profiles are `instant_debug`, `steady`, `natural`, and reserved `manual_calibrated`. `--movement-profile` remains a lower-level path generator; `--input-profile` controls the motor envelope.
   - The governor records movement, click-hold, reaction-delay, camera-hold, and direct-backend-bypass metrics in `action_trace.v2`.

8. Diagnostics/reporting
   - Diagnostics explain current state and source agreement. They must not become new runtime sources of truth.
   - Canonical live diagnostics:
     - Readiness: `diagnose_live_readiness.py`
     - Woodcutting candidates: `diagnose_woodcutting_candidates.py`
     - Visual/highlighter: `target_geometry_inspector.py`
     - Action dry run: `execute_next_action.py --dry-run --explain-target --verify-coordinates`

## B. Source-Of-Truth Rules

1. Daemon session vs latest-session file source
   - The daemon session is the action source of truth.
   - `--latest-session` is useful for file inspection, but it can be stale if a newer telemetry session has no live outputs.
   - Shared rule: prefer the daemon session for action/highlighter checks when it has live overlay output; otherwise compare it against the newest session with live outputs.
   - Tools that need to inspect the active live run should prefer `--from-daemon` or an explicit `--session` path over blind filesystem newest-session selection.
   - Canonical modules: `live_session_core.py`, `candidate_core.py`, `live_readiness_core.py`.

2. Candidate source vs highlighter source
   - The selected target used for an action must be present in the highlighter/overlay source before execution.
   - The candidate diagnostic and readiness diagnostic must use the same target identity/matching logic from `candidate_core.py`.
   - `target_geometry_inspector.py --from-daemon --live` must use the same session resolution rule.

3. Freshness and tick matching
   - Candidate freshness uses daemon freshness domains, selected target tick, latest daemon tick, and daemon status age.
   - A selected target whose tick trails latest tick by more than the current tolerance is stale.
   - Stale candidate data is a readiness FAIL for target-selection execution.

4. Debug overlay/live overlay JSON role
   - `overlay_debug_state.json` and overlay state are highlighter/debug sources, not independent action truth.
   - They are required for resource-target execution readiness because they prove the selected target is visible to the same highlighter source.
   - In snapshot-no-files daily mode, candidate files may be intentionally disabled; overlay debug state may still be present when `--write-overlay-state` is used.

5. When action execution must refuse
   - `execute_next_action.py --execute` must refuse when `actionReadiness.executionAllowed` is false. Overall context readiness may be `WARN` while the current action-specific readiness is `PASS`.
   - Refusal reasons include daemon unreachable, plugin snapshot unavailable while `inputSourceActive=plugin-snapshot`, stale or unavailable `client_tick_hot.v1` interaction state for hover-confirmed actions, daemon/latest-live session mismatch, missing live outputs required for the action, stale candidates, selected target not in highlighter source, missing target geometry, invalid raw aim point, missing safe visible aim point, off-screen target, UI-blocked target, or unavailable input geometry.
   - Stale `client_tick_hot.v1` blockers should distinguish login-screen/inactive-client recovery from true plugin/daemon hot-state failure. Readiness reports `gameState`, logged-in status, hot-state ages, `staleReason`, and a recovery hint.
   - Candidate validity is separate from actionability. A partially visible target may remain a valid Tree/Oak candidate but must not receive a click unless `safe_aimpoint.v1` finds a visible/interactable point inside the canvas and viewport margin.
   - When `--hover-confirm-target` is used, execution must move first, then require a fresh `PostMenuSort` sample at the intended canvas point whose top option/target matches the action intent. If confirmation times out or reports a rejected option such as `Walk here`, execution must skip the click.
   - `Cancel` hover is classified separately from `Walk here`. A lower `Cancel` sentinel entry does not block a valid left-click action, but a true left-click `Cancel` sample is a no-click blocker.
   - Hover mismatches and unsafe geometry skips are not action attempts. An action attempt starts only when the backend performs mouse down/up.
   - Repeated no-click hover failures may suppress a target/aimpoint for a short bounded window and reacquire another existing candidate from daemon/profile/overlay context. Suppression must never force a click; if all candidates are suppressed, the loop waits.
   - Suppression and reacquire counters are lifecycle-phase aware. A phase,
     intent, plane, or route-node change clears stale target suppression and
     starts a fresh budget for the current action bucket. Route-transition
     objects, service objects, resources, navigation waypoints, and camera
     recovery are reported with separate `reacquireBudgetType` values.
   - For `navigation_waypoint_action`, `Walk here` is a valid expected menu action. If the intended waypoint hover is a foreground object action, the executor may use camera-guided waypoint exposure: keep the same world tile, hold bounded camera input, continuously re-project that same tile while sampling client-tick hover state, move to the updated canvas point, release camera input as soon as fresh `Walk here` appears, and click only after that confirmation.
   - Camera reacquire must not become dense pixel scanning. It may use structured alternate route/path tiles first, then bounded same-tile camera follow. If the waypoint is still occluded or offscreen, execution must skip the click or use an explicit navigation-only fallback when available.
   - If a route object such as a staircase, ladder, door, gate, bank booth, banker, or deposit box is visible and hover-confirmable for the current service route, `interact_service_route_object` or a service action wins over another `walk_to` waypoint.
   - Open-field route navigation may select a farther structured route/path waypoint from the path horizon; transition/tight-geometry movement stays short and precise.
   - Live execution paths should report `directBackendBypassCount=0`. Low-level backend classes are the exception because they are the adapter layer.

## C. Canonical Schemas/Contracts

1. Candidate record
   - Source: live target processor, daemon in-memory status, or overlay marker.
   - Required for action use: name or targetName, classId/targetClass, target identity (`targetKey`, `objectKey`, or id/world tile), world tile, geometry/on-screen fields, aim point, source tick.
   - Shared explanation format: `candidate_explanation.v1` from `candidate_core.explain_candidate`.

2. Readiness result
   - Schema: `live_readiness.v2`.
   - Required top-level shape: `status`, `ready`, `currentIntent`, `actionReadiness`, `contextReadiness`, `blockers`, `warnings`, `session`, `daemon`, `liveFiles`, `candidates`, `highlighter`, `selectedTarget`, `freshness`, `inputGeometry`, `clientTickHot`, and `actionExecution`.
   - `actionReadiness` includes `status`, `executionAllowed`, `intent`, `blockers`, `warnings`, `checks`, `checksSkippedAsNotApplicable`, and `missingCapabilities`.
   - `contextReadiness` carries non-current-context warnings such as a resource selected-target/highlighter mismatch while the active proposal is service navigation.
   - Resource-object actions require selected resource/highlighter agreement, safe aimpoint, on-screen geometry, freshness, and hover-confirmable resource menu behavior.
   - Navigation waypoint actions require daemon/session freshness, input geometry, fresh `client_tick_hot.v1` state when the daemon uses plugin-snapshot input, an executable route/path waypoint, and an intent that allows `Walk here`; they do not require a Tree/Oak selected target to be present in the resource highlighter source.
   - Client tick hot capability details include freshness, latest PostMenuSort age, last clicked-menu age, `gameState`, logged-in status, and stale reason/recovery when unavailable for action.
   - Service-object actions require a visible/actionable service target, route-transition actions require a visible/actionable transition object such as stairs/door/ladder with the expected option, and interface dialogue choice actions require an active route-matching dialogue option.
   - Capability fields: `requiredCapabilities`, `optionalCapabilities`, and `capabilities`. Plugin snapshot is required only when the daemon's active input source is `plugin-snapshot`; otherwise it is optional and must not mask stale/mismatched target blockers.
   - Backward-compatible aliases: `sessions`, `candidateSource`, `overlay`, and `readinessPassed`.

3. Context response
   - Context responses must describe current daemon/session state and freshness, not silently invent replacement target logic.
   - Canonical context query/service code remains `live_context_query.py` and `context_service.py`.

4. Action proposal
   - Source: `input_control/action_proposal.py`.
   - Must include action, target kind/name, confidence, click or key action, required context, warnings, missing capabilities, input geometry, and `targetExplanation` when a target is involved.
   - Resource-target proposals must prefer `safeAimPoint` over raw object centers. If no safe visible aimpoint exists, the proposal may explain the target but must be non-executable with `safe_aimpoint` missing.
   - Route-transition proposals use `interact_service_route_object` only for a visible live object such as stairs/ladder/door with expected menu options. Static route anchors may drive navigation/scouting, but they are not clicked as transition objects.
   - A fresh client-tick hover sample that predicts a route transition option such as `Climb-up Staircase` may also create a route-transition proposal. Generic `Climb Staircase` is allowed only as a route-transition dialogue opener when the active route step declares it; it is not final transition success until the dialogue resolver selects the correct up/down option and live plane/location evidence changes.
   - `interface_dialogue_choice` is proposed when `dialogue_state.v1` is active and the current route step expects an up/down staircase option. Number-key selection wins when the option key is known; widget bounds are the fallback.
   - Route waypoint proposals include `routeWaypointSelection` when adaptive path selection chooses a structured route/path tile beyond the immediate next step.
   - If a static route prior is outside the local collision window, pathing can expose a bounded `local_frontier_waypoint` toward the prior. This gives the executor a nearby scout waypoint instead of a far no-projection anchor, but wall/fence corridors still stop as `route_wall_hugging_detected`.

5. Action trace
   - Source: `input_control/action_lifecycle.py` and `input_control/executor.py`.
   - Schema: `action_trace.v2`.
   - Must include proposed action, action intent, selected target explanation, safe/raw aimpoint fields, game tick before action, client tick/hover samples, mouse move start/end timestamps, intended canvas/screen point, accepted/rejected hover samples, click timestamp, clicked-menu before/after samples, clicked-menu classification, target suppression/reacquisition fields when applicable, dialogue prompt/option fields when applicable, pacing delay when applied, human input governor metrics, camera input metrics, action-specific readiness used for pre-action gating, game-tick verification timeline, optional final reconcile window/result, final classification, and warnings.
   - Service-route navigation adds `routeStability`, `navigationInProgress`, clicked waypoint tile, player tile after the click, movement state, and any replan-suppression reason. A movement click is not considered permission to immediately click the next waypoint while pathing still reports movement.
   - Resource actions add `resourceProgressClassification` when useful:
     `resource_click_confirmed_waiting`, `resource_animation_started_pending`,
     `resource_delayed_inventory_success`, `resource_target_depleted_success`,
     `resource_timeout_no_progress`, or
     `resource_timeout_reconciled_success`. A timeout reconciled by later
     inventory/progress evidence also records
     `delayedProgressReconciliation=true`.
   - Route-transition actions add `routeTransitionProgressClassification` when
     route-transition evidence is ambiguous. Pending values such as
     `return_transition_pending` mean a clicked stair has pathing,
     local-destination, dialogue, or movement evidence but no final plane/node
     change yet. Retry values such as `return_transition_retry_required` and
     `return_transition_retry_success` separate a no-evidence first attempt
     from a later same-object retry. Reconciled values are
     `route_transition_reconciled_success` and
     `return_transition_reconciled_success` and require later evidence that the
     original transition advanced the route.
   - Route-transition traces can include `routeTransitionLedgerEntry` with
     schema `route_transition_action_ledger.v1`. The ledger records action id,
     expected action, route node before/after, object identity, plane/player
     location before/after, local destination before/after, clicked-menu
     samples, retry relationship, and evidence booleans used for pending,
     retry-required, retry-success, or reconciled-success classification.

6. Safe visible aimpoint
   - Schema: `safe_aimpoint.v1`.
   - Required fields include `status`, `actionable`, `validButUnsafe`, `unsafeReasons`, `canvasX`, `canvasY`, `source`, `insideCanvas`, `insideViewport`, `insideInteractableRegion`, `uiBlocked`, `distanceToViewportEdgePx`, `distanceToCanvasEdgePx`, `clippedVisibleAreaPx`, `clippedVisibleAreaRatio`, `hoverConfirmed`, `rawAimPoint`, `sampledAimpoints`, `acceptedAimpoint`, `rejectedAimpoints`, and `rejectionReason`.
   - Sources include `hoverConfirmedVisibleHull`, `visibleHullInterior`, `clippedClickboxInterior`, `clickboxCenter`, `boundsCenter`, and `fallback`.
   - Off-viewport raw centers are clipped to visible/interactable geometry with a small margin; if no clipped safe area remains, action selection skips the candidate and reports unsafe reasons such as `centerOffViewport`, `noVisibleInteractableGeometry`, or `uiBlocked`. Projection sentinel coordinates such as `2147483647` are invalid aim points and must not satisfy readiness.

7. Client tick hot state
   - Schema: `client_tick_hot.v1`.
   - Required compact fields: `clientTick`, `wallTimeMillis`, `monotonicTimeNanos`, `gameTickAtSample`, `gameState`, `sessionId`, `sessionPath`, `mouse`, `postMenuSort`, `hoverMenu`, `lastMenuOptionClicked`, and `latency`.
   - `postMenuSort`/`hoverMenu` contains mouse canvas position, top option/target/type/id/params, entry count, capped entries, menu-open flag, and `sourceEvent=PostMenuSort`.
   - `lastMenuOptionClicked` contains option/target/type/id/params, mouse canvas position, and `sourceEvent=MenuOptionClicked`.
   - Explicit snapshot need `client_tick_tail` may include bounded `clientTickTail`, `postMenuSortTail`, and `clickedTail`; compact snapshots do not include unbounded client tick history.
   - For navigation waypoints, recent `postMenuSortTail` samples at the same canvas point are also used as a volatility guard. If the tail mixes `Walk here` with NPC/object/widget actions, the executor records `volatileHoverZone` and skips the click rather than trusting a single pre-click sample.

8. Dialogue state
   - Schema: `dialogue_state.v1`.
   - Source: bounded read-only plugin widget-root scan cached as `live_dialogue_state_packet.v1` and surfaced through plugin snapshots/daemon status.
   - Required fields include `active`, `type`, `promptText`, `options`, `canUseNumberKeys`, `canUseSpaceContinue`, `source`, `widgetRootIds`, `latestClientTick`, and `wallTimeMillis`.
   - Route-transition dialogue resolver uses this state for Lumbridge's `Climb up or down the stairs?` prompt. For `planeChange="+1"` it selects the `Climb up` option; for `planeChange="-1"` it selects `Climb down`. Selection goes through `HumanInputController`, using number keys first and widget bounds only as fallback.

9. Camera exposure score
   - Schema: `camera_exposure_score.v1`.
   - Source: `input_control/executor.py` using plugin tile projection, camera viewport, and `client_tick_hot.v1` hover samples.
   - Required fields include `classification`, `score`, `targetWorldTile`, `waypointCanvasPoint`, `projectionAvailable`, `projectionDeltaPx`, `mousePositionMatchesProjection`, `hoverOption`, `hoverTarget`, `hoverMenuClass`, `hoverMatchesWalkHere`, `blockingHoverOption`, `blockingHoverTarget`, `distanceToViewportEdgePx`, `waypointTileBounds`, `onScreen`, `geometryAvailable`, `cameraYaw`, `cameraPitch`, `yawDelta`, and `pitchDelta`.
   - Classifications include `exposed_walk_here`, `occluded_by_object`, `offscreen`, `edge_blocked`, `no_projection`, `no_camera_delta`, `worsening`, `timeout`, and `ambiguous`.
   - `action_trace.v2.reacquisition.cameraExposureAttempts` records each bounded closed-loop exposure attempt with `targetWorldTile`, `cameraMethod`, `cameraCommand`, held keys or drag pulse, projection before/after, camera viewport before/after, score before/after, compact projection/hover samples, follow error, and whether camera movement was actually observed. Loop summaries count only attempts where yaw/pitch or target projection changed.

10. Route projection status
   - Schema: `route_projection_status.v1`.
   - Source: plugin tile projection plus executor hover/projection checks.
   - Required fields include `worldTile`, `canvasPoint`, `canvasTileBounds`, `inCanvas`, `inViewport`, `degenerateProjection`, `tinyProjection`, `offscreen`, `uiBlocked`, `edgeClipped`, `edgeMarginPx`, `projectedVisibleAreaPx`, `projectedVisibleAreaRatio`, `partiallyOffscreen`, `objectOccluded`, `hoverOption`, `hoverTarget`, `projectionSource`, `actionableByCanvas`, `actionableByMinimap`, `classification`, and `rejectionReason`.
   - Classifications include `visible`, `edge_clipped`, `offscreen`, `degenerate`, `tiny_projection`, `occluded`, `no_projection`, and `not_actionable`. Visible/projected tiles can still be skipped if the hover proves an object/NPC action instead of the intended navigation action.
   - With `--reject-edge-route-clicks`, route waypoints closer than the configured edge margin or below the minimum visible-area ratio are demoted instead of clicked. With `--camera-reacquire-on-edge-projection`, those failures can trigger bounded camera-guided reacquisition before the executor falls back to alternates or a safe stop.

11. Service route context
   - Schema: `service_route_context.v1`.
   - Source: `service_route_core.py` and `profiles/service_routes.json`.
   - Required fields include `routeAvailable`, `routeId`, `routeVerifiedLive`, `routeStepStatus`, `routeNodes`, `routeEdges`, `currentNodeId`, `nextEdge`, `currentStep`, `currentNavigationTarget`, `visibleInteractionTarget`, `visibleServiceTarget`, `actionReady`, `interactionExpectedOptions`, `interactionExpectedTargets`, `expectedPlaneChange`, `observedAnchors`, `completedSteps`, and warnings/missing capabilities.
   - `routeContext` uses `route_context.v1` to classify the current source area
     before applying a source-specific route prior. It reports the current
     location/plane, current-area source, selected service goal, route mode,
     route-source status, selected approach node, and any route-source mismatch.
   - Route modes are `explicit_route`, `reverse_route`,
     `goal_directed_fallback`, `local_frontier_to_service`, and `unknown`.
     Known west-tree collection keeps the explicit Lumbridge route. An
     unexpected nearby tree/resource area with a known Lumbridge bank anchor
     switches to goal-directed fallback instead of forcing the west-tree source
     node.
   - In goal-directed fallback, the service anchor is a destination goal, not a
     click target. The route context chooses a destination-centered approach
     node, usually castle entrance/courtyard before stair search, and pathing
     emits a reachable local frontier waypoint that reduces distance toward
     that approach.
   - Approach nodes are marked complete by arrival or by passing the node along
     the bankward corridor, so a bridge/approach node is not reselected behind
     the player after useful progress.
   - Route-object status fields include `routeObjectsVisible`, `routeObjectsActionable`, `routeRelevantObjects`, `routeRelevantActionableObjects`, `visibleButRouteIrrelevantObjects`, `serviceObjectsVisible`, and `selectedRouteObjectPresent`. These are separate from resource target counts and can be non-zero when Tree/Oak target counts are zero.
   - `routeObjectCensus` uses `service_route_object_census.v1` to summarize transition/service objects from their own bounded route-object lane. It records source lane counts, rejected reasons, visible-but-irrelevant objects, and top objects with projection status and `route_relevance.v1`.
   - `serviceObjectCensus` uses `service_object_census.v1` at the bank/service stage. It counts Bank booth, Banker, Deposit box, and Bank chest candidates separately from route transitions and resources, records projection/actionability reasons, and exposes `selectedServiceObject`, `selectedServiceAction`, and service relevance when `open_service` can execute.
   - `route_relevance.v1` is the route-sanity gate for object intercept. It must pass route id/current step, object kind, expected action, expected plane or plane change, and route corridor/search-area checks before a staircase, ladder, door, bank booth, or banker can outrank waypoint walking.
   - Route graph nodes can be world tile anchors, visible object anchors, staircase/ladder transitions, bank/service targets, or fallback/scouting points. Edges describe operations such as `walk_to`, `reacquire_visible_target`, `interact_climb_up`, `wait_for_plane_change`, and `interact_bank`.
   - The Lumbridge Castle bank prior is staged through west approach, entrance/courtyard, first-stairs search, first climb-up, second climb-up, and bank service. Static coordinates remain low-confidence priors until live telemetry verifies them.
   - Acquisition order: visible service candidate first, route-relevant live transition/service object second, previously observed service anchor as a verified navigation target third, static route anchor as a low-confidence scouting target fourth, then `service_target_missing`.
   - Observed anchors are in-memory only and include object id/name/world tile/actions, confidence, live verification source, and last seen tick. Static route priors are never marked verified by themselves.
   - Multi-plane steps must verify transition progress through live state, such as plane/location change or route-step advancement, before the next step is trusted. Plane evidence may mark earlier stair steps completed, but it must not make an unseen future step clickable.
   - Bank/deposit service clicks may begin OSRS path-to-interact before the UI opens. The lifecycle verifier reports `service_object_pathing_to_object` when movement or service distance improves, and only reports service completion after bank/deposit UI state or inventory service evidence appears.
   - Bank/deposit UI telemetry may expose compact bank-side `inventorySlots`
     with widget bounds and actions. `bank_operation_context.v1` uses these to
     produce `resourceItemSlotBounds` and `resourceItemWidgets` for selective
     resource depositing when protected or non-resource inventory items make the
     generic deposit-inventory button unsafe. Once the bank is closed, a retained
     inventory summary with zero target resources keeps `bankingComplete=true`
     rather than reopening service.
   - After service completion, a stale `serviceNeeded=true` or route-relevant
     service object still visible at the bank floor is contextual only. If
     `bank_operation_context.v1` proves zero target resources remain and the
     inventory has room, the proposer should close the bank or return to
     resources instead of proposing another service interaction.
   - Tree/Oak hover while inventory is full is treated as a service-navigation
     blocker for that local frontier, not as permission to click the resource.
     The fallback should suppress or move past that frontier, try another
     approach/camera/path option, or stop with a clear
     `goal_directed_path_blocked` style reason.

12. Return route context
   - Schema: `return_route_context.v1`.
   - Source: `service_route_core.py` plus the same route prior used for service
     navigation. It is enabled only after service completion when
     `resource_return_context.v1` has a remembered, learned, or profile resource
     anchor.
   - Required fields include `sourceRouteId`, `returnRouteId`, `state`,
     `currentNodeId`, `nextEdge`, `targetResourceArea`, `resourceAnchor`,
     `returnActionReady`, and `returnBlockedReason`.
   - Lumbridge return routing descends from the bank floor through
     `Climb-down` route-transition steps, then uses staged ground-floor /
     courtyard / west-approach waypoints before resource reacquisition. A
     visible route-relevant down staircase outranks a plain return waypoint.
   - `interface_dialogue_choice` also applies to return transitions. For a
     generic Lumbridge staircase prompt, `planeChange="-1"` selects the
     `Climb down the stairs.` option; live plane/location evidence is still
     required before the route advances.
   - Post-service return-stair reacquisition uses the route-transition budget,
     not stale resource, service, or waypoint retry state. If the bank UI is
     closed, service is complete, and the relevant down staircase is
     visible/actionable, `interact_service_route_object` remains the preferred
     proposal before any return waypoint.

13. Navigation-in-progress and oscillation guard
   - After an actual `Walk here` click for `navigation_waypoint_action`, the executor may hold `navigationInProgress` instead of replanning. The hold clears when the clicked waypoint is reached, movement settles, a route object becomes actionable, or a bounded timeout/stuck condition is reported.
   - `--nav-replan-while-moving false` is the live-safe default. `--nav-min-game-ticks-between-clicks`, `--nav-stuck-game-ticks`, and `--nav-destination-arrival-distance` tune the motion lock.
   - Recent waypoint history detects immediate A-B-A cycles and repeated clicks into the same local tile. Those are reported as `route_oscillation_detected`, `route_backtracking_detected`, or `route_wall_hugging_detected` instead of sending another click into the same corridor.

14. Full lifecycle soak summary
   - `execute_next_action.py` loop summaries count full-cycle milestones
     separately from clicks: `lifecycleCyclesStarted`,
     `lifecycleCyclesCompleted`, `collectionPhasesStarted`,
     `inventoryFullEvents`, `serviceRoutesStarted`,
     `serviceRoutesCompleted`, `bankOpenEvents`, `depositSuccesses`,
     `serviceCompleteEvents`, `returnRoutesStarted`,
     `returnRoutesCompleted`, `resourceReacquisitions`,
     `postServiceResourceCollections`, and `postServiceLogsCollected`.
   - A cycle is complete only after service completion, return-route resource
     reacquisition, and at least one post-service resource collection.
   - Soak stop flags include `--stop-after-lifecycle-cycles`,
     `--stop-after-service-cycles`, `--stop-after-post-service-logs`,
     `--max-total-actions`, `--max-wall-time-minutes`,
     `--max-consecutive-no-progress`, and `--max-consecutive-timeouts`.
   - `--resource-reconcile-ms`, `--resource-reconcile-game-ticks`, and
     `--post-click-progress-tail-ticks` extend the bounded final reconcile for
     resource clicks only, so delayed inventory/resource evidence can convert
     an initial timeout into a reconciled success without hiding true failures.
   - Timeout summaries include `unresolvedTimeouts`, `timeoutReasons`,
     `timeoutActionTypes`, `timeoutsByIntent`, `timeoutRecoveredBy`,
     `resolvedByRetry`, `resolvedByLateEvidence`, `pendingButSafe`, and
     `evidenceAfterTimeout` so a successful soak can still show which actions
     had delayed, pending, retry-resolved, or unresolved verification.
   - Route-transition soak counters include `routeTransitionAttempts`,
     `routeTransitionFirstTrySuccesses`, `routeTransitionPending`,
     `routeTransitionRetryRequired`, `routeTransitionRetrySuccesses`,
     `routeTransitionTrueTimeouts`, and
     `routeTransitionReconciledSuccesses`. Transition-specific windows are
     controlled by `--transition-verify-ms`,
     `--transition-verify-game-ticks`, `--transition-pending-game-ticks`, and
     `--transition-retry-after-stall-ticks`.

15. Resource projection recovery
   - Schema: `resource_projection_status.v1`.
   - Source: `safe_aimpoint_core.py` and overlay/status summaries.
   - Resource candidates can be logically selected while still lacking
     executable geometry. Sentinel projection values such as `2147483647`,
     missing geometry, cap hits, offscreen/tiny projections, stale projection,
     and edge clipping are classified separately.
   - `bestLogicalResourceTarget` is the candidate the task wants to recover or
     inspect. `selectedExecutableResourceTarget` is non-null only when a valid
     safe aimpoint is available.
   - If a recoverable failure exists during collection, the proposer can emit
     `resource_view_recovery`. The executor performs bounded camera input
     through `HumanInputController` and does not click. A resource click remains
     gated on a fresh safe aimpoint and hover-confirmed resource action.
   - Recovery is counted as progress only when projection improves or a safe
     aimpoint appears. If sentinel/no-projection geometry remains unchanged
     through the bounded verification window, the action stops as
     `resource_projection_recovery_failed` instead of looping camera input.
   - Resource timeout reconciliation can keep watching when a target is freshly
     reacquired after a no-progress timeout, because delayed chop/inventory
     evidence may land after the first verification window.

16. Visual debug bundles
   - Schema: `visual_debug_bundle.v1`.
   - Source: `input_control/visual_debug_bundle.py` through
     `execute_next_action.py` when explicit screenshot debug flags are used.
   - Bundles are sparse, event-triggered, and capped. They can be captured for
     route-source mismatches, goal-directed fallback start, route wall-hugging
     and blocked-path states, alternate approach selection, service-anchor
     arrival, route-object reacquisition, failures, timeouts, camera/resource
     recovery, edge route rejections, lifecycle transitions, and final
     summaries.
   - Bundle directories contain `bundle.json`, optional `screenshot.png`,
     daemon status, overlay debug state when available, and a compact
     action-trace excerpt.
   - `bundle.json` includes enough telemetry for visual review without making
     pixels part of the click path: route mode, current node/edge, selected
     service anchor, selected approach node, selected waypoint, route/source
     mismatch details, pathing reason, wall-loop classification,
     projection/safe-aimpoint summaries, client-tick hover and clicked-menu
     summaries, human-input metrics when available, and the final decision.
   - Screenshots are not an action source of truth. They exist to audit the
     viewport and compare visual evidence against telemetry after a run. The
     runtime decision path remains telemetry-first: projection, safe aimpoint,
     client-tick hover, clicked-menu proof, route/service state, and
     HumanInputController output.

16. Diagnostic report
   - Diagnostics should use `PASS`, `WARN`, or `FAIL`.
   - JSON diagnostics print JSON to stdout and should not write files unless an explicit output path is requested.
   - Console wording is not a source-of-truth contract; JSON schema, status, warnings, and blockers are.

## D. Allowed Dependency Direction

- Runtime code may import core modules.
- Diagnostics may import core modules.
- Tests may import core modules and entrypoints.
- Core modules must not import diagnostics.
- Executor must not depend on diagnostic scripts.
- Diagnostics must not create new source-of-truth logic.

Current core modules:

- `live_session_core.py`: session/path and daemon session rules.
- `live_file_core.py`: live file/cache paths and safe loading.
- `candidate_core.py`: candidate identity, source matching, freshness, woodcutting classification summary, and candidate explanation.
- `live_readiness_core.py`: reusable readiness contract and action gate.
- `action_proposal_core.py`: compatibility import surface for proposal/explanation users.
- `client_tick_core.py`: `client_tick_hot.v1` parsing, generic action-intent matching, hover confirmation, and clicked-menu classification.
- `safe_aimpoint_core.py`: visible/interactable aimpoint selection and edge-safe actionability checks.
- `service_route_core.py`: low-confidence service route priors, live route-object matching, and bounded in-memory observed anchor state.

## E. Future Rule

Before adding a new diagnostic script, check whether the behavior belongs in:

- `live_readiness_core.py`
- `live_session_core.py`
- `live_file_core.py`
- `candidate_core.py`
- `input_control/action_proposal.py` or `action_proposal_core.py`
- `input_control/action_lifecycle.py`
- An existing canonical diagnostic

If a new script is still needed, document its input files, output files, source of truth, tests, and whether it can execute input.
