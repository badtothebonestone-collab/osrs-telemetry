from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from input_control.executor import LOOP_SCHEMA, backend_from_name, execute_action_loop, execute_next_action, run_camera_self_test


def bool_arg(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {value!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally execute one input action from daemon context.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--backend", choices=["pyautogui", "pydirectinput"], default="pyautogui")
    parser.add_argument("--input-profile", choices=["instant_debug", "steady", "natural", "manual_calibrated"], default="instant_debug")
    parser.add_argument("--movement-profile", choices=["instant_test", "linear_debug", "smooth_bezier", "fitts_guided", "wind_mouse"], default="linear_debug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--explain-target", action="store_true", help="Print the shared selected-target explanation when available.")
    parser.add_argument("--verify-coordinates", action="store_true", help="Resolve click coordinates without adding execution behavior.")
    parser.add_argument("--focus-runelite", dest="focus_runelite", action="store_true")
    parser.add_argument("--no-focus-runelite", dest="focus_runelite", action="store_false")
    parser.set_defaults(focus_runelite=None)
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--verify-after-action", action="store_true")
    parser.add_argument("--after-action-wait-ms", type=int, default=500)
    parser.add_argument("--hover-only", action="store_true", help="Move to the selected target and confirm hover/menu state without clicking.")
    parser.add_argument("--hover-confirm-target", action="store_true", help="Require fresh plugin hover menu to match the selected target before clicking.")
    parser.add_argument("--hover-confirm-timeout-ms", type=int, default=120)
    parser.add_argument("--hover-poll-ms", type=int, default=10)
    parser.add_argument("--hover-position-tolerance", type=int, default=3)
    parser.add_argument("--click-hold-ms", type=int, default=0)
    parser.add_argument("--client-tick-debug", action="store_true")
    parser.add_argument("--client-tick-tail", type=int, default=0)
    parser.add_argument("--menu-entry-limit", type=int, default=5)
    parser.add_argument("--require-clicked-menu-match", action="store_true")
    parser.add_argument("--record-client-hot", action="store_true")
    parser.add_argument("--client-hot-output", default="interaction_geometry/live/client_tick_hot.jsonl")
    parser.add_argument("--client-hot-window-ms", type=int, default=5000)
    parser.add_argument("--client-hot-max-samples", type=int, default=128)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--sessions-dir", help="Override telemetry sessions root for pre-action readiness checks.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--max-runtime-seconds", type=float, default=30.0)
    parser.add_argument("--cooldown-ms", type=int, default=1200)
    parser.add_argument("--action-timeout-ms", type=int, default=5000)
    parser.add_argument("--result-timeout-ms", type=int, default=15000)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--wait-for-ready", type=float, default=0.0, metavar="SEC", help="Wait for daemon/overlay/highlighter/input readiness before executing.")
    parser.add_argument("--stop-on-warn", action="store_true")
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--stop-after-inventory-changes", type=int)
    parser.add_argument("--stop-when-inventory-full", action="store_true")
    parser.add_argument("--max-successful-actions", type=int)
    parser.add_argument("--max-timeouts", type=int)
    parser.add_argument("--final-reconcile-ms", type=int, default=0)
    parser.add_argument("--final-reconcile-game-ticks", type=int, default=0)
    parser.add_argument("--resource-reconcile-ms", type=int, default=0)
    parser.add_argument("--resource-reconcile-game-ticks", type=int, default=0)
    parser.add_argument("--post-click-progress-tail-ticks", type=int, default=0)
    parser.add_argument("--stop-after-lifecycle-cycles", type=int)
    parser.add_argument("--stop-after-service-cycles", type=int)
    parser.add_argument("--stop-after-post-service-logs", type=int)
    parser.add_argument("--max-total-actions", type=int)
    parser.add_argument("--max-wall-time-minutes", type=float)
    parser.add_argument("--max-consecutive-no-progress", type=int)
    parser.add_argument("--max-consecutive-timeouts", type=int)
    parser.add_argument("--nav-verify-game-ticks", type=int, default=0)
    parser.add_argument("--nav-verify-ms", type=int, default=0)
    parser.add_argument("--nav-progress-min-distance", type=float, default=0.0)
    parser.add_argument("--transition-verify-game-ticks", type=int, default=0)
    parser.add_argument("--transition-verify-ms", type=int, default=0)
    parser.add_argument("--transition-pending-game-ticks", type=int, default=0)
    parser.add_argument("--transition-retry-after-stall-ticks", type=int, default=0)
    parser.add_argument("--pacing-profile", choices=["instant_debug", "steady", "natural"], default="instant_debug")
    parser.add_argument("--target-switch-min-ms", type=int, default=0)
    parser.add_argument("--target-switch-max-ms", type=int, default=0)
    parser.add_argument("--post-resource-min-ms", type=int, default=0)
    parser.add_argument("--post-resource-max-ms", type=int, default=0)
    parser.add_argument("--occasional-idle-chance", type=float, default=0.0)
    parser.add_argument("--occasional-idle-min-ms", type=int, default=0)
    parser.add_argument("--occasional-idle-max-ms", type=int, default=0)
    parser.add_argument("--target-hover-failure-limit", type=int, default=3)
    parser.add_argument("--target-suppression-ms", type=int, default=2500)
    parser.add_argument("--clear-suppression-on-progress", dest="clear_suppression_on_progress", action="store_true", default=True)
    parser.add_argument("--no-clear-suppression-on-progress", dest="clear_suppression_on_progress", action="store_false")
    parser.add_argument("--max-candidate-reacquire-rounds", type=int, default=3)
    parser.add_argument("--max-waypoint-alternates", type=int, default=12)
    parser.add_argument("--max-hover-checks-per-waypoint", type=int, default=0)
    parser.add_argument("--max-navigation-reacquire-rounds", type=int, default=3)
    parser.add_argument("--max-camera-adjustments-per-route-step", type=int, default=0)
    parser.add_argument("--camera-adjust-ms", type=int, default=0)
    parser.add_argument("--camera-adjust-direction", choices=["auto", "left", "right"], default="auto")
    parser.add_argument("--camera-reacquire-ms", type=int, default=0)
    parser.add_argument("--camera-reacquire-waypoint", action="store_true", help="For occluded navigation waypoints, nudge camera and reproject the same world tile before clicking.")
    parser.add_argument("--camera-method", choices=["auto", "keyboard_arrows", "keyboard_wasd", "middle_mouse_drag"], default="auto")
    parser.add_argument("--camera-exposure-max-ms", type=int, default=0)
    parser.add_argument("--camera-sample-interval-ms", type=int, default=None)
    parser.add_argument("--camera-max-direction-switches", type=int, default=None)
    parser.add_argument("--camera-allow-diagonal", action="store_true")
    parser.add_argument("--camera-reacquire-timeout-ms", type=int, default=0)
    parser.add_argument("--camera-probe-ms", type=int, default=120)
    parser.add_argument("--camera-max-nudges", type=int, default=0)
    parser.add_argument("--camera-follow-target", dest="camera_follow_target", action="store_true", default=True)
    parser.add_argument("--no-camera-follow-target", dest="camera_follow_target", action="store_false")
    parser.add_argument("--camera-min-score-improvement", type=int, default=1)
    parser.add_argument("--camera-min-projection-delta-px", type=float, default=2.0)
    parser.add_argument("--camera-allow-pitch-adjust", action="store_true")
    parser.add_argument("--camera-debug-summary", action="store_true")
    parser.add_argument("--camera-self-test", action="store_true")
    parser.add_argument("--camera-test-return", action="store_true")
    parser.add_argument("--reject-edge-route-clicks", action="store_true", help="Reject route waypoint clicks that are too close to the viewport/canvas edge or too clipped to be safe.")
    parser.add_argument("--camera-reacquire-on-edge-projection", action="store_true", help="Use camera-guided waypoint reacquisition when a useful route tile projects poorly at the edge.")
    parser.add_argument("--route-click-edge-margin-px", type=int, default=12)
    parser.add_argument("--route-min-visible-area-ratio", type=float, default=0.45)
    parser.add_argument("--allow-minimap-navigation", action="store_true", help="Reserved navigation-only fallback; ignored until reliable minimap click telemetry is available.")
    parser.add_argument("--route-waypoint-lookahead-tiles", type=int, default=12)
    parser.add_argument("--route-waypoint-max-horizon-tiles", type=int, default=25)
    parser.add_argument("--min-route-progress-tiles", type=int, default=3)
    parser.add_argument("--max-route-waypoint-distance", type=int, default=30)
    parser.add_argument("--prefer-long-visible-waypoint", action="store_true")
    parser.add_argument("--route-waypoint-distance-mode", choices=["adaptive", "precise", "next"], default="adaptive")
    parser.add_argument("--nav-replan-while-moving", nargs="?", const=True, default=False, type=bool_arg)
    parser.add_argument("--nav-min-game-ticks-between-clicks", type=int, default=3)
    parser.add_argument("--nav-stuck-game-ticks", type=int, default=6)
    parser.add_argument("--nav-destination-arrival-distance", type=int, default=1)
    parser.add_argument("--no-safe-target-wait-ms", type=int, default=150)
    parser.add_argument("--suppressed-target-wait-ms", type=int, default=75)
    parser.add_argument("--capture-debug-screenshots", action="store_true", help="Capture sparse event-triggered visual debug bundles.")
    parser.add_argument("--screenshot-on-failure", action="store_true", help="Capture a debug bundle for execution failures and menu mismatches.")
    parser.add_argument("--screenshot-on-camera-recovery", action="store_true", help="Capture debug bundles around camera/reprojection recovery events.")
    parser.add_argument("--screenshot-on-timeout", action="store_true", help="Capture debug bundles for resource/navigation timeout events.")
    parser.add_argument("--screenshot-on-edge-reject", action="store_true", help="Capture debug bundles when an edge-clipped route click is rejected.")
    parser.add_argument("--screenshot-on-lifecycle-transition", action="store_true", help="Capture debug bundles for major lifecycle transitions.")
    parser.add_argument("--max-debug-screenshots", type=int, default=20)
    parser.add_argument("--debug-screenshot-dir")
    parser.add_argument("--summary-every-action", action="store_true")
    return parser.parse_args(argv)


def apply_focus_default(args: argparse.Namespace) -> argparse.Namespace:
    if args.hover_only:
        args.execute = False
    if args.max_total_actions:
        args.max_actions = max(args.max_actions, int(args.max_total_actions))
        args.loop = True
    if args.max_wall_time_minutes:
        args.max_runtime_seconds = max(args.max_runtime_seconds, float(args.max_wall_time_minutes) * 60.0)
        args.loop = True
    if (args.stop_after_inventory_changes or args.stop_when_inventory_full or args.max_successful_actions or args.max_timeouts) and args.max_actions <= 1:
        args.max_actions = max(args.max_actions, int(args.stop_after_inventory_changes or args.max_successful_actions or 1))
        args.loop = True
    if args.stop_after_lifecycle_cycles or args.stop_after_service_cycles or args.stop_after_post_service_logs:
        args.loop = True
    if args.focus_runelite is None:
        args.focus_runelite = bool((args.execute or args.hover_only or args.camera_self_test) and args.backend == "pyautogui")
    args.require_live_readiness = bool(args.execute or args.hover_only)
    return args


def client_hot_records_from_payload(payload: dict[str, Any], *, max_samples: int = 128) -> list[dict[str, Any]]:
    results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else [payload]
    records: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        trace = result.get("actionTrace") if isinstance(result, dict) and isinstance(result.get("actionTrace"), dict) else {}
        client_tick = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
        for sample_kind, key in (
            ("accepted_hover", "acceptedHoverSample"),
            ("last_click_before", "lastMenuOptionClickedBefore"),
            ("last_click_after", "lastMenuOptionClickedAfter"),
        ):
            sample = client_tick.get(key)
            if isinstance(sample, dict):
                records.append(
                    {
                        "schema": "client_tick_hot_record.v1",
                        "actionIndex": index,
                        "sampleKind": sample_kind,
                        "sample": sample,
                    }
                )
        for sample in client_tick.get("rejectedHoverSamples") or []:
            if isinstance(sample, dict):
                records.append(
                    {
                        "schema": "client_tick_hot_record.v1",
                        "actionIndex": index,
                        "sampleKind": "rejected_hover",
                        "sample": sample.get("sample") if isinstance(sample.get("sample"), dict) else sample,
                        "reason": sample.get("reason"),
                    }
                )
    return records[-max(0, int(max_samples or 0)) :]


def maybe_record_client_hot(payload: dict[str, Any], args: argparse.Namespace) -> None:
    if not args.record_client_hot:
        return
    records = client_hot_records_from_payload(payload, max_samples=args.client_hot_max_samples)
    output = Path(args.client_hot_output)
    if not output.is_absolute():
        session_path = None
        readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
        session = readiness.get("session") if isinstance(readiness.get("session"), dict) else {}
        if isinstance(session.get("activeSessionPath"), str):
            session_path = Path(session["activeSessionPath"])
        output = (session_path / output if session_path else output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=False) + "\n")


def format_human(payload: dict[str, Any]) -> str:
    if payload.get("schema") == LOOP_SCHEMA:
        summary = payload.get("loopSummary") if isinstance(payload.get("loopSummary"), dict) else {}
        lines = [
            f"EXECUTE ACTION LOOP - {payload.get('status') or 'UNKNOWN'}",
            "",
            f"Mode: {'dry-run' if payload.get('dryRun') else 'execute'}",
            f"Executed actions: {payload.get('executedActionCount', 0)} / {payload.get('maxActions', 'unknown')}",
            f"Reason: {payload.get('reason') or 'unknown'}",
            "",
            "Summary:",
            f"  Proposed actions: {summary.get('proposedActions', payload.get('actionResultCount', 0))}",
            f"  Actual click attempts: {summary.get('actionsAttempted', payload.get('executedActionCount', 0))}",
            f"  Actions executed: {summary.get('actionsExecuted', payload.get('executedActionCount', 0))}",
            f"  Hover checks: {summary.get('hoverChecks', 0)}",
            f"  Skips: unsafe geometry={summary.get('skippedUnsafeGeometry', 0)} hover mismatch={summary.get('skippedHoverMismatch', 0)} stale client tick={summary.get('skippedStaleClientTick', 0)} suppressed targets={summary.get('targetsSuppressed', 0)} no-progress suppressions={summary.get('targetNoProgressSuppressions', 0)}",
            f"  Navigation: occluded waypoints={summary.get('waypointOccludedByObject', 0)} alternate attempts={summary.get('navigationAlternateAttempts', 0)} camera adjustments={summary.get('cameraAdjustments', 0)} edge rejects={summary.get('edgeRouteClicksRejected', 0)} edge camera={summary.get('cameraReacquireOnEdgeCount', 0)} volatile skips={summary.get('volatileHoverSkips', 0)} menu flips={summary.get('menuFlipMismatchCount', 0)}",
            f"  Route stability: motion waits={summary.get('navigationInProgressWaits', 0)} replan suppressed={summary.get('routeReplanSuppressedWhileMoving', 0)} oscillation={summary.get('routeOscillationDetections', 0)} backtracking={summary.get('routeBacktrackingDetections', 0)} barrier={summary.get('routeBarrierDetections', 0)}",
            f"  Route transitions: attempts={summary.get('routeTransitionAttempts', 0)} firstTry={summary.get('routeTransitionFirstTrySuccesses', 0)} pending={summary.get('routeTransitionPending', 0)} retryRequired={summary.get('routeTransitionRetryRequired', 0)} retrySuccess={summary.get('routeTransitionRetrySuccesses', 0)} reconciled={summary.get('routeTransitionReconciledSuccesses', 0)} trueTimeouts={summary.get('routeTransitionTrueTimeouts', 0)}",
            f"  Successful actions: {summary.get('successfulActions', 0)}",
            f"  Timeouts: {summary.get('timeouts', 0)} unresolved={summary.get('unresolvedTimeouts', 0)} trueUnresolved={summary.get('trueUnresolvedTimeouts', summary.get('unresolvedTimeouts', 0))} classifications={summary.get('timeoutClassifications', {})}",
            f"  Timeout details: reasons={summary.get('timeoutReasons', {})} actions={summary.get('timeoutActionTypes', {})} intents={summary.get('timeoutsByIntent', {})} recoveredBy={summary.get('timeoutRecoveredBy', {})} resolvedByRetry={summary.get('resolvedByRetry', 0)} resolvedByLateEvidence={summary.get('resolvedByLateEvidence', 0)} pendingButSafe={summary.get('pendingButSafe', 0)}",
            f"  Delayed reconciliations: {summary.get('delayedProgressReconciliations', 0)} resource timeout recoveries={summary.get('resourceTimeoutReconciledSuccesses', 0)}",
            f"  Recoverable goal retries: {summary.get('recoverableFailuresAfterGoal', 0)}" if summary.get("goalReachedWithRecoverableFailures") else None,
            f"  Inventory changes: {summary.get('inventoryChanges', 0)}",
            f"  Inventory free slots: {summary.get('inventoryFreeSlotsStart', 'unknown')} -> {summary.get('inventoryFreeSlotsEnd', 'unknown')}",
            f"  Resource count: {summary.get('resourceCountStart', 'unknown')} -> {summary.get('resourceCountEnd', 'unknown')}",
            f"  Progress: {summary.get('progressStart', 'unknown')} -> {summary.get('progressEnd', 'unknown')}",
            f"  Lifecycle cycles: started={summary.get('lifecycleCyclesStarted', 0)} completed={summary.get('lifecycleCyclesCompleted', 0)} serviceComplete={summary.get('serviceCompleteEvents', 0)} returnComplete={summary.get('returnRoutesCompleted', 0)} post-service logs={summary.get('postServiceLogsCollected', 0)}",
            f"  Hover confirms: {summary.get('hoverConfirmSuccesses', 0)} pass / {summary.get('hoverConfirmFailures', 0)} fail",
            f"  Hover failures: cancel={summary.get('cancelHoverFailures', 0)} Walk here={summary.get('walkHereHoverFailures', 0)} stale={summary.get('staleHoverSamples', 0)} volatile={summary.get('volatileHoverFailures', 0)}",
            f"  Menu clicks: {summary.get('expectedMenuClicks', 0)} expected / {summary.get('walkHereClicks', 0)} Walk here / {summary.get('cancelClicks', 0)} Cancel",
            f"  Hover latency ms: min={summary.get('hoverLatencyMinMillis', 'n/a')} avg={summary.get('hoverLatencyAvgMillis', 'n/a')} max={summary.get('hoverLatencyMaxMillis', 'n/a')}",
            f"  Pacing delays ms: count={summary.get('pacingDelayCount', 0)} min={summary.get('pacingDelayMinMillis', 'n/a')} avg={summary.get('pacingDelayAvgMillis', 'n/a')} max={summary.get('pacingDelayMaxMillis', 'n/a')}",
            f"  Input profile: {summary.get('inputProfile') or 'unknown'} | mouse move avg ms={summary.get('averageMouseMoveMs', 'n/a')} click hold avg ms={summary.get('averageClickHoldMs', 'n/a')} reaction avg ms={summary.get('averageReactionDelayMs', 'n/a')}",
            f"  Camera hold ms: min={summary.get('cameraHoldMinMs', 'n/a')} avg={summary.get('cameraHoldAvgMs', 'n/a')} max={summary.get('cameraHoldMaxMs', 'n/a')} switches={summary.get('cameraDirectionSwitches', 0)}",
            f"  Direct backend bypasses: {summary.get('directBackendBypassCount', 0)}",
            f"  Debug screenshots: captured={summary.get('debugScreenshotBundlesCaptured', 0)} failures={summary.get('debugScreenshotCaptureFailures', 0)} skippedByLimit={summary.get('debugScreenshotBundlesSkippedByLimit', 0)}",
            f"  Reacquire waits: {summary.get('targetReacquireWaits', 0)} ({summary.get('targetReacquireWaitMillis', 0)} ms)",
            f"  Final reconcile: {summary.get('finalReconcileResult') or 'none'} ({summary.get('finalReconcileMillis', 0)} ms, {summary.get('finalReconcileGameTicks', 0)} ticks)",
            f"  Final cycle stage: {summary.get('finalCycleStage') or 'unknown'}",
            f"  Final phase/intent: {summary.get('finalPhase') or 'unknown'} / {summary.get('finalActiveIntent') or 'unknown'}",
            f"  Last observed signals: {', '.join(str(item) for item in (summary.get('lastObservedSignals') or [])) or 'none'}",
            "",
            "Actions:",
        ]
        lines = [line for line in lines if line is not None]
        action_results = payload.get("actionResults") if isinstance(payload.get("actionResults"), list) else []
        if action_results:
            for index, action_result in enumerate(action_results, start=1):
                proposal = action_result.get("proposal") if isinstance(action_result.get("proposal"), dict) else {}
                lifecycle = action_result.get("lifecycleState") if isinstance(action_result.get("lifecycleState"), dict) else {}
                observed = action_result.get("observedResult") if isinstance(action_result.get("observedResult"), dict) else {}
                commands = action_result.get("commands") if isinstance(action_result.get("commands"), list) else []
                lines.extend(
                    [
                        f"  {index}. {action_result.get('proposedAction') or 'none'} -> {proposal.get('targetName') or 'none'}",
                        f"     command: {commands[0] if commands else 'none'}",
                        f"     expected: {(action_result.get('expectedResult') or {}).get('resultType') if isinstance(action_result.get('expectedResult'), dict) else 'unknown'}",
                        f"     observed: {observed.get('observedResult') or 'unknown'}",
                        f"     outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
                        f"     lifecycle: {lifecycle.get('currentState') or 'unknown'} reason={lifecycle.get('reason') or 'unknown'}",
                    ]
                )
        else:
            lines.append("  none")
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        lines.extend(["", "Warnings:"])
        lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
        return "\n".join(lines).rstrip() + "\n"
    proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
    movement = payload.get("movementPlan") if isinstance(payload.get("movementPlan"), dict) else {}
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
    lifecycle = payload.get("lifecycleState") if isinstance(payload.get("lifecycleState"), dict) else {}
    observed = payload.get("observedResult") if isinstance(payload.get("observedResult"), dict) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    action_readiness = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}
    hover = payload.get("hoverConfirmation") if isinstance(payload.get("hoverConfirmation"), dict) else {}
    trace = payload.get("actionTrace") if isinstance(payload.get("actionTrace"), dict) else {}
    human_input = trace.get("humanInput") if isinstance(trace.get("humanInput"), dict) else {}
    reacquisition = trace.get("reacquisition") if isinstance(trace.get("reacquisition"), dict) else {}
    hover_sample = hover.get("sample") if isinstance(hover.get("sample"), dict) else hover.get("latestHoverMenu") if isinstance(hover.get("latestHoverMenu"), dict) else {}
    latest_match = hover.get("latestMatch") if isinstance(hover.get("latestMatch"), dict) else {}
    if not hover_sample and isinstance(latest_match.get("sample"), dict):
        hover_sample = latest_match.get("sample")
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    lines = [
        f"EXECUTE NEXT ACTION - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Mode: {'dry-run' if payload.get('dryRun') else 'execute'}",
        f"Backend: {payload.get('backend') or 'unknown'}",
        f"Movement profile: {payload.get('movementProfile') or 'unknown'}",
        f"Input profile: {human_input.get('profile') or 'unknown'}",
        "",
        "Proposal:",
        f"  Action: {proposal.get('proposedAction') or payload.get('proposedAction')}",
        f"  Target: {proposal.get('targetName') or 'none'}",
        f"  Reason: {proposal.get('reason') or 'unknown'}",
        f"  Click point space: {proposal.get('clickPointSpace') or 'unknown'}",
        f"  Canvas click point: {proposal.get('suggestedClickPoint') or 'none'}",
        f"  Screen click point: {proposal.get('resolvedScreenClickPoint') or resolution.get('screenClickPoint') or 'none'}",
        f"  Conversion: {resolution.get('method') or 'unknown'}",
        f"  Key action: {proposal.get('keyAction') or 'none'}",
        "",
        "Movement:",
        f"  Duration ms: {movement.get('durationMs', 'n/a')}",
        f"  Point count: {movement.get('pointCount', 'n/a')}",
        f"  Click point: {movement.get('clickPoint', 'n/a')}",
        f"  Governed avg move/click ms: {human_input.get('averageMouseMoveMs', 'n/a')} / {human_input.get('averageClickHoldMs', 'n/a')}",
        f"  Direct backend bypasses: {human_input.get('directBackendBypassCount', 0)}",
        "",
        "Lifecycle:",
        f"  State: {lifecycle.get('currentState') or 'unknown'}",
        f"  Expected: {(payload.get('expectedResult') or {}).get('resultType') if isinstance(payload.get('expectedResult'), dict) else 'unknown'}",
        f"  Observed: {observed.get('observedResult') or 'unknown'}",
        f"  Signals: {', '.join(str(item) for item in (observed.get('observedSignals') or lifecycle.get('observedSignals') or [])) or 'none'}",
        f"  Outcome: {observed.get('resultOutcome') or lifecycle.get('resultOutcome') or 'unknown'} | complete={observed.get('resultComplete') if observed.get('resultComplete') is not None else lifecycle.get('resultComplete')}",
        f"  Next action allowed: {observed.get('nextActionAllowed') if observed.get('nextActionAllowed') is not None else lifecycle.get('nextActionAllowed')}",
        f"  Verification: {payload.get('verificationStatus') or 'unknown'}",
        f"  Next allowed: {payload.get('nextAllowedAt') or 'unknown'}",
        "",
        "Hover confirmation:",
        f"  Status: {hover.get('status') or 'not requested'}",
        f"  Confirmed: {hover.get('confirmed') if hover.get('confirmed') is not None else 'unknown'}",
        f"  Latency ms: {hover.get('latencyMillis', 'n/a')}",
        f"  Reason: {hover.get('reason') or 'unknown'}",
        f"  Top menu: {hover_sample.get('topOption') or hover_sample.get('option') or 'unknown'} {hover_sample.get('topTarget') or hover_sample.get('target') or ''}".rstrip(),
        f"  Clicked menu: {((hover.get('lastMenuOptionClickedAfter') or {}).get('option') if isinstance(hover.get('lastMenuOptionClickedAfter'), dict) else None) or 'unknown'} {((hover.get('lastMenuOptionClickedAfter') or {}).get('target') if isinstance(hover.get('lastMenuOptionClickedAfter'), dict) else None) or ''}".rstrip(),
        f"  Click classification: {hover.get('clickClassification') or observed.get('menuClickClassification') or 'unknown'}",
        f"  Action classification: {observed.get('actionResultClassification') or 'unknown'}",
        "",
        "Camera reacquire:",
        f"  Attempts: {len(reacquisition.get('cameraExposureAttempts') or []) if isinstance(reacquisition.get('cameraExposureAttempts'), list) else 0}",
        f"  Reacquired by camera: {reacquisition.get('waypointReacquiredByCamera') if reacquisition.get('waypointReacquiredByCamera') is not None else 'unknown'}",
        "",
        "Pre-action readiness:",
        f"  Status: {readiness.get('status') or 'not checked'}",
        f"  Proposed action: {readiness.get('proposedAction') or 'unknown'}",
        f"  Current intent: {readiness.get('currentIntent') or 'unknown'}",
        f"  Action readiness: {action_readiness.get('status') or 'unknown'}",
        f"  Execution allowed: {action_readiness.get('executionAllowed') if action_readiness.get('executionAllowed') is not None else 'unknown'}",
        f"  Passed: {readiness.get('readinessPassed') if readiness.get('readinessPassed') is not None else 'unknown'}",
        "",
    ]
    if explanation:
        freshness = explanation.get("freshness") if isinstance(explanation.get("freshness"), dict) else {}
        lines.extend(
            [
                "Selected target:",
                f"  Name: {explanation.get('name') or 'unknown'}",
                f"  Object id: {explanation.get('objectId') if explanation.get('objectId') is not None else explanation.get('id', 'unknown')}",
                f"  Class/profile: {explanation.get('classId') or 'unknown'} / {explanation.get('profile') or 'unknown'} match={explanation.get('profileMatch')}",
                f"  Rank/score: {explanation.get('rank') if explanation.get('rank') is not None else 'unknown'} / {explanation.get('score') if explanation.get('score') is not None else 'unknown'}",
                f"  World: {explanation.get('worldLocation') or explanation.get('world') or 'unknown'}",
                f"  Aim point: {explanation.get('canvasAimPoint') or explanation.get('aimPoint') or 'unknown'}",
                f"  Safe aim point: {explanation.get('safeAimPoint') or 'none'}",
                f"  Geometry/on-screen: {explanation.get('geometryStatus') or explanation.get('geometryAvailable')} / {explanation.get('onScreenStatus') or explanation.get('onScreen')}",
                f"  Freshness: {freshness.get('status') or explanation.get('freshness') or 'unknown'}",
                f"  Accepted reasons: {', '.join(str(item) for item in (explanation.get('acceptedReasons') or [])) or 'none'}",
                f"  Rejected/demoted reasons: {', '.join(str(item) for item in (explanation.get('rejectedReasons') or [])) or 'none'}",
                "",
            ]
        )
    lines.append("Commands:")
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    lines.extend(f"  {command}" for command in commands) if commands else lines.append("  none")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def format_camera_self_test(payload: dict[str, Any]) -> str:
    lines = [
        f"CAMERA SELF TEST - {payload.get('status') or 'UNKNOWN'}",
        "",
        f"Selected method: {payload.get('selectedMethod') or 'none'}",
        f"Calibration file: {payload.get('calibrationPath') or 'not written'}",
        "",
        "Methods:",
    ]
    for result in payload.get("methodResults") or []:
        if not isinstance(result, dict):
            continue
        lines.append(
            "  "
            + f"{result.get('method')}: status={result.get('status')} "
            + f"yawDelta={result.get('yawDelta')} pitchDelta={result.get('pitchDelta')} "
            + f"reason={result.get('reason') or 'none'}"
        )
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        args.execute = False
    apply_focus_default(args)
    backend = backend_from_name(
        args.backend,
        focus_runelite=args.focus_runelite,
        window_title_filter=args.window_title_filter,
    )
    if args.camera_self_test:
        payload = run_camera_self_test(args.snapshot_url, args, backend=backend)
        print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_camera_self_test(payload))
        return 0 if payload.get("status") != "FAIL" else 1
    run_loop = bool(args.loop or args.max_actions > 1)
    result = execute_action_loop(args.daemon_url, args, backend=backend) if run_loop else execute_next_action(args.daemon_url, args, backend=backend)
    payload = result.to_dict()
    maybe_record_client_hot(payload, args)
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
