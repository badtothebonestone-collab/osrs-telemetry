from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import hypot

from .definition import FixedRoute, RoutePointClassification, TaskSiteDefinition
from .model import WorldPoint
from .movement import route_progress


_DISTANCE_BINS: tuple[tuple[str, float, float | None], ...] = (
    ("1-4", 0.0, 4.0),
    ("5-11", 4.0, 11.0),
    ("12-19", 11.0, 19.0),
    ("20-30", 19.0, 30.0),
    (">30", 30.0, None),
)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = int(value)
    return numeric if float(value) == float(numeric) else None


def _world_point(value: object, *, fallback_plane: object = None) -> WorldPoint | None:
    row = _mapping(value)
    if not row:
        return None
    nested = _mapping(row.get("world"))
    if nested:
        row = nested
    x = _integer(row.get("x", row.get("worldX")))
    y = _integer(row.get("y", row.get("worldY")))
    plane = _integer(row.get("plane"))
    if plane is None:
        plane = _integer(fallback_plane)
    if x is None or y is None or plane is None or not 0 <= plane <= 3:
        return None
    return WorldPoint(x, y, plane)


def _manual_point(row: Mapping[str, object]) -> WorldPoint | None:
    for key in (
        "manualIntentTarget",
        "manual_intent_target",
        "chosenTargetWorld",
        "chosen_target_world",
        "selectedSceneTile",
        "selected_scene_tile",
    ):
        point = _world_point(row.get(key), fallback_plane=row.get("plane"))
        if point is not None:
            return point
    return None


def _event_order(row: Mapping[str, object], fallback: int) -> tuple[float, int]:
    for key in (
        "clickMonotonicMillis",
        "eventTimeMillis",
        "event_time_millis",
        "monotonicMillis",
        "monotonic_millis",
    ):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), fallback
    for key in ("clickEventSequence", "eventSequence", "event_sequence"):
        value = _integer(row.get(key))
        if value is not None:
            return float(value), fallback
    return float(fallback), fallback


def _ordered_manual_points(
    rows: tuple[Mapping[str, object], ...],
) -> tuple[tuple[Mapping[str, object], WorldPoint], ...]:
    accepted = tuple(
        (index, row, point)
        for index, row in enumerate(rows)
        if (point := _manual_point(row)) is not None
    )
    return tuple(
        (row, point)
        for index, row, point in sorted(
            accepted, key=lambda item: _event_order(item[1], item[0])
        )
    )


def _route_metrics(route: FixedRoute, points: tuple[WorldPoint, ...]) -> dict[str, object]:
    samples: list[tuple[WorldPoint, object]] = []
    unsupported = 0
    for point in points:
        try:
            samples.append((point, route_progress(route, point)))
        except ValueError:
            unsupported += 1
    deltas = tuple(
        float(current.distance_along_route - previous.distance_along_route)
        for (_previous_point, previous), (_current_point, current) in zip(
            samples, samples[1:]
        )
    )
    deviations = tuple(float(sample.lateral_deviation) for _point, sample in samples)
    total = (
        float(samples[-1][1].distance_along_route - samples[0][1].distance_along_route)
        if len(samples) >= 2
        else 0.0
    )
    return {
        "routeId": route.route_id,
        "supportedPointCount": len(samples),
        "unsupportedPointCount": unsupported,
        "forwardProgressTiles": round(total, 3),
        "forwardStepCount": sum(delta > 0.75 for delta in deltas),
        "backtrackingEventCount": sum(delta < -0.75 for delta in deltas),
        "backtrackingTiles": round(sum(-delta for delta in deltas if delta < 0.0), 3),
        "averageCorridorDeviationTiles": (
            round(sum(deviations) / len(deviations), 3) if deviations else None
        ),
        "maximumCorridorDeviationTiles": round(max(deviations), 3) if deviations else None,
        "progressSamples": [
            {
                "x": point.x,
                "y": point.y,
                "plane": point.plane,
                "distanceAlongRoute": round(float(progress.distance_along_route), 3),
                "lateralDeviation": round(float(progress.lateral_deviation), 3),
            }
            for point, progress in samples
        ],
    }


def _direction(
    to_bank: Mapping[str, object], to_resource: Mapping[str, object]
) -> tuple[str, str | None, str]:
    bank_count = int(to_bank.get("supportedPointCount") or 0)
    resource_count = int(to_resource.get("supportedPointCount") or 0)
    if max(bank_count, resource_count) < 2:
        return "not_compared", None, "at least two plane-supported manual Walk targets are required"
    bank_progress = float(to_bank.get("forwardProgressTiles") or 0.0)
    resource_progress = float(to_resource.get("forwardProgressTiles") or 0.0)
    if bank_progress >= 2.0 and resource_progress <= 0.0:
        return "compared", "to_bank", "manual targets progress forward only on the bank route"
    if resource_progress >= 2.0 and bank_progress <= 0.0:
        return "compared", "to_resource", "manual targets progress forward only on the resource route"
    return (
        "ambiguous",
        None,
        "recording evidence does not uniquely determine route direction; both comparisons are retained",
    )


def _distance_summary(
    ordered: tuple[tuple[Mapping[str, object], WorldPoint], ...]
) -> dict[str, object]:
    def raw_player_sample_distance(
        row: Mapping[str, object], target: WorldPoint
    ) -> float | None:
        value = row.get("requestedTileDistance")
        recorded_distance = (
            float(value)
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) >= 0.0
            else None
        )
        player = _world_point(
            row.get("playerWorldAtClick", row.get("player_world_at_click"))
        )
        if player is not None:
            if player.plane != target.plane:
                return None
            if recorded_distance is not None:
                return recorded_distance
            return hypot(target.x - player.x, target.y - player.y)
        if recorded_distance is not None:
            # Legacy route evidence did not persist playerWorldAtClick. Its
            # requested distance was derived from that sample by the recorder.
            return recorded_distance
        return None

    def accepted_player_sample_distance(
        row: Mapping[str, object], target: WorldPoint
    ) -> float | None:
        status = row.get("requestedTileDistanceStatus")
        if isinstance(status, str) and status not in {
            "same_source_tick_player_sample",
            "near_source_tick_player_sample_estimate",
        }:
            # Versioned manual-route semantics deliberately refuse old or
            # unknown player samples. Do not silently reclaim those values by
            # recomputing from playerWorldAtClick in the review basis.
            return None
        return raw_player_sample_distance(row, target)

    player_sample_distances = tuple(
        distance
        for row, point in ordered
        if (distance := raw_player_sample_distance(row, point)) is not None
    )
    points = tuple(point for _row, point in ordered)
    target_to_target = tuple(
        hypot(current.x - previous.x, current.y - previous.y)
        for previous, current in zip(points, points[1:])
        if previous.plane == current.plane
    )
    reference: list[float] = []
    segment_start_measurements = 0
    previous_point: WorldPoint | None = None
    for row, point in ordered:
        if previous_point is None or previous_point.plane != point.plane:
            distance = accepted_player_sample_distance(row, point)
            if distance is not None:
                reference.append(distance)
                segment_start_measurements += 1
        else:
            reference.append(
                hypot(point.x - previous_point.x, point.y - previous_point.y)
            )
        previous_point = point
    distances = tuple(reference) or target_to_target
    histogram = {label: 0 for label, _low, _high in _DISTANCE_BINS}
    for distance in distances:
        for label, low, high in _DISTANCE_BINS:
            if distance > low and (high is None or distance <= high):
                histogram[label] += 1
                break
    return {
        "basis": (
            "first_player_sample_per_plane_segment_then_consecutive_manual_targets"
            if segment_start_measurements > 1
            else "first_player_sample_then_consecutive_manual_targets"
            if segment_start_measurements == 1
            else "consecutive_manual_target_to_target_same_plane"
        ),
        "qualification": (
            "Review estimate only. The first target after each plane transition uses "
            "an accepted same- or near-source-tick player sample; later same-plane "
            "targets use target-to-target "
            "spacing because player samples can lag."
            if segment_start_measurements > 1
            else "Review estimate only. The first plane segment uses an accepted "
            "same- or near-source-tick player sample; later player samples can lag, "
            "so target-to-target spacing is used after the first click."
            if segment_start_measurements == 1
            else "Target-to-target spacing is not proof that the player reached each target."
        ),
        "sampleCount": len(distances),
        "distancesTiles": [round(value, 3) for value in distances],
        "playerSampleToTargetDistancesTiles": [
            round(value, 3) for value in player_sample_distances
        ],
        "playerSampleDistanceUse": (
            "plane_segment_start_basis_else_diagnostic_only"
        ),
        "acceptedVersionedPlayerSampleStatuses": [
            "same_source_tick_player_sample",
            "near_source_tick_player_sample_estimate",
        ],
        "consecutiveTargetDistancesTiles": [
            round(value, 3) for value in target_to_target
        ],
        "histogram": histogram,
    }


def _definition_points(route: FixedRoute, plane: int) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    if route.start_anchor.plane == plane:
        points.append(
            {
                "x": route.start_anchor.x,
                "y": route.start_anchor.y,
                "plane": plane,
                "classification": "start_anchor",
                "stepId": None,
            }
        )
    points.extend(
        {
            "x": step.location.x,
            "y": step.location.y,
            "plane": plane,
            "classification": step.classification.value,
            "stepId": step.step_id,
        }
        for step in route.steps
        if step.location.plane == plane
    )
    return points


def _plane_views(
    manual_points: tuple[WorldPoint, ...],
    observed_points: tuple[WorldPoint, ...],
    routes: tuple[FixedRoute, ...],
) -> list[dict[str, object]]:
    planes = sorted({point.plane for point in (*manual_points, *observed_points)})
    views: list[dict[str, object]] = []
    for plane in planes:
        manual = [
            {"x": point.x, "y": point.y, "plane": point.plane}
            for point in manual_points
            if point.plane == plane
        ]
        observed = [
            {"x": point.x, "y": point.y, "plane": point.plane}
            for point in observed_points
            if point.plane == plane
        ]
        definitions = [
            {
                "routeId": route.route_id,
                "points": _definition_points(route, plane),
            }
            for route in routes
        ]
        mandatory = [
            point
            for definition in definitions
            for point in definition["points"]
            if point["classification"]
            in {
                RoutePointClassification.MANDATORY_TRANSITION.value,
                RoutePointClassification.MANDATORY_TURN.value,
                RoutePointClassification.ARRIVAL_POINT.value,
            }
        ]
        views.append(
            {
                "plane": plane,
                "manualTargets": manual,
                "observedPlayerPath": observed,
                "definitionRoutes": definitions,
                "mandatoryDefinitionPoints": mandatory,
            }
        )
    return views


def compare_manual_route(
    manual_route_targets: object,
    observed_route_points: object,
    definition: TaskSiteDefinition,
) -> dict[str, object]:
    """Build an ephemeral, review-only comparison without changing the artifact."""

    manual_rows = _rows(manual_route_targets)
    ordered = _ordered_manual_points(manual_rows)
    manual_points = tuple(point for _row, point in ordered)
    observed_points = tuple(
        point
        for row in _rows(observed_route_points)
        if (point := _world_point(row)) is not None
    )
    bank_metrics = _route_metrics(definition.route_to_bank, manual_points)
    resource_metrics = _route_metrics(definition.route_to_resource, manual_points)
    status, direction, reason = _direction(bank_metrics, resource_metrics)
    selected_route = (
        definition.route_to_bank
        if direction == "to_bank"
        else definition.route_to_resource
        if direction == "to_resource"
        else None
    )
    routes = (
        (selected_route,)
        if selected_route is not None
        else (definition.route_to_bank, definition.route_to_resource)
    )
    selected_metrics = (
        bank_metrics
        if direction == "to_bank"
        else resource_metrics
        if direction == "to_resource"
        else None
    )
    return {
        "status": status,
        "direction": direction,
        "reason": reason,
        "definitionId": definition.definition_id,
        "definitionVersion": definition.version,
        "reviewOnly": True,
        "automaticConfigurationAllowed": False,
        "manualTargetCount": len(manual_points),
        "observedPlayerPointCount": len(observed_points),
        "selectedRouteMetrics": selected_metrics,
        "candidateRouteMetrics": {
            "toBank": bank_metrics,
            "toResource": resource_metrics,
        },
        "targetDistanceSummary": _distance_summary(ordered),
        "planeViews": _plane_views(manual_points, observed_points, routes),
        "interpretation": (
            "Manual targets are intended click evidence; observed player points are sampled outcomes. "
            "Neither layer is copied into the task definition automatically."
        ),
    }


def build_demonstration_review(
    inspection: object, definition: TaskSiteDefinition
) -> dict[str, object]:
    converter = getattr(inspection, "to_dict", None)
    payload = dict(converter()) if callable(converter) else dict(_mapping(inspection))
    recorded_manual_targets = getattr(inspection, "manual_route_targets", None)
    if recorded_manual_targets is None:
        recorded_manual_targets = payload.get("manualRouteTargets") or payload.get(
            "manual_route_targets"
        )
    if recorded_manual_targets is not None:
        payload["manualRouteTargets"] = list(recorded_manual_targets)
    manual_review_targets = getattr(
        inspection, "manual_route_review_targets", None
    )
    if manual_review_targets is None:
        manual_review_targets = payload.get("manualRouteReviewTargets") or payload.get(
            "manual_route_review_targets"
        )
    if manual_review_targets is not None:
        payload["manualRouteReviewTargets"] = list(manual_review_targets)
    comparison_targets = (
        manual_review_targets
        if manual_review_targets is not None
        else recorded_manual_targets
    )
    observed = getattr(inspection, "route_points", None)
    if observed is None:
        observed = payload.get("routePoints") or payload.get("route_points")
    camera_review = getattr(inspection, "camera_review_episodes", None)
    if camera_review is not None:
        payload["cameraReviewEpisodes"] = list(camera_review)
    timing_review = getattr(inspection, "timing_review_profiles", None)
    if timing_review is None:
        timing_review = payload.get("timingReviewProfiles") or payload.get(
            "timing_review_profiles"
        )
    if timing_review is not None:
        payload["timingReviewProfiles"] = list(timing_review)
    payload["routeComparison"] = compare_manual_route(
        comparison_targets or (), observed or (), definition
    )
    return payload
