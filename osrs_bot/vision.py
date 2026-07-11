from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .model import ScreenBounds, ScreenPoint


VISION_EVIDENCE_SCHEMA = "vision_evidence.v1"


def _positive_bounds(value: object, field_name: str) -> ScreenBounds:
    if (
        not isinstance(value, ScreenBounds)
        or any(
            not isinstance(component, int) or isinstance(component, bool)
            for component in (value.x, value.y, value.width, value.height)
        )
        or value.width <= 0
        or value.height <= 0
    ):
        raise ValueError(f"{field_name} must be positive ScreenBounds")
    return value


def _contains(outer: ScreenBounds, inner: ScreenBounds) -> bool:
    return outer.contains(ScreenPoint(inner.x, inner.y)) and outer.contains(
        ScreenPoint(inner.x + inner.width - 1, inner.y + inner.height - 1)
    )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty trimmed text")
    return value


@dataclass(frozen=True, slots=True)
class VisionCropTransform:
    window_screen_bounds: ScreenBounds
    canvas_screen_bounds: ScreenBounds
    crop_screen_bounds: ScreenBounds
    model_input_width: int
    model_input_height: int

    def __post_init__(self) -> None:
        window = _positive_bounds(self.window_screen_bounds, "window_screen_bounds")
        canvas = _positive_bounds(self.canvas_screen_bounds, "canvas_screen_bounds")
        crop = _positive_bounds(self.crop_screen_bounds, "crop_screen_bounds")
        if not _contains(window, canvas):
            raise ValueError("canvas_screen_bounds must be inside the exact window")
        if not _contains(canvas, crop):
            raise ValueError("crop_screen_bounds must be inside the exact canvas")
        for field_name, value in (
            ("model_input_width", self.model_input_width),
            ("model_input_height", self.model_input_height),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")

    @property
    def screen_to_model_scale_x(self) -> float:
        return self.model_input_width / self.crop_screen_bounds.width

    @property
    def screen_to_model_scale_y(self) -> float:
        return self.model_input_height / self.crop_screen_bounds.height

    def to_dict(self) -> dict[str, Any]:
        return {
            "windowScreenBounds": _bounds_dict(self.window_screen_bounds),
            "canvasScreenBounds": _bounds_dict(self.canvas_screen_bounds),
            "cropScreenBounds": _bounds_dict(self.crop_screen_bounds),
            "modelInput": {
                "width": self.model_input_width,
                "height": self.model_input_height,
            },
            "screenToModel": {
                "offsetX": -self.crop_screen_bounds.x,
                "offsetY": -self.crop_screen_bounds.y,
                "scaleX": self.screen_to_model_scale_x,
                "scaleY": self.screen_to_model_scale_y,
            },
        }


@dataclass(frozen=True, slots=True)
class VisionEvidence:
    captured_at: datetime
    transform: VisionCropTransform
    model_name: str
    model_version: str
    class_name: str
    confidence: float
    occlusion_status: str
    image_quality_status: str
    model_bounds: ScreenBounds | None = None
    model_mask: tuple[ScreenPoint, ...] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.captured_at, datetime)
            or self.captured_at.tzinfo is None
            or self.captured_at.utcoffset() is None
        ):
            raise ValueError("captured_at must be a timezone-aware datetime")
        if not isinstance(self.transform, VisionCropTransform):
            raise ValueError("transform must be VisionCropTransform")
        for field_name, value in (
            ("model_name", self.model_name),
            ("model_version", self.model_version),
            ("class_name", self.class_name),
            ("occlusion_status", self.occlusion_status),
            ("image_quality_status", self.image_quality_status),
        ):
            _text(value, field_name)
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ValueError("confidence must be between 0 and 1")
        if (self.model_bounds is None) == (self.model_mask is None):
            raise ValueError("exactly one of model_bounds or model_mask is required")
        if self.model_bounds is not None:
            bounds = _positive_bounds(self.model_bounds, "model_bounds")
            model = ScreenBounds(
                0,
                0,
                self.transform.model_input_width,
                self.transform.model_input_height,
            )
            if not _contains(model, bounds):
                raise ValueError("model_bounds must be inside the model input")
        if self.model_mask is not None:
            if (
                not isinstance(self.model_mask, tuple)
                or len(self.model_mask) < 3
                or any(not isinstance(point, ScreenPoint) for point in self.model_mask)
            ):
                raise ValueError("model_mask must be a tuple of at least three points")
            if any(
                not (
                    isinstance(point.x, int)
                    and not isinstance(point.x, bool)
                    and isinstance(point.y, int)
                    and not isinstance(point.y, bool)
                    and
                    0 <= point.x < self.transform.model_input_width
                    and 0 <= point.y < self.transform.model_input_height
                )
                for point in self.model_mask
            ):
                raise ValueError("model_mask points must be inside the model input")

    @property
    def authoritative(self) -> bool:
        return False

    @property
    def may_authorize_input(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": VISION_EVIDENCE_SCHEMA,
            "capturedAtUtc": self.captured_at.astimezone(timezone.utc).isoformat(),
            "transform": self.transform.to_dict(),
            "model": {"name": self.model_name, "version": self.model_version},
            "class": self.class_name,
            "confidence": float(self.confidence),
            "modelBounds": _bounds_dict(self.model_bounds),
            "modelMask": (
                None
                if self.model_mask is None
                else [{"x": point.x, "y": point.y} for point in self.model_mask]
            ),
            "occlusionStatus": self.occlusion_status,
            "imageQualityStatus": self.image_quality_status,
            "authoritative": False,
            "mayAuthorizeInput": False,
        }


def _bounds_dict(bounds: ScreenBounds | None) -> dict[str, int] | None:
    if bounds is None:
        return None
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }
