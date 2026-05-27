from __future__ import annotations

from typing import Any


WORLD_MODEL_SCHEMA = "world_model_snapshot.v1"
WORLD_MODEL_QUERY_SCHEMA = "world_model_query_response.v1"
WORLD_MODEL_NEEDS = [
    "world_model_summary",
    "resource_object_census",
    "service_object_census",
    "route_object_census",
    "projection_audit",
    "view_quality_inputs",
    "pathing_frontier",
]
CENSUS_NEEDS = {
    "resource_object_census",
    "service_object_census",
    "route_object_census",
    "scene_object_census",
}
FABRIC_EXTRA_PAYLOADS = {
    "inventory",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _bool(value: Any) -> bool:
    return isinstance(value, bool) and value


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def extract_world_model_payloads(response: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    payloads = _dict(response.get("payloads"))
    extracted: dict[str, Any] = {
        key: value
        for key, value in payloads.items()
        if isinstance(key, str) and (key in WORLD_MODEL_NEEDS or key in CENSUS_NEEDS or key == "full_world_model_debug")
    }
    for key in FABRIC_EXTRA_PAYLOADS:
        value = payloads.get(key)
        if isinstance(value, dict):
            extracted[key] = value
    world_model = _dict(response.get("worldModel"))
    nested = _dict(world_model.get("payloads"))
    for key, value in nested.items():
        if isinstance(key, str) and key not in extracted:
            extracted[key] = value
    quality = response.get("worldModelQuality") or world_model.get("quality")
    if isinstance(quality, dict):
        extracted["quality"] = dict(quality)
    return extracted


def world_model_payloads_for_tick(tick: dict[str, Any] | None) -> dict[str, Any]:
    return _dict((tick or {}).get("_worldModelPayloads"))


def world_model_quality(payloads: dict[str, Any] | None) -> dict[str, Any]:
    payloads = _dict(payloads)
    quality = _dict(payloads.get("quality"))
    summary_quality = _dict(_dict(payloads.get("world_model_summary")).get("quality"))
    if summary_quality:
        quality = {**summary_quality, **quality}
    return quality


def census_objects(census: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in _list(_dict(census).get("objects")) if isinstance(item, dict)]


def object_projection_status(obj: dict[str, Any] | None) -> dict[str, Any]:
    obj = _dict(obj)
    projection = _dict(obj.get("projection") or obj.get("projectionStatus"))
    if not projection:
        return {
            "schema": "world_model_projection.v1",
            "visible": False,
            "onScreen": False,
            "geometryAvailable": False,
            "actionableByCanvas": False,
            "classification": "unavailable",
            "reason": "projection_missing",
        }
    return dict(projection)


def target_level_status(obj: dict[str, Any] | None) -> dict[str, Any]:
    obj = _dict(obj)
    return {
        "requiredSkill": obj.get("requiredSkill"),
        "requiredLevel": obj.get("requiredLevel"),
        "playerLevelKnown": obj.get("playerLevelKnown"),
        "playerLevel": obj.get("playerLevel"),
        "levelRequirementMet": obj.get("levelRequirementMet"),
        "targetTemporarilyLockedReason": obj.get("targetTemporarilyLockedReason"),
        "visibleButNotExecutable": obj.get("visibleButNotExecutable"),
        "futureEligibleWhenLevelMet": obj.get("futureEligibleWhenLevelMet"),
    }


def object_to_candidate(obj: dict[str, Any], *, source_lane: str) -> dict[str, Any]:
    projection = object_projection_status(obj)
    aim = _dict(projection.get("aimPoint"))
    canvas_x = aim.get("canvasX")
    canvas_y = aim.get("canvasY")
    resource = _bool(obj.get("resourceCandidate"))
    service = _bool(obj.get("serviceObjectCandidate"))
    route = _bool(obj.get("routeObjectCandidate"))
    class_id = "scene_object"
    if resource:
        class_id = "tree"
    elif service:
        class_id = str(obj.get("serviceObjectType") or "bank_service")
    elif route:
        class_id = str(obj.get("routeObjectKind") or "route_transition")
    candidate: dict[str, Any] = {
        "schema": "world_model_candidate.v1",
        "targetType": "sceneObject",
        "classId": class_id,
        "targetClass": class_id,
        "targetName": obj.get("name") or obj.get("objectName"),
        "name": obj.get("name") or obj.get("objectName"),
        "id": obj.get("id"),
        "rawId": obj.get("id"),
        "hash": obj.get("hash"),
        "objectKey": obj.get("objectKey"),
        "targetKey": obj.get("objectKey"),
        "actions": list(_list(obj.get("actions"))),
        "actionNames": list(_list(obj.get("actions"))),
        "menuActions": list(_list(obj.get("actions"))),
        "worldX": obj.get("worldX"),
        "worldY": obj.get("worldY"),
        "plane": obj.get("plane"),
        "sceneX": obj.get("sceneX"),
        "sceneY": obj.get("sceneY"),
        "localX": obj.get("localX"),
        "localY": obj.get("localY"),
        "distanceTiles": obj.get("distanceToPlayer"),
        "source": "world_model_cache",
        "worldModelSource": True,
        "worldModelSourceLane": source_lane,
        "_routeObjectScanSource": source_lane,
        "projectionStatus": projection,
        "onScreen": projection.get("visible") is True or projection.get("onScreen") is True,
        "geometryAvailable": projection.get("geometryAvailable") is True,
        "actionability": "needs_hover_confirmation" if projection.get("actionableByCanvas") is True else "needs_live_projection",
        "actionTargetSource": "live_route_object" if route and not resource else ("live_resource_candidate" if resource else "live_service_object"),
    }
    if canvas_x is not None and canvas_y is not None:
        candidate["aimPoint"] = {"x": canvas_x, "y": canvas_y, "source": aim.get("source") or "world_model_projection"}
        candidate["aimPointContext"] = {"canvasX": canvas_x, "canvasY": canvas_y, "source": aim.get("source") or "world_model_projection"}
    if route:
        candidate["routeObjectKind"] = obj.get("routeObjectKind")
    if service:
        candidate["serviceObjectType"] = obj.get("serviceObjectType")
    if resource:
        candidate["resourceType"] = obj.get("resourceType")
        candidate.update(target_level_status(obj))
        if obj.get("visibleButNotExecutable") is True:
            candidate["actionability"] = "blocked"
            candidate["rejectionReason"] = obj.get("targetTemporarilyLockedReason") or "insufficient_level"
    return candidate


def census_to_candidates(census: dict[str, Any] | None, *, source_lane: str) -> list[dict[str, Any]]:
    return [object_to_candidate(obj, source_lane=source_lane) for obj in census_objects(census)]


def route_service_candidates_from_payloads(payloads: dict[str, Any] | None) -> list[dict[str, Any]]:
    payloads = _dict(payloads)
    candidates: list[dict[str, Any]] = []
    candidates.extend(census_to_candidates(_dict(payloads.get("service_object_census")), source_lane="worldModelServiceObjectCensus"))
    candidates.extend(census_to_candidates(_dict(payloads.get("route_object_census")), source_lane="worldModelRouteObjectCensus"))
    return dedupe_candidates(candidates)


def resource_candidates_from_payloads(payloads: dict[str, Any] | None) -> list[dict[str, Any]]:
    return census_to_candidates(_dict(_dict(payloads).get("resource_object_census")), source_lane="worldModelResourceObjectCensus")


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in candidates:
        key = (
            candidate.get("objectKey") or candidate.get("targetKey"),
            candidate.get("id"),
            candidate.get("hash"),
            candidate.get("worldX"),
            candidate.get("worldY"),
            candidate.get("plane"),
            candidate.get("name") or candidate.get("targetName"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def census_summary(census: dict[str, Any] | None) -> dict[str, Any]:
    census = _dict(census)
    return {
        "schema": census.get("schema"),
        "count": census.get("count"),
        "returned": census.get("returned"),
        "capHit": census.get("capHit"),
        "objectCensusCapHit": census.get("objectCensusCapHit"),
        "source": census.get("source"),
    }


def status_fields(payloads: dict[str, Any] | None) -> dict[str, Any]:
    payloads = _dict(payloads)
    quality = world_model_quality(payloads)
    summary = _dict(payloads.get("world_model_summary"))
    metadata = _dict(summary.get("metadata"))
    viewport = _dict(metadata.get("viewport"))
    if viewport:
        viewport = dict(viewport)
        if metadata.get("cameraYaw") is not None:
            viewport["cameraYaw"] = metadata.get("cameraYaw")
        if metadata.get("cameraPitch") is not None:
            viewport["cameraPitch"] = metadata.get("cameraPitch")
    resource = _dict(payloads.get("resource_object_census"))
    service = _dict(payloads.get("service_object_census"))
    route = _dict(payloads.get("route_object_census"))
    projection = _dict(payloads.get("projection_audit"))
    view = _dict(payloads.get("view_quality_inputs"))
    return {
        "worldModelAvailable": quality.get("worldModelAvailable"),
        "worldModelAgeMs": quality.get("worldModelAgeMs"),
        "worldModelSourceTick": quality.get("sourceTick"),
        "worldModelClientTick": quality.get("clientTick"),
        "worldModelObjectCensusCapHit": quality.get("objectCensusCapHit"),
        "worldModelCollisionAvailable": quality.get("collisionAvailable"),
        "worldModelProjectionAuditAvailable": quality.get("projectionAuditAvailable"),
        "worldModelProjectionCapHit": quality.get("projectionCapHit"),
        "worldModelLoadedSceneOnly": quality.get("loadedSceneOnly"),
        "worldModelFullWorldLoaded": quality.get("fullWorldLoaded"),
        "worldModelResourceObjectCensus": census_summary(resource),
        "worldModelServiceObjectCensus": census_summary(service),
        "worldModelRouteObjectCensus": census_summary(route),
        "worldModelResourceCandidateCount": resource.get("count"),
        "worldModelServiceObjectCount": service.get("count"),
        "worldModelRouteObjectCount": route.get("count"),
        "worldModelCameraViewport": viewport or None,
        "worldModelProjectionAudit": projection,
        "worldModelViewQualityInputs": view,
    }


def oak_level_rejected_count(payloads: dict[str, Any] | None) -> int:
    count = 0
    for candidate in resource_candidates_from_payloads(payloads):
        name = _str(candidate.get("name") or candidate.get("targetName")).lower()
        if "oak" in name and candidate.get("visibleButNotExecutable") is True:
            count += 1
    return count
