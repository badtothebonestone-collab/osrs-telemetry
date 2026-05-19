from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SCHEMA = "action_proposal.v1"

KNOWN_ACTIONS = {
    "none",
    "select_resource_target",
    "wait_for_resource_result",
    "navigate_to_service",
    "open_service",
    "deposit_inventory",
    "deposit_resources",
    "close_bank",
    "return_to_resource_area",
    "wait_for_context",
}


@dataclass
class ActionProposal:
    proposed_action: str = "none"
    target_kind: str = "none"
    target_name: str | None = None
    target_tile: dict[str, Any] | None = None
    suggested_click_point: dict[str, int] | None = None
    click_point_space: str = "screen"
    suggested_world_tile: dict[str, Any] | None = None
    key_action: dict[str, str] | None = None
    reason: str = "not_applicable"
    confidence: float = 0.0
    required_context: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "PASS"
    source_tick: int | None = None

    @property
    def executable(self) -> bool:
        if self.proposed_action in {"none", "wait_for_context"}:
            return False
        return bool(self.key_action or self.suggested_click_point)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "proposedAction": self.proposed_action,
            "targetKind": self.target_kind,
            "targetName": self.target_name,
            "targetTile": self.target_tile,
            "suggestedClickPoint": self.suggested_click_point,
            "clickPointSpace": self.click_point_space,
            "suggestedWorldTile": self.suggested_world_tile,
            "keyAction": self.key_action,
            "reason": self.reason,
            "confidence": self.confidence,
            "requiredContext": list(self.required_context),
            "missingCapabilities": list(self.missing_capabilities),
            "warnings": list(self.warnings),
            "sourceTick": self.source_tick,
            "executable": self.executable,
        }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "ready", "available", "complete"}:
            return True
        if text in {"false", "no", "0", "closed", "not_ready", "unavailable", "incomplete"}:
            return False
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _target_name(target: Any) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    value = (
        target.get("targetName")
        or target.get("name")
        or target.get("label")
        or target.get("classId")
        or target.get("targetType")
        or target.get("id")
    )
    return str(value) if value is not None else None


def _tile_from(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict):
        return None
    if isinstance(target.get("targetTile"), dict):
        return dict(target["targetTile"])
    if isinstance(target.get("returnDestinationTile"), dict):
        return dict(target["returnDestinationTile"])
    if target.get("worldX") is not None and target.get("worldY") is not None:
        return {
            "worldX": target.get("worldX"),
            "worldY": target.get("worldY"),
            "plane": target.get("plane", 0),
        }
    return None


def _point_from_aim(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"), value.get("centerX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"), value.get("centerY"))
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return {"x": int(round(float(x))), "y": int(round(float(y)))}
    return None


def _point_space_from_aim(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("canvasX") is not None or value.get("canvasY") is not None or str(value.get("source") or "").lower().startswith("canvas"):
        return "canvas"
    if value.get("screenX") is not None or value.get("screenY") is not None:
        return "screen"
    return None


def _point_from_bounds(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("bounds"), dict):
        return _point_from_bounds(value.get("bounds"))
    x = _first_present(value.get("x"), value.get("canvasX"), value.get("screenX"), value.get("left"), value.get("minX"))
    y = _first_present(value.get("y"), value.get("canvasY"), value.get("screenY"), value.get("top"), value.get("minY"))
    width = _first_present(value.get("width"), value.get("w"))
    height = _first_present(value.get("height"), value.get("h"))
    if width is None and value.get("right") is not None and x is not None:
        width = float(value["right"]) - float(x)
    if height is None and value.get("bottom") is not None and y is not None:
        height = float(value["bottom"]) - float(y)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            return {"x": int(round(float(x) + float(width) / 2.0)), "y": int(round(float(y) + float(height) / 2.0))}
        return {"x": int(round(float(x))), "y": int(round(float(y)))}
    return None


def _point_space_from_bounds(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("bounds"), dict):
        return _point_space_from_bounds(value.get("bounds"))
    if any(key in value for key in ("canvasX", "canvasY", "canvasMinX", "canvasMinY")):
        return "canvas"
    if any(key in value for key in ("screenX", "screenY", "screenMinX", "screenMinY")):
        return "screen"
    source = str(value.get("source") or "").lower()
    if "canvas" in source or "clickbox" in source or "convex" in source:
        return "canvas"
    return None


def _click_point_from(target: Any) -> dict[str, int] | None:
    if not isinstance(target, dict):
        return None
    for key in (
        "suggestedClickPoint",
        "clickPoint",
        "aimPoint",
        "aimPointContext",
        "canvasPoint",
        "canvasLocation",
        "canvasCenter",
    ):
        point = _point_from_aim(target.get(key))
        if point:
            return point
    for key in (
        "bounds",
        "clickboxBounds",
        "convexHullBounds",
        "geometrySummary",
        "closeButtonBounds",
        "depositInventoryButtonBounds",
    ):
        point = _point_from_bounds(target.get(key))
        if point:
            return point
    geometry = _dict(target.get("geometry"))
    if geometry:
        return _click_point_from(geometry)
    return None


def _click_point_space_from(target: Any) -> str:
    if not isinstance(target, dict):
        return "screen"
    for key in (
        "suggestedClickPoint",
        "clickPoint",
        "aimPoint",
        "aimPointContext",
        "canvasPoint",
        "canvasLocation",
        "canvasCenter",
    ):
        space = _point_space_from_aim(target.get(key))
        if space:
            return space
        if key.startswith("canvas") and isinstance(target.get(key), dict):
            return "canvas"
    for key in (
        "bounds",
        "clickboxBounds",
        "convexHullBounds",
        "geometrySummary",
        "closeButtonBounds",
        "depositInventoryButtonBounds",
    ):
        space = _point_space_from_bounds(target.get(key))
        if space:
            return space
        if key in {"clickboxBounds", "convexHullBounds"} and isinstance(target.get(key), dict):
            return "canvas"
    geometry = _dict(target.get("geometry"))
    if geometry:
        return _click_point_space_from(geometry)
    return "screen"


def _overlay_selected(brain: dict[str, Any]) -> dict[str, Any]:
    overlay = _dict(brain.get("intentOverlayContext"))
    selected = _dict(overlay.get("selectedMarker") or overlay.get("selectedTarget"))
    if selected:
        return selected
    for marker in _list(overlay.get("markers")):
        if isinstance(marker, dict) and marker.get("markerType") == "selected_target":
            return marker
    return {}


def _status_context(status_or_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    status = status_or_context if isinstance(status_or_context, dict) else {}
    brain = _dict(status.get("brain")) or status
    return status, brain


def _proposal(
    action: str,
    *,
    target_kind: str,
    target: dict[str, Any] | None = None,
    click_point: dict[str, int] | None = None,
    key_action: dict[str, str] | None = None,
    reason: str,
    confidence: float,
    required_context: list[str] | None = None,
    warnings: list[str] | None = None,
    missing: list[str] | None = None,
    source_tick: int | None = None,
) -> ActionProposal:
    target = target if isinstance(target, dict) else {}
    proposal = ActionProposal(
        proposed_action=action if action in KNOWN_ACTIONS else "none",
        target_kind=target_kind,
        target_name=_target_name(target),
        target_tile=_tile_from(target),
        suggested_click_point=click_point or _click_point_from(target),
        click_point_space="screen" if click_point else _click_point_space_from(target),
        suggested_world_tile=_tile_from(target),
        key_action=key_action,
        reason=reason,
        confidence=confidence,
        required_context=required_context or [],
        missing_capabilities=missing or [],
        warnings=warnings or [],
        source_tick=source_tick,
    )
    if proposal.proposed_action not in {"none", "wait_for_context"} and not proposal.executable:
        proposal.status = "WARN"
        if "click_point" not in proposal.missing_capabilities:
            proposal.missing_capabilities.append("click_point")
        if not any("click point" in warning for warning in proposal.warnings):
            proposal.warnings.append("missing click point or key action")
    elif proposal.warnings or proposal.missing_capabilities:
        proposal.status = "WARN"
    else:
        proposal.status = "PASS"
    return proposal


def _service_target(service: dict[str, Any], generic: dict[str, Any]) -> dict[str, Any]:
    return _dict(service.get("bestServiceCandidate") or service.get("bestServiceTarget") or service.get("target") or generic.get("activeIntentTarget"))


def _deposit_inventory_target(bank_ui: dict[str, Any]) -> dict[str, Any]:
    return {
        "targetName": "Deposit inventory",
        "bounds": _dict(
            bank_ui.get("depositInventoryButtonBounds")
            or bank_ui.get("depositInventoryButtonWidget")
            or bank_ui.get("depositInventoryWidget")
        ),
        "aimPoint": _point_from_aim(bank_ui.get("depositInventoryButtonAimPoint")),
    }


def _deposit_resource_target(bank_operation: dict[str, Any]) -> dict[str, Any]:
    bounds_values = _list(bank_operation.get("resourceItemSlotBounds"))
    widget_values = _list(bank_operation.get("resourceItemWidgets") or bank_operation.get("resourceSlotWidgets"))
    first = bounds_values[0] if bounds_values else (widget_values[0] if widget_values else {})
    return {"targetName": "Resource item slot", "bounds": first}


def _close_bank_key(close_bank: dict[str, Any], bank_ui: dict[str, Any]) -> dict[str, str] | None:
    keyboard_close_possible = _first_present(close_bank.get("keyboardClosePossible"), bank_ui.get("keyboardClosePossible"))
    return {"type": "key_press", "key": "escape"} if _bool(keyboard_close_possible) is True else None


def _close_bank_target(close_bank: dict[str, Any], bank_ui: dict[str, Any]) -> dict[str, Any]:
    bounds = (
        close_bank.get("closeButtonBounds")
        or close_bank.get("closeButtonWidget")
        or bank_ui.get("closeButtonBounds")
        or bank_ui.get("bankCloseButtonBounds")
        or bank_ui.get("closeButtonWidget")
        or bank_ui.get("bankCloseButtonWidget")
    )
    return {"targetName": "Close bank", "bounds": _dict(bounds)}


def _path_target(pathing: dict[str, Any], fallback: dict[str, Any], name: str) -> dict[str, Any]:
    target = _dict(pathing.get("nextWaypointTarget") or pathing.get("destination") or fallback)
    merged = dict(target)
    merged.setdefault("targetName", name)
    if isinstance(pathing.get("nextWaypointTile"), dict):
        merged.setdefault("targetTile", pathing.get("nextWaypointTile"))
    for key in ("nextWaypointAimPoint", "pathClickPoint", "destinationAimPoint"):
        if isinstance(pathing.get(key), dict):
            merged.setdefault("aimPoint", pathing.get(key))
            break
    return merged


def build_action_proposal(status_or_context: dict[str, Any]) -> ActionProposal:
    status, brain = _status_context(status_or_context)
    generic = _dict(brain.get("genericTaskState"))
    inventory = _dict(brain.get("inventoryContext"))
    service = _dict(brain.get("serviceContext"))
    pathing = _dict(brain.get("pathingContext"))
    bank_ui = _dict(brain.get("bankUiContext"))
    bank_operation = _dict(brain.get("bankOperationContext"))
    close_bank = _dict(brain.get("closeBankContext"))
    resource_return = _dict(brain.get("resourceReturnContext"))
    active_target = _dict(generic.get("activeIntentTarget"))
    overlay_selected = _overlay_selected(brain)
    source_tick = status.get("latestTick") if isinstance(status.get("latestTick"), int) else None

    if _bool(bank_ui.get("bankPinOpen")) is True or "bank_pin_required" in _list(generic.get("blockingConditions")):
        return _proposal(
            "wait_for_context",
            target_kind="none",
            reason="bank_pin_required",
            confidence=1.0,
            warnings=["bank_pin_required"],
            required_context=["bank_ui"],
            source_tick=source_tick,
        )

    if _bool(bank_operation.get("bankingComplete")) is True and _bool(close_bank.get("closeBankReady")) is True:
        key_action = _close_bank_key(close_bank, bank_ui)
        return _proposal(
            "close_bank",
            target_kind="bank_ui",
            target=_close_bank_target(close_bank, bank_ui),
            key_action=key_action,
            reason="close_bank_ready",
            confidence=0.95,
            required_context=["bank_operation", "close_bank"],
            source_tick=source_tick,
        )

    if _bool(bank_ui.get("bankReadable")) is True and _int(bank_operation.get("resourceItemsHeld")) > 0:
        deposit_available = _bool(_first_present(bank_operation.get("depositInventoryAvailable"), bank_ui.get("depositInventoryButtonVisible")))
        if deposit_available is True:
            return _proposal(
                "deposit_inventory",
                target_kind="bank_ui",
                target=_deposit_inventory_target(bank_ui),
                reason="deposit_inventory_available",
                confidence=0.9,
                required_context=["bank_ui", "bank_operation"],
                source_tick=source_tick,
            )
        return _proposal(
            "deposit_resources",
            target_kind="bank_ui",
            target=_deposit_resource_target(bank_operation),
            reason="deposit_inventory_unavailable",
            confidence=0.72,
            required_context=["bank_ui", "bank_operation"],
            source_tick=source_tick,
        )

    if _bool(resource_return.get("returnDestinationAvailable")) is True:
        target = _path_target(pathing, active_target or {"returnDestinationTile": resource_return.get("returnDestinationTile")}, "Resource return")
        return _proposal(
            "return_to_resource_area",
            target_kind="path_tile",
            target=target,
            reason=str(resource_return.get("reason") or "using_remembered_resource_area"),
            confidence=0.78,
            required_context=["resource_return", "pathing"],
            source_tick=source_tick,
        )

    if _bool(_first_present(service.get("serviceReady"), pathing.get("serviceReady"))) is True and _bool(bank_ui.get("bankOpen")) is not True:
        return _proposal(
            "open_service",
            target_kind="service",
            target=_service_target(service, generic),
            reason="service_ready_bank_closed",
            confidence=0.86,
            required_context=["service", "bank_ui"],
            source_tick=source_tick,
        )

    if _bool(pathing.get("pathingNeeded")) is True:
        return _proposal(
            "navigate_to_service",
            target_kind="path_tile",
            target=_path_target(pathing, _service_target(service, generic), "Service waypoint"),
            reason="pathing_to_service",
            confidence=0.72,
            required_context=["pathing"],
            source_tick=source_tick,
        )

    inventory_full = _bool(_first_present(inventory.get("inventoryFull"), status.get("inventoryFull")))
    target = active_target or overlay_selected
    target_class = str(target.get("classId") or "").lower()
    target_type = str(target.get("targetType") or "").lower()
    if inventory_full is not True and target and (target_class in {"tree", "woodcutting_tree"} or "resource" in target_type or "tree" in target_class):
        return _proposal(
            "select_resource_target",
            target_kind="resource",
            target=target,
            reason="resource_target_visible",
            confidence=0.82,
            required_context=["target", "inventory"],
            source_tick=source_tick,
        )

    return _proposal(
        "wait_for_context",
        target_kind="none",
        reason="no_executable_action",
        confidence=0.2,
        warnings=["no executable action from current context"],
        source_tick=source_tick,
    )
