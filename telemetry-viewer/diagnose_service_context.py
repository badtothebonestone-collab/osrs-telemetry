from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


SCHEMA = "service_context_diagnostic.v1"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def compact_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {}
    fields = (
        "objectKey",
        "targetKey",
        "targetType",
        "classId",
        "serviceCandidateType",
        "targetName",
        "name",
        "id",
        "worldX",
        "worldY",
        "plane",
        "sceneX",
        "sceneY",
        "sizeX",
        "sizeY",
        "footprintWidth",
        "footprintHeight",
        "objectSizeX",
        "objectSizeY",
        "width",
        "height",
        "localX",
        "localY",
        "distanceTiles",
        "directReachability",
        "serviceScore",
        "serviceTypePriority",
        "serviceReachabilityContribution",
        "serviceDistanceContribution",
        "servicePathingContribution",
        "serviceQualityContribution",
        "serviceRankReason",
        "serviceSelectedReason",
        "interactionRadiusTiles",
        "approachRadiusTiles",
        "navigation",
        "aimPoint",
        "bounds",
        "clickbox",
        "clickableHull",
        "clickboxPolygon",
        "canvasTilePolygon",
    )
    return {key: candidate.get(key) for key in fields if candidate.get(key) is not None}


def build_from_daemon(status: dict[str, Any]) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    service_context = brain.get("serviceContext") if isinstance(brain.get("serviceContext"), dict) else {}
    candidates = service_context.get("serviceCandidates") if isinstance(service_context.get("serviceCandidates"), list) else []
    preview = status.get("serviceCandidateInputsPreview") if isinstance(status.get("serviceCandidateInputsPreview"), list) else []
    visibility = status.get("serviceCandidateVisibility")
    filtered_or_capped = visibility == "possibly_capped_or_filtered"
    payload = {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "daemonReachable": True,
        "activeProfile": status.get("activeProfile") or status.get("profile"),
        "taskPolicy": status.get("brainTaskPolicy"),
        "serviceNeeded": service_context.get("serviceNeeded", status.get("serviceNeeded")),
        "serviceTypeNeeded": service_context.get("serviceTypeNeeded", status.get("serviceTypeNeeded")),
        "profileCandidateCount": status.get("profileCandidateCount"),
        "broadCandidateCount": status.get("broadCandidateCount", status.get("candidateCount")),
        "serviceCandidateInputCount": status.get("serviceCandidateInputCount"),
        "serviceCandidateVisibility": visibility,
        "serviceCandidateCount": service_context.get("candidateCount", status.get("serviceCandidateCount")),
        "candidatesByType": service_context.get("candidateCountsByType") or {},
        "bestServiceCandidate": compact_candidate(service_context.get("bestServiceCandidate")),
        "nearestServiceCandidate": compact_candidate(service_context.get("nearestServiceCandidate")),
        "selectedReason": (
            service_context.get("reason")
            or (service_context.get("bestServiceCandidate") or {}).get("serviceSelectedReason")
            if isinstance(service_context.get("bestServiceCandidate"), dict)
            else service_context.get("reason")
        ),
        "topServiceLikeRawCandidates": [compact_candidate(candidate) for candidate in preview[:10]],
        "filteredOutByProfileFiltering": bool(
            (status.get("serviceCandidateInputCount") or 0) == 0
            and (status.get("worldTargetsPrefilteredOut") or 0) > 0
        ),
        "hotTierOrCapMayHideServiceCandidates": bool(filtered_or_capped or status.get("pluginSnapshotProjectionCapped")),
        "pluginSnapshotTier": status.get("pluginSnapshotTier"),
        "pluginSnapshotProjectionCapped": status.get("pluginSnapshotProjectionCapped"),
        "worldTargetsPrefilteredOut": status.get("worldTargetsPrefilteredOut"),
        "warnings": list(service_context.get("warnings") or []),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }
    if payload["serviceNeeded"] and not payload["serviceCandidateCount"]:
        payload["warnings"].append("service target not observed in current daemon context")
    if payload["hotTierOrCapMayHideServiceCandidates"] and not payload["serviceCandidateInputCount"]:
        payload["warnings"].append("hot snapshot tier or projection cap may hide service candidates; try expanded tier for comparison")
    return payload


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        "SERVICE CONTEXT DIAGNOSTIC",
        "",
        f"Source: {payload.get('source')}",
        f"Daemon reachable: {'yes' if payload.get('daemonReachable') else 'no'}",
        f"Active profile: {payload.get('activeProfile') or 'unknown'}",
        f"Task policy: {payload.get('taskPolicy') or 'unknown'}",
        f"Service needed: {'yes' if payload.get('serviceNeeded') else 'no'}",
        f"Service type: {payload.get('serviceTypeNeeded') or 'none'}",
        f"Profile candidates: {payload.get('profileCandidateCount') if payload.get('profileCandidateCount') is not None else 'unknown'}",
        f"Broad candidates: {payload.get('broadCandidateCount') if payload.get('broadCandidateCount') is not None else 'unknown'}",
        f"Service candidate inputs: {payload.get('serviceCandidateInputCount') if payload.get('serviceCandidateInputCount') is not None else 'unknown'}",
        f"Service candidates: {payload.get('serviceCandidateCount') if payload.get('serviceCandidateCount') is not None else 'unknown'}",
        f"Service visibility: {payload.get('serviceCandidateVisibility') or 'unknown'}",
        f"Candidates by type: {json.dumps(payload.get('candidatesByType') or {}, sort_keys=True)}",
    ]
    best = payload.get("bestServiceCandidate") if isinstance(payload.get("bestServiceCandidate"), dict) else {}
    if best:
        lines.append(
            "Best service candidate: "
            f"{best.get('targetName') or best.get('name') or best.get('classId')} "
            f"at {best.get('worldX')},{best.get('worldY')},{best.get('plane')}"
        )
        if best.get("serviceScore") is not None:
            lines.append(f"  Score: {best.get('serviceScore')}")
        if best.get("serviceTypePriority") is not None:
            lines.append(f"  Type priority: {best.get('serviceTypePriority')}")
        if best.get("distanceTiles") is not None:
            lines.append(f"  Distance: {best.get('distanceTiles')}")
        if best.get("serviceReachabilityContribution") is not None:
            lines.append(f"  Reachability contribution: {best.get('serviceReachabilityContribution')}")
        if best.get("servicePathingContribution") is not None:
            lines.append(f"  Pathing contribution: {best.get('servicePathingContribution')}")
        selected_reason = payload.get("selectedReason") or best.get("serviceSelectedReason") or best.get("serviceRankReason")
        if selected_reason:
            lines.append(f"  Selected reason: {selected_reason}")
    else:
        lines.append("Best service candidate: none")
    if payload.get("filteredOutByProfileFiltering"):
        lines.append("Profile filtering: service-like candidates may have been filtered before service analysis")
    if payload.get("hotTierOrCapMayHideServiceCandidates"):
        lines.append("Snapshot tier/cap: hot tier or projection cap may hide service candidates")
    raw = payload.get("topServiceLikeRawCandidates") if isinstance(payload.get("topServiceLikeRawCandidates"), list) else []
    lines.extend(["", "Top service-like raw candidates:"])
    if raw:
        for candidate in raw[:10]:
            lines.append(
                "  "
                f"{candidate.get('targetName') or candidate.get('name') or candidate.get('classId')} "
                f"{candidate.get('classId') or ''} "
                f"{candidate.get('worldX')},{candidate.get('worldY')},{candidate.get('plane')}"
            )
    else:
        lines.append("  none")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(["", "Warnings:"])
    if warnings:
        for warning in warnings:
            lines.append(f"  {warning}")
    else:
        lines.append("  none")
    lines.extend(["", f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only service context diagnostic. Prints to stdout only.")
    parser.add_argument("--from-daemon", action="store_true", help="Read current live daemon memory/status.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.from_daemon:
        payload = {
            "schema": SCHEMA,
            "source": "not_requested",
            "daemonReachable": False,
            "warnings": ["pass --from-daemon to read live daemon service context"],
            "noActionEmitted": True,
        }
    else:
        try:
            payload = build_from_daemon(fetch_json(daemon_status_url(args.daemon_url), timeout=args.timeout))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            payload = {
                "schema": SCHEMA,
                "source": "daemon-memory",
                "daemonReachable": False,
                "warnings": [f"daemon status unavailable: {type(error).__name__}: {error}"],
                "noActionEmitted": True,
            }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    return 0 if payload.get("daemonReachable") or not args.from_daemon else 1


if __name__ == "__main__":
    raise SystemExit(main())
