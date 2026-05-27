from __future__ import annotations

import argparse
import json

from live_readiness_core import build_readiness_report


def yn(value) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return "unknown"
    return str(value)


def format_target(target: dict | None) -> str:
    if not target:
        return "none"
    world = "unknown"
    if target.get("worldX") is not None and target.get("worldY") is not None:
        world = f"{target.get('worldX')},{target.get('worldY')},{target.get('plane', 0)}"
    return (
        f"{target.get('name') or 'unknown'} id={target.get('id') if target.get('id') is not None else 'unknown'} "
        f"class={target.get('classId') or 'unknown'} world={world} score={target.get('score') if target.get('score') is not None else 'unknown'}"
    )


def format_human(report: dict) -> str:
    daemon = report.get("daemon") if isinstance(report.get("daemon"), dict) else {}
    sessions = report.get("sessions") if isinstance(report.get("sessions"), dict) else {}
    overlay = report.get("overlay") if isinstance(report.get("overlay"), dict) else {}
    candidate = report.get("candidateSource") if isinstance(report.get("candidateSource"), dict) else {}
    freshness = candidate.get("freshness") if isinstance(candidate.get("freshness"), dict) else {}
    checks = report.get("selectedTargetChecks") if isinstance(report.get("selectedTargetChecks"), dict) else {}
    action_readiness = report.get("actionReadiness") if isinstance(report.get("actionReadiness"), dict) else {}
    context_readiness = report.get("contextReadiness") if isinstance(report.get("contextReadiness"), dict) else {}
    geometry = report.get("inputGeometry") if isinstance(report.get("inputGeometry"), dict) else {}
    client_hot = report.get("clientTickHot") if isinstance(report.get("clientTickHot"), dict) else {}
    lines = [
        f"LIVE READINESS - {report.get('status') or 'UNKNOWN'}",
        f"  current intent: {report.get('currentIntent') or 'unknown'}",
        f"  action readiness: {action_readiness.get('status') or 'unknown'}",
        f"  execution allowed: {yn(action_readiness.get('executionAllowed'))}",
        "",
        "Daemon:",
        f"  reachable: {yn(daemon.get('reachable'))}",
        f"  latest tick: {daemon.get('latestTick') if daemon.get('latestTick') is not None else 'unknown'}",
        f"  session: {daemon.get('sessionPath') or 'unknown'}",
        "",
        "Sessions:",
        f"  latest session: {sessions.get('latestSessionPath') or 'unknown'}",
        f"  latest live session: {sessions.get('latestLiveSessionPath') or 'unknown'}",
        f"  highlighter session: {sessions.get('highlighterSessionPath') or 'unknown'}",
        f"  daemon matches latest live: {yn(sessions.get('matchLatestLive'))}",
        f"  daemon matches highlighter: {yn(sessions.get('matchHighlighter'))}",
        f"  stale file session context: {yn(report.get('staleFileSessionContext'))}",
        f"  daemon session fresh: {yn(report.get('daemonSessionFresh'))}",
        f"  plugin snapshot fresh: {yn(report.get('pluginSnapshotFresh'))}",
        "",
        "Overlay / Highlighter:",
        f"  debug overlay JSON: {'present' if overlay.get('debugOverlayExists') else 'missing'}",
        f"  debug overlay path: {overlay.get('debugOverlayPath') or 'unknown'}",
        f"  marker count: {overlay.get('markerCount') if overlay.get('markerCount') is not None else 'unknown'}",
        f"  overlay age: {overlay.get('debugOverlayAgeSeconds') if overlay.get('debugOverlayAgeSeconds') is not None else 'unknown'}",
        "",
        "Candidates:",
        f"  daemon in-memory candidates: {candidate.get('daemonInMemoryCandidates') if candidate.get('daemonInMemoryCandidates') is not None else 'unknown'}",
        f"  highlighter markers: {candidate.get('highlighterMarkers') if candidate.get('highlighterMarkers') is not None else 'unknown'}",
        f"  known Tree/Oak candidates: {candidate.get('knownChopCandidates') if candidate.get('knownChopCandidates') is not None else 'unknown'}",
        f"  target freshness: {freshness.get('targetCandidateFreshness') or 'unknown'}",
        f"  target freshness applicable: {yn(report.get('selectedResourceTargetFreshnessApplicable'))}",
        f"  stale: {yn(freshness.get('stale'))}",
        "",
        "Selected Target:",
        f"  target: {format_target(report.get('selectedTarget'))}",
        f"  in highlighter source: {yn(checks.get('inHighlighterSource'))}",
        f"  on screen: {yn(checks.get('onScreen'))}",
        f"  geometry available: {yn(checks.get('geometryAvailable'))}",
        f"  aim point: {yn(checks.get('hasAimPoint'))}",
        f"  UI blocked: {yn(checks.get('uiBlocked'))}",
        "",
        "Input:",
        f"  geometry available: {yn(geometry.get('inputGeometryAvailable'))}",
        f"  canvas origin: {geometry.get('canvasScreenOrigin') or 'unknown'}",
        f"  canvas size: {geometry.get('canvasSize') or 'unknown'}",
        f"  reason: {geometry.get('reason') or 'unknown'}",
        f"  client tick hot available: {yn(client_hot.get('available'))}",
        f"  client tick hot fresh: {yn(client_hot.get('fresh'))}",
        f"  client tick hot age ms: {client_hot.get('ageMillis') if client_hot.get('ageMillis') is not None else 'unknown'}",
        f"  latest PostMenuSort age ms: {client_hot.get('latestPostMenuSortAgeMillis') if client_hot.get('latestPostMenuSortAgeMillis') is not None else 'unknown'}",
        f"  last MenuOptionClicked age ms: {client_hot.get('lastMenuOptionClickedAgeMillis') if client_hot.get('lastMenuOptionClickedAgeMillis') is not None else 'unknown'}",
        f"  game state: {client_hot.get('gameState') or 'unknown'}",
        f"  logged in: {yn(client_hot.get('isLoggedIn'))}",
        f"  stale reason: {client_hot.get('staleReason') or 'none'}",
        f"  recovery: {client_hot.get('recovery') or 'none'}",
        f"  top menu: {client_hot.get('topOption') or 'unknown'} {client_hot.get('topTarget') or ''}".rstrip(),
        "",
        "Action Readiness:",
        f"  intent: {action_readiness.get('intent') or report.get('currentIntent') or 'unknown'}",
        f"  status: {action_readiness.get('status') or 'unknown'}",
        f"  execution allowed: {yn(action_readiness.get('executionAllowed'))}",
        f"  skipped checks: {', '.join(str(item) for item in (action_readiness.get('checksSkippedAsNotApplicable') or [])) or 'none'}",
        "",
        "Context Readiness:",
        f"  status: {context_readiness.get('status') or 'unknown'}",
        "",
        "Blockers:",
    ]
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    if blockers:
        for blocker in blockers:
            if isinstance(blocker, dict):
                action = f" action={blocker.get('action')}" if blocker.get("action") else ""
                lines.append(f"  FAIL: {blocker.get('code')}: {blocker.get('message')}{action}")
            else:
                lines.append(f"  FAIL: {blocker}")
    else:
        lines.append("  none")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    applicable_warnings = report.get("applicableWarnings") if isinstance(report.get("applicableWarnings"), list) else []
    non_applicable_warnings = (
        report.get("nonApplicableContextWarnings") if isinstance(report.get("nonApplicableContextWarnings"), list) else []
    )
    action_warnings = action_readiness.get("warnings") if isinstance(action_readiness.get("warnings"), list) else []
    context_warnings = context_readiness.get("warnings") if isinstance(context_readiness.get("warnings"), list) else []
    lines.extend(["", "Action warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in action_warnings) if action_warnings else lines.append("  none")
    lines.extend(["", "Applicable context warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in applicable_warnings) if applicable_warnings else lines.append("  none")
    lines.extend(["", "Non-applicable context warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in non_applicable_warnings) if non_applicable_warnings else lines.append("  none")
    lines.extend(["", "Context warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in context_warnings) if context_warnings else lines.append("  none")
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    lines.extend(
        [
            "",
            "Next action command:",
            "  python telemetry-viewer\\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --movement-profile linear_debug --execute --verify-after-action --wait-for-ready 30",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether live daemon, overlay, highlighter, target, and input geometry are ready for one action.")
    parser.add_argument("--latest-session", action="store_true", help="Retained for compatibility; readiness always compares daemon with newest live-output session.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions root.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--profile", default="woodcutting")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_readiness_report(
        daemon_url=args.daemon_url,
        timeout=args.timeout,
        sessions_dir=args.sessions_dir,
        profile=args.profile,
    )
    print(json.dumps(report, indent=2, sort_keys=False) if args.json else format_human(report), end="")
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
