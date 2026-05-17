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
        "serviceGroup",
        "serviceCandidatePolicyGroupRank",
        "depositFallbackEligible",
        "policyEligible",
        "ineligibleReason",
        "lastSeenTick",
        "lastSeenSourceLane",
        "ageTicks",
        "missingTicks",
        "serviceTypePriority",
        "serviceReachabilityContribution",
        "serviceDistanceContribution",
        "servicePathingContribution",
        "serviceApproachQualityContribution",
        "serviceRetentionContribution",
        "serviceQualityContribution",
        "selectedServiceTargetSource",
        "retainedFromPrevious",
        "retainedServiceAgeTicks",
        "retainedServiceMissingTicks",
        "serviceSelectionTentative",
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
        "loadedServiceSceneCount": status.get("loadedServiceSceneCount"),
        "serviceCandidateVisibility": visibility,
        "serviceCandidateCount": service_context.get("candidateCount", status.get("serviceCandidateCount")),
        "candidatesByType": service_context.get("candidateCountsByType") or {},
        "candidateCountsByServiceGroup": service_context.get("candidateCountsByServiceGroup") or {},
        "visiblePrimaryServiceTargetCount": service_context.get("visiblePrimaryServiceTargetCount", status.get("visiblePrimaryServiceTargetCount", 0)),
        "visibleDepositServiceTargetCount": service_context.get("visibleDepositServiceTargetCount", status.get("visibleDepositServiceTargetCount", 0)),
        "sourceStageCounts": service_context.get("sourceStageCounts") or status.get("sourceStageCounts") or {},
        "memoryLifecycle": service_context.get("memoryLifecycle") or status.get("memoryLifecycle") or {},
        "serviceCandidates": [compact_candidate(candidate) for candidate in candidates[:10]],
        "bestServiceCandidate": compact_candidate(service_context.get("bestServiceCandidate")),
        "nearestServiceCandidate": compact_candidate(service_context.get("nearestServiceCandidate")),
        "selectedReason": (
            service_context.get("reason")
            or (service_context.get("bestServiceCandidate") or {}).get("serviceSelectedReason")
            if isinstance(service_context.get("bestServiceCandidate"), dict)
            else service_context.get("reason")
        ),
        "serviceTargetRetained": service_context.get("serviceTargetRetained", status.get("serviceTargetRetained")),
        "retainedServiceTargetName": service_context.get("retainedServiceTargetName", status.get("retainedServiceTargetName")),
        "retainedServiceMissingTicks": service_context.get("retainedServiceMissingTicks", status.get("retainedServiceMissingTicks")),
        "serviceSwitchReason": service_context.get("serviceSwitchReason", status.get("serviceSwitchReason")),
        "serviceCandidateDroppedReason": service_context.get("serviceCandidateDroppedReason", status.get("serviceCandidateDroppedReason")),
        "retainedServiceCandidateCount": service_context.get("retainedServiceCandidateCount", status.get("retainedServiceCandidateCount", 0)),
        "retainedBestServiceCandidate": compact_candidate(service_context.get("retainedBestServiceCandidate") or status.get("retainedBestServiceCandidate")),
        "retainedServiceAgeTicks": service_context.get("retainedServiceAgeTicks", status.get("retainedServiceAgeTicks")),
        "preferredServiceTypesSeen": list(service_context.get("preferredServiceTypesSeen") or status.get("preferredServiceTypesSeen") or []),
        "preferredServiceTypesRecentlySeen": list(service_context.get("preferredServiceTypesRecentlySeen") or status.get("preferredServiceTypesRecentlySeen") or []),
        "missingPreferredReason": service_context.get("missingPreferredReason", status.get("missingPreferredReason")),
        "selectedServiceTargetSource": service_context.get("selectedServiceTargetSource", status.get("selectedServiceTargetSource")),
        "primaryServiceVisible": service_context.get("primaryServiceVisible", status.get("primaryServiceVisible", False)),
        "primaryServiceRetained": service_context.get("primaryServiceRetained", status.get("primaryServiceRetained", False)),
        "depositFallbackAllowed": service_context.get("depositFallbackAllowed", status.get("depositFallbackAllowed", True)),
        "selectedServiceGroup": service_context.get("selectedServiceGroup", status.get("selectedServiceGroup")),
        "logicError": bool(service_context.get("logicError", status.get("logicError", False))),
        "serviceCandidateSourceLanes": status.get("serviceCandidateSourceLanes") or {
            "profileCandidates": status.get("profileCandidateCount"),
            "broadCandidates": status.get("broadCandidateCount", status.get("candidateCount")),
            "loadedServiceScene": status.get("loadedServiceSceneCount"),
            "serviceCandidateInputs": status.get("serviceCandidateInputCount"),
            "retainedServiceCandidates": service_context.get("retainedServiceCandidateCount", status.get("retainedServiceCandidateCount", 0)),
        },
        "topServiceLikeRawCandidates": [compact_candidate(candidate) for candidate in preview[:10]],
        "filteredOutByProfileFiltering": bool(
            (status.get("serviceCandidateInputCount") or 0) == 0
            and (status.get("worldTargetsPrefilteredOut") or 0) > 0
        ),
        "hotTierOrCapMayHideServiceCandidates": bool(filtered_or_capped or status.get("pluginSnapshotProjectionCapped")),
        "snapshotTier": status.get("pluginSnapshotTier") or status.get("snapshotTier"),
        "pluginSnapshotTier": status.get("pluginSnapshotTier") or status.get("snapshotTier"),
        "projectionRefsRequested": status.get("pluginSnapshotMaxProjectionRefs") or status.get("projectionRefsRequested"),
        "projectionRefsEffective": status.get("pluginSnapshotProjectionRefs") or status.get("projectionRefsEffective"),
        "serviceHintsUsed": list(status.get("pluginSnapshotServiceHintsUsed") or status.get("serviceHintsUsed") or []),
        "pluginSnapshotProjectionCapped": status.get("pluginSnapshotProjectionCapped"),
        "worldTargetsPrefilteredOut": status.get("worldTargetsPrefilteredOut"),
        "warnings": list(service_context.get("warnings") or []),
        "noActionEmitted": brain.get("noActionEmitted", True),
    }
    if payload["serviceNeeded"] and not payload["serviceCandidateCount"]:
        payload["warnings"].append("service target not observed in current daemon context")
    if payload["hotTierOrCapMayHideServiceCandidates"] and not payload["serviceCandidateInputCount"]:
        payload["warnings"].append("hot snapshot tier or projection cap may hide service candidates; try expanded tier for comparison")
    if payload.get("missingPreferredReason") in {None, "preferred_service_not_observed_current_tick", "no_service_candidates_observed"}:
        if payload.get("filteredOutByProfileFiltering"):
            payload["missingPreferredReason"] = "filtered"
        elif payload.get("pluginSnapshotProjectionCapped"):
            payload["missingPreferredReason"] = "capped"
        elif payload.get("serviceNeeded") and not payload.get("preferredServiceTypesSeen") and not payload.get("preferredServiceTypesRecentlySeen"):
            payload["missingPreferredReason"] = payload.get("missingPreferredReason") or "unknown"
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
        f"Loaded service scene: {payload.get('loadedServiceSceneCount') if payload.get('loadedServiceSceneCount') is not None else 'unknown'}",
        f"Service candidate inputs: {payload.get('serviceCandidateInputCount') if payload.get('serviceCandidateInputCount') is not None else 'unknown'}",
        f"Service candidates: {payload.get('serviceCandidateCount') if payload.get('serviceCandidateCount') is not None else 'unknown'}",
        f"Service visibility: {payload.get('serviceCandidateVisibility') or 'unknown'}",
        f"Snapshot tier: {payload.get('snapshotTier') or payload.get('pluginSnapshotTier') or 'unknown'}",
        f"Candidates by type: {json.dumps(payload.get('candidatesByType') or {}, sort_keys=True)}",
        f"Visible primary targets: {'yes' if payload.get('primaryServiceVisible') else 'no'}",
        f"Retained primary targets: {'yes' if payload.get('primaryServiceRetained') else 'no'}",
        f"Visible deposit targets: {payload.get('visibleDepositServiceTargetCount') if payload.get('visibleDepositServiceTargetCount') is not None else 'unknown'}",
        f"Deposit fallback allowed: {'yes' if payload.get('depositFallbackAllowed') else 'no'}",
        f"Selected service group: {payload.get('selectedServiceGroup') or 'none'}",
        f"Selection logic error: {'yes' if payload.get('logicError') else 'no'}",
    ]
    lifecycle = payload.get("memoryLifecycle") if isinstance(payload.get("memoryLifecycle"), dict) else {}
    if lifecycle:
        lines.append(
            "Memory grace: "
            f"primary={lifecycle.get('primaryBankGraceTicks', 'unknown')} "
            f"deposit={lifecycle.get('depositGraceTicks', 'unknown')} "
            f"default={lifecycle.get('serviceMemoryGraceTicks', 'unknown')}"
        )
        lines.append(
            "Memory policy: "
            f"plane={lifecycle.get('memoryPlanePolicy', 'unknown')} "
            f"maxDistance={lifecycle.get('memoryMaxDistanceTiles') if lifecycle.get('memoryMaxDistanceTiles') is not None else 'none'} "
            f"size={lifecycle.get('memorySize', 0)}"
        )
    if payload.get("projectionRefsRequested") is not None or payload.get("projectionRefsEffective") is not None:
        lines.append(
            "Projection refs: "
            f"{payload.get('projectionRefsEffective') if payload.get('projectionRefsEffective') is not None else 'unknown'}/"
            f"{payload.get('projectionRefsRequested') if payload.get('projectionRefsRequested') is not None else 'unknown'}"
        )
    hints = payload.get("serviceHintsUsed") if isinstance(payload.get("serviceHintsUsed"), list) else []
    lines.append(f"Service hints used: {', '.join(str(item) for item in hints) if hints else 'none'}")
    lanes = payload.get("serviceCandidateSourceLanes") if isinstance(payload.get("serviceCandidateSourceLanes"), dict) else {}
    if lanes:
        lines.append(
            "Candidate lanes: "
            f"profile={lanes.get('profileCandidates', 'unknown')} "
            f"broad={lanes.get('broadCandidates', 'unknown')} "
            f"serviceInputs={lanes.get('serviceCandidateInputs', 'unknown')} "
            f"retained={lanes.get('retainedServiceCandidates', 'unknown')}"
        )
    best = payload.get("bestServiceCandidate") if isinstance(payload.get("bestServiceCandidate"), dict) else {}
    if best:
        lines.append(
            "Best service candidate: "
            f"{best.get('targetName') or best.get('name') or best.get('classId')} "
            f"at {best.get('worldX')},{best.get('worldY')},{best.get('plane')}"
        )
        if best.get("serviceScore") is not None:
            lines.append(f"  Score: {best.get('serviceScore')}")
        if best.get("serviceGroup") is not None:
            lines.append(f"  Service group: {best.get('serviceGroup')}")
        if best.get("serviceCandidatePolicyGroupRank") is not None:
            lines.append(f"  Policy group rank: {best.get('serviceCandidatePolicyGroupRank')}")
        if best.get("serviceTypePriority") is not None:
            lines.append(f"  Type priority: {best.get('serviceTypePriority')}")
        if best.get("distanceTiles") is not None:
            lines.append(f"  Distance: {best.get('distanceTiles')}")
        if best.get("serviceReachabilityContribution") is not None:
            lines.append(f"  Reachability contribution: {best.get('serviceReachabilityContribution')}")
        if best.get("servicePathingContribution") is not None:
            lines.append(f"  Pathing contribution: {best.get('servicePathingContribution')}")
        if best.get("serviceApproachQualityContribution") is not None:
            lines.append(f"  Approach quality contribution: {best.get('serviceApproachQualityContribution')}")
        if best.get("serviceRetentionContribution") is not None:
            lines.append(f"  Retention contribution: {best.get('serviceRetentionContribution')}")
        if best.get("selectedServiceTargetSource") or payload.get("selectedServiceTargetSource"):
            lines.append(f"  Selection source: {best.get('selectedServiceTargetSource') or payload.get('selectedServiceTargetSource')}")
        if best.get("serviceSelectionTentative") is not None:
            lines.append(f"  Tentative: {'yes' if best.get('serviceSelectionTentative') else 'no'}")
        selected_reason = payload.get("selectedReason") or best.get("serviceSelectedReason") or best.get("serviceRankReason")
        if selected_reason:
            lines.append(f"  Selected reason: {selected_reason}")
    else:
        lines.append("Best service candidate: none")
    lines.append(f"Service target retained: {'yes' if payload.get('serviceTargetRetained') else 'no'}")
    lines.append(f"Retained service candidates: {payload.get('retainedServiceCandidateCount') or 0}")
    if payload.get("retainedServiceTargetName"):
        lines.append(f"Retained target: {payload.get('retainedServiceTargetName')}")
    retained_best = payload.get("retainedBestServiceCandidate") if isinstance(payload.get("retainedBestServiceCandidate"), dict) else {}
    if retained_best:
        lines.append(
            "Retained best candidate: "
            f"{retained_best.get('targetName') or retained_best.get('name') or retained_best.get('classId')} "
            f"at {retained_best.get('worldX')},{retained_best.get('worldY')},{retained_best.get('plane')}"
        )
    if payload.get("retainedServiceMissingTicks") is not None:
        lines.append(f"Retained missing ticks: {payload.get('retainedServiceMissingTicks')}")
    if payload.get("retainedServiceAgeTicks") is not None:
        lines.append(f"Retained age ticks: {payload.get('retainedServiceAgeTicks')}")
    seen = payload.get("preferredServiceTypesSeen") if isinstance(payload.get("preferredServiceTypesSeen"), list) else []
    recent = payload.get("preferredServiceTypesRecentlySeen") if isinstance(payload.get("preferredServiceTypesRecentlySeen"), list) else []
    lines.append(f"Preferred service types seen this tick: {', '.join(str(item) for item in seen) if seen else 'none'}")
    lines.append(f"Preferred service types recently seen: {', '.join(str(item) for item in recent) if recent else 'none'}")
    if payload.get("missingPreferredReason"):
        lines.append(f"Missing preferred reason: {payload.get('missingPreferredReason')}")
    if payload.get("serviceSwitchReason"):
        lines.append(f"Switch reason: {payload.get('serviceSwitchReason')}")
    if payload.get("serviceCandidateDroppedReason"):
        lines.append(f"Candidate dropped reason: {payload.get('serviceCandidateDroppedReason')}")
    source_counts = payload.get("sourceStageCounts") if isinstance(payload.get("sourceStageCounts"), dict) else {}
    lines.extend(["", "Source stages:"])
    if source_counts:
        for key in ("bank_booth", "banker", "bank_chest", "deposit_box", "bank_table"):
            stage = source_counts.get(key)
            if not isinstance(stage, dict):
                continue
            lines.append(
                "  "
                f"{key}: raw={stage.get('rawProjection') if stage.get('rawProjection') is not None else 'unknown'} "
                f"profile={stage.get('profileCandidates', 0)} "
                f"broad={stage.get('broadCandidates', 0)} "
                f"loaded={stage.get('loadedServiceScene', 0)} "
                f"inputs={stage.get('serviceCandidateInputs', 0)} "
                f"service={stage.get('serviceCandidates', 0)} "
                f"memory={stage.get('retainedMemory', 0)} "
                f"lastSeen={stage.get('lastSeenTick') if stage.get('lastSeenTick') is not None else 'none'} "
                f"age={stage.get('ageTicks') if stage.get('ageTicks') is not None else 'unknown'} "
                f"missing={stage.get('missingReason') or 'none'}"
            )
    else:
        lines.append("  none")
    retained_memory = lifecycle.get("retainedCandidates") if isinstance(lifecycle.get("retainedCandidates"), list) else []
    evictions = lifecycle.get("memoryEvictionReasons") if isinstance(lifecycle.get("memoryEvictionReasons"), list) else []
    lines.extend(["", "Retained memory:"])
    if retained_memory:
        for candidate in retained_memory[:10]:
            lines.append(
                "  "
                f"{candidate.get('targetName') or candidate.get('candidateKey') or 'candidate'} "
                f"group={candidate.get('serviceGroup') or 'unknown'} "
                f"tile={candidate.get('worldX')},{candidate.get('worldY')},{candidate.get('plane')} "
                f"age={candidate.get('ageTicks') if candidate.get('ageTicks') is not None else 'unknown'} "
                f"missing={candidate.get('missingTicks') if candidate.get('missingTicks') is not None else 'unknown'} "
                f"lane={candidate.get('lastSeenSourceLane') or 'unknown'}"
            )
    else:
        lines.append("  none")
    if evictions:
        lines.append("Memory evictions:")
        for eviction in evictions[:10]:
            lines.append(
                "  "
                f"{eviction.get('targetName') or eviction.get('candidateKey') or 'candidate'} "
                f"reason={eviction.get('reason')} age={eviction.get('ageTicks') if eviction.get('ageTicks') is not None else 'unknown'}"
            )
    if payload.get("filteredOutByProfileFiltering"):
        lines.append("Profile filtering: service-like candidates may have been filtered before service analysis")
    if payload.get("hotTierOrCapMayHideServiceCandidates"):
        lines.append("Snapshot tier/cap: hot tier or projection cap may hide service candidates")
    service_candidates = payload.get("serviceCandidates") if isinstance(payload.get("serviceCandidates"), list) else []
    lines.extend(["", "Service candidates:"])
    if service_candidates:
        for candidate in service_candidates[:10]:
            lines.append(
                "  "
                f"{candidate.get('targetName') or candidate.get('name') or candidate.get('classId')} "
                f"{candidate.get('classId') or ''} "
                f"{candidate.get('worldX')},{candidate.get('worldY')},{candidate.get('plane')} "
                f"group={candidate.get('serviceGroup') or 'unknown'} "
                f"eligible={'yes' if candidate.get('policyEligible', True) else 'no'}"
                + (f" reason={candidate.get('ineligibleReason')}" if candidate.get("ineligibleReason") else "")
            )
    else:
        lines.append("  none")
    raw = payload.get("topServiceLikeRawCandidates") if isinstance(payload.get("topServiceLikeRawCandidates"), list) else []
    lines.extend(["", "Top service-like raw candidates:"])
    if raw:
        for candidate in raw[:10]:
            lines.append(
                "  "
                f"{candidate.get('targetName') or candidate.get('name') or candidate.get('classId')} "
                f"{candidate.get('classId') or ''} "
                f"{candidate.get('worldX')},{candidate.get('worldY')},{candidate.get('plane')} "
                f"group={candidate.get('serviceGroup') or 'unknown'}"
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
