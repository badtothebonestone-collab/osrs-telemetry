from __future__ import annotations

import argparse
import json
from pathlib import Path

from candidate_core import build_report


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
        f"class={target.get('classId') or 'unknown'} d={target.get('distanceTiles') if target.get('distanceTiles') is not None else 'unknown'} "
        f"world={world} score={target.get('score') if target.get('score') is not None else 'unknown'}"
    )


def format_human(report: dict) -> str:
    sessions = report.get("sessions") if isinstance(report.get("sessions"), dict) else {}
    freshness = report.get("freshness") if isinstance(report.get("freshness"), dict) else {}
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    checks = report.get("selectedTargetChecks") if isinstance(report.get("selectedTargetChecks"), dict) else {}
    source = report.get("sourceHealth") if isinstance(report.get("sourceHealth"), dict) else {}
    lines = [
        f"WOODCUTTING CANDIDATES - {report.get('status') or 'UNKNOWN'}",
        "",
        "Sources:",
        f"  latest session: {sessions.get('latestSessionPath') or 'unknown'}",
        f"  daemon action session: {sessions.get('daemonActionSessionPath') or 'unknown'}",
        f"  highlighter session: {sessions.get('highlighterSessionPath') or 'unknown'}",
        f"  source mismatch: {yn(sessions.get('sourceMismatch'))}",
        "",
        "Freshness:",
        f"  latest telemetry tick: {freshness.get('latestTick') if freshness.get('latestTick') is not None else 'unknown'}",
        f"  selected target tick: {freshness.get('selectedTargetTick') if freshness.get('selectedTargetTick') is not None else 'unknown'}",
        f"  target freshness: {freshness.get('targetCandidateFreshness') or 'unknown'}",
        f"  stale: {yn(freshness.get('stale'))}",
        f"  latest candidate file age: {freshness.get('latestCandidateFileAgeSeconds') if freshness.get('latestCandidateFileAgeSeconds') is not None else 'unknown'}",
        f"  highlighter overlay age: {freshness.get('highlighterOverlayAgeSeconds') if freshness.get('highlighterOverlayAgeSeconds') is not None else 'unknown'}",
        "",
        "Counts:",
        f"  raw scene objects / broad candidates: {counts.get('rawSceneObjects') if counts.get('rawSceneObjects') is not None else 'unknown'}",
        f"  daemon in-memory candidates: {counts.get('daemonInMemoryCandidates') if counts.get('daemonInMemoryCandidates') is not None else 'unknown'}",
        f"  latest file candidates: {counts.get('latestFileCandidates')}",
        f"  highlighter file candidates: {counts.get('highlighterFileCandidates')}",
        f"  highlighter markers: {counts.get('highlighterMarkers')}",
        f"  woodcutting-profile candidates: {counts.get('woodcuttingProfileCandidates') if counts.get('woodcuttingProfileCandidates') is not None else 'unknown'}",
        f"  tree-class candidates: {counts.get('treeClassCandidates')}",
        f"  known Tree/Oak chop candidates: {counts.get('knownChopCandidates')}",
        "",
        "Selected:",
        f"  target: {format_target(report.get('selectedTarget'))}",
        f"  on screen: {yn(checks.get('onScreen'))}",
        f"  geometry available: {yn(checks.get('geometryAvailable'))}",
        f"  aim point: {yn(checks.get('hasAimPoint'))}",
        f"  UI blocked: {yn(checks.get('uiBlocked'))}",
        f"  stale: {yn(checks.get('stale'))}",
        f"  visible in highlighter source: {yn(checks.get('inHighlighterSource'))}",
        "",
        "Source limits:",
        f"  source cap hit: {yn(source.get('sourceCapHit'))}",
        f"  budget exceeded: {yn(source.get('budgetExceeded'))}",
        f"  scene knowledge complete: {yn(source.get('sourceSceneKnowledgeComplete'))}",
        f"  overlay state written: {yn(source.get('overlayStateWritten'))}",
        f"  live candidate files expected: {yn(source.get('candidateFilesExpected'))}",
        "",
        "Top candidates / highlighter markers:",
    ]
    top = report.get("topCandidates") if isinstance(report.get("topCandidates"), list) else []
    if top:
        for target in top:
            lines.append(f"  {target.get('rank') or '-'}: {format_target(target)}")
    else:
        lines.append("  none")
    rejected = counts.get("rejectedByReason") if isinstance(counts.get("rejectedByReason"), dict) else {}
    lines.extend(["", "Rejected/demoted by reason:"])
    if rejected:
        lines.extend(f"  {reason}: {count}" for reason, count in rejected.items())
    else:
        lines.append("  none observed in available file source")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    lines.extend(["", "Warnings:"])
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    lines.extend(["", "Failures:"])
    lines.extend(f"  FAIL: {failure}" for failure in failures) if failures else lines.append("  none")
    lines.extend(
        [
            "",
            "Inspector/highlighter command:",
            "  python telemetry-viewer\\target_geometry_inspector.py --from-daemon --daemon-url http://127.0.0.1:8890 --live",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose live woodcutting candidate and highlighter source health.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest telemetry session for file-source comparison.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions root.")
    parser.add_argument("--profile", default="woodcutting")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--show-rejections", action="store_true", help="Retained for compatibility; rejection counts are always shown when available.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        session=Path(args.session).expanduser() if args.session else None,
        latest_session=args.latest_session,
        sessions_dir=args.sessions_dir,
        profile=args.profile,
        top=args.top,
        daemon_url=args.daemon_url,
        timeout=args.timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=False) if args.json else format_human(report), end="")
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
