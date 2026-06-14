"""R3 read-only diagnostic boundary for compact context responses."""

from __future__ import annotations

from typing import Any


DIAGNOSTIC_SCHEMA = "recovery_diagnostic.v1"
CONTEXT_SCHEMA = "context_response.v1"

FORBIDDEN_CONTEXT_FIELD_NAMES = {
    "action",
    "actions",
    "click",
    "command",
    "commands",
    "execute",
    "input",
    "interact",
    "interaction",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
}

REQUIRED_CONTEXT_FACTS = [
    "schema",
    "ok",
    "state.gameState",
    "state.loggedIn",
    "state.latestTick_or_timestampUtc",
    "player.worldX",
    "player.worldY",
]


def _present(value: Any) -> bool:
    return value not in (None, "")


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_CONTEXT_FIELD_NAMES:
                return True
            if _contains_forbidden_field(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_field(child) for child in value)
    return False


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _observed_context(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
    return {
        "schema": payload.get("schema"),
        "ok": payload.get("ok"),
        "state_present": bool(state),
        "game_state_present": _present(state.get("gameState")),
        "login_flag_present": isinstance(state.get("loggedIn"), bool),
        "freshness_marker_present": _present(state.get("latestTick")) or _present(state.get("timestampUtc")),
        "player_present": bool(player),
        "player_world_x_present": _present(player.get("worldX")),
        "player_world_y_present": _present(player.get("worldY")),
        "error_count": _list_count(payload.get("errors")),
        "warning_count": _list_count(payload.get("warnings")),
    }


def evaluate_context(payload: Any) -> dict[str, Any]:
    """Return read-only diagnostic readiness for a compact context response."""
    if payload is None:
        return _diagnostic(False, "FAIL", ["missing_context"], {}, [])
    if not isinstance(payload, dict):
        return _diagnostic(False, "FAIL", ["malformed_context"], {}, [])

    observed = _observed_context(payload)
    reasons: list[str] = []
    warnings: list[str] = []

    if _contains_forbidden_field(payload):
        reasons.append("forbidden_context_field")
    if payload.get("schema") != CONTEXT_SCHEMA:
        reasons.append("unexpected_schema")
    if payload.get("ok") is not True:
        reasons.append("upstream_not_ok")

    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    player = payload.get("player") if isinstance(payload.get("player"), dict) else {}

    if not state:
        reasons.append("missing_state")
    else:
        if not _present(state.get("gameState")):
            reasons.append("missing_state_game_state")
        if not isinstance(state.get("loggedIn"), bool):
            reasons.append("missing_state_login_flag")
        if not (_present(state.get("latestTick")) or _present(state.get("timestampUtc"))):
            reasons.append("missing_state_freshness_marker")

    if not player:
        reasons.append("missing_player")
    else:
        if not _present(player.get("worldX")):
            reasons.append("missing_player_world_x")
        if not _present(player.get("worldY")):
            reasons.append("missing_player_world_y")

    error_count = _list_count(payload.get("errors"))
    if error_count:
        reasons.append(f"upstream_error_count:{error_count}")

    warning_count = _list_count(payload.get("warnings"))
    if warning_count:
        warnings.append(f"upstream_warning_count:{warning_count}")

    status = "PASS"
    if reasons:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    return _diagnostic(status != "FAIL", status, sorted(set(reasons)), observed, warnings)


def _diagnostic(
    ok: bool,
    status: str,
    reasons: list[str],
    observed_context: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "ok": ok,
        "status": status,
        "reasons": reasons,
        "required_context": list(REQUIRED_CONTEXT_FACTS),
        "observed_context": observed_context,
        "warnings": warnings,
    }
