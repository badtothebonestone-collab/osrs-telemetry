"""Read-only R2 compact context request and response boundary."""

from __future__ import annotations

from typing import Any

import live_context_query as query
from state_baseline import compact_activity


REQUEST_SCHEMA = "context_request.v1"
RESPONSE_SCHEMA = "context_response.v1"

COMPACT_CONTEXT_ALLOWED_NEEDS = {"state", "player", "inventory", "activity", "liveness", "source"}
COMPACT_CONTEXT_ALLOWED_REQUEST_FIELDS = {"schema", "needs", "responseMode", "maxAgeMs"}
COMPACT_CONTEXT_DEFAULT_NEEDS = ["state", "player", "inventory", "activity", "liveness", "source"]


def _compact_source_metadata(state_baseline: dict) -> dict:
    files = []
    for item in state_baseline.get("sourceFiles") or []:
        if isinstance(item, dict):
            files.append(
                {
                    "name": item.get("name"),
                    "exists": item.get("exists"),
                    "modifiedUtc": item.get("modifiedUtc"),
                    "sizeBytes": item.get("sizeBytes"),
                }
            )
    return {
        "stateSchema": state_baseline.get("schema"),
        "stateStatus": state_baseline.get("status"),
        "sessionPath": state_baseline.get("sessionPath"),
        "files": files,
    }


def _compact_warning_text(value: Any) -> str:
    text = str(value)
    for marker in (" missing: ", " unreadable: "):
        if marker in text:
            return text.split(marker, 1)[0] + marker.rstrip()
    return text


def compact_context_request(payload: Any) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return {"schema": REQUEST_SCHEMA, "needs": COMPACT_CONTEXT_DEFAULT_NEEDS, "responseMode": "compact"}, ["invalid_request"]
    if payload.get("schema") not in (None, REQUEST_SCHEMA):
        warnings.append("invalid_schema")
    if payload.get("task") not in (None, ""):
        warnings.append("unsupported_task")
    if payload.get("profile") not in (None, ""):
        warnings.append("unsupported_profile")
    unsupported_fields = sorted(set(payload) - COMPACT_CONTEXT_ALLOWED_REQUEST_FIELDS - {"task", "profile"})
    if unsupported_fields:
        warnings.append(f"unsupported_request_field_count:{len(unsupported_fields)}")

    raw_needs = payload.get("needs")
    if isinstance(raw_needs, str):
        raw_needs = [raw_needs]
    if not isinstance(raw_needs, list) or not raw_needs:
        if raw_needs is not None:
            warnings.append("invalid_needs")
        needs = list(COMPACT_CONTEXT_DEFAULT_NEEDS)
    else:
        needs = []
        unsupported_need_count = 0
        for need in raw_needs:
            text = str(need).strip().lower().replace("_", "-")
            if text in {"baseline", "state-baseline"}:
                text = "state"
            text = text.replace("-", "_")
            if text in COMPACT_CONTEXT_ALLOWED_NEEDS:
                needs.append(text)
            else:
                unsupported_need_count += 1
        if unsupported_need_count:
            warnings.append("unsupported_need")
            warnings.append(f"unsupported_need_count:{unsupported_need_count}")
        if not needs:
            needs = ["state"]

    response_mode = str(payload.get("responseMode") or "compact").strip().lower()
    if response_mode != "compact":
        warnings.append("unsupported_response_mode")
        response_mode = "compact"

    request = {"schema": REQUEST_SCHEMA, "needs": sorted(set(needs)), "responseMode": response_mode}
    max_age = query.as_int(payload.get("maxAgeMs"))
    if max_age is not None and max_age >= 0:
        request["maxAgeMs"] = max_age
    return request, warnings


def compact_context_response(state_baseline: dict, request_payload: Any | None = None) -> dict[str, Any]:
    request, request_warnings = compact_context_request(request_payload)
    needs = set(request["needs"])
    warnings = sorted(set(request_warnings + [_compact_warning_text(warning) for warning in state_baseline.get("warnings") or [] if warning]))
    errors = [str(field) for field in state_baseline.get("missingFields") or [] if field in {"baseline", "status"}]
    max_age = request.get("maxAgeMs")
    state_age = state_baseline.get("stateAgeMillis")
    if max_age is not None and isinstance(state_age, (int, float)) and state_age > max_age:
        errors.append(f"stateAgeMillis exceeds maxAgeMs: {int(state_age)} > {max_age}")
    if state_age is None and max_age is not None:
        warnings.append("state age is unavailable; maxAgeMs could not be evaluated.")

    response: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "generatedAtUtc": query.utc_now(),
    }

    if "state" in needs:
        response["state"] = {
            "gameState": state_baseline.get("gameState"),
            "loggedIn": state_baseline.get("loggedIn"),
            "latestTick": state_baseline.get("latestTick"),
            "timestampUtc": state_baseline.get("timestampUtc"),
            "stateAgeMillis": state_age,
            "staleThresholdMillis": state_baseline.get("staleThresholdMillis"),
        }
    if "player" in needs and isinstance(state_baseline.get("player"), dict):
        response["player"] = state_baseline.get("player")
    if "inventory" in needs and isinstance(state_baseline.get("inventory"), dict):
        response["inventory"] = state_baseline.get("inventory")
    if "activity" in needs and isinstance(state_baseline.get("activity"), dict):
        activity_summary = compact_activity(state_baseline.get("activity"))
        if activity_summary:
            response["activity"] = activity_summary
    if "liveness" in needs:
        response["liveness"] = {}
    if "source" in needs:
        response["source"] = _compact_source_metadata(state_baseline)
    return response
