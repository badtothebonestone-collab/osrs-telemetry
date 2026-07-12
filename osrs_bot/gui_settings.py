from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


SETTINGS_SCHEMA = "osrs_gui_settings.v1"
DEFAULT_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / ".osrs-telemetry" / "gui-settings.json"
)
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ARDUINO_PORT = re.compile(r"^COM[1-9][0-9]{0,2}$", re.IGNORECASE)
_GEOMETRY = re.compile(
    r"^(?P<width>[0-9]{2,5})x(?P<height>[0-9]{2,5})"
    r"(?P<x>[+-][0-9]{1,6})(?P<y>[+-][0-9]{1,6})$"
)


class GuiSettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GuiSettings:
    """Harmless preferences only; never active engine or sensor state."""

    profile_id: str
    arduino_port: str = ""
    overlay_enabled: bool = False
    geometry: str | None = None
    last_demo_directory: str | None = None
    keep_terminal_summary_visible: bool = True

    def with_updates(self, **values: object) -> "GuiSettings":
        unknown = set(values).difference(asdict(self))
        if unknown:
            raise GuiSettingsError(
                f"unknown GUI setting(s): {', '.join(sorted(unknown))}"
            )
        return replace(self, **values)


class GuiSettingsStore:
    """Load and atomically persist the small, explicitly allowlisted settings."""

    def __init__(self, path: Path | str = DEFAULT_SETTINGS_PATH) -> None:
        self.path = Path(path)

    def load(
        self,
        catalog: Mapping[str, object],
        profile_validator: Callable[[Mapping[str, object]], object],
    ) -> GuiSettings:
        raw: Mapping[str, object] = {}
        try:
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict) and decoded.get("schema") == SETTINGS_SCHEMA:
                raw = decoded
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        return self.validate(raw, catalog, profile_validator)

    def save(
        self,
        settings: GuiSettings,
        catalog: Mapping[str, object],
        profile_validator: Callable[[Mapping[str, object]], object],
    ) -> GuiSettings:
        if not isinstance(settings, GuiSettings):
            raise TypeError("settings must be GuiSettings")
        validated = self.validate(
            {
                "profileId": settings.profile_id,
                "arduinoPort": settings.arduino_port,
                "overlayEnabled": settings.overlay_enabled,
                "keepTerminalSummaryVisible": settings.keep_terminal_summary_visible,
                "geometry": settings.geometry,
                "lastDemonstrationDirectory": settings.last_demo_directory,
            },
            catalog,
            profile_validator,
        )
        payload = {
            "schema": SETTINGS_SCHEMA,
            "profileId": validated.profile_id,
            "arduinoPort": validated.arduino_port,
            "overlayEnabled": validated.overlay_enabled,
            "keepTerminalSummaryVisible": validated.keep_terminal_summary_visible,
            "geometry": validated.geometry,
            "lastDemonstrationDirectory": validated.last_demo_directory,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return validated

    @staticmethod
    def profile_values(
        catalog: Mapping[str, object], profile_id: str
    ) -> dict[str, object]:
        values = _profile_defaults(catalog)
        values["profileId"] = profile_id
        return values

    @classmethod
    def validate(
        cls,
        raw: Mapping[str, object],
        catalog: Mapping[str, object],
        profile_validator: Callable[[Mapping[str, object]], object],
    ) -> GuiSettings:
        if not isinstance(raw, Mapping):
            raise TypeError("raw settings must be a mapping")
        if not callable(profile_validator):
            raise TypeError("profile_validator must be callable")
        defaults = _profile_defaults(catalog)
        default_profile = defaults.get("profileId")
        if not isinstance(default_profile, str) or not _PROFILE_ID.fullmatch(
            default_profile
        ):
            raise GuiSettingsError("catalog has no valid default profileId")

        candidate = raw.get("profileId", default_profile)
        profile_id = candidate if isinstance(candidate, str) else default_profile
        profile_id = profile_id.strip()
        if not _PROFILE_ID.fullmatch(profile_id) or not _profile_is_valid(
            profile_validator,
            cls.profile_values(catalog, profile_id),
        ):
            profile_id = default_profile
        if not _profile_is_valid(
            profile_validator,
            cls.profile_values(catalog, profile_id),
        ):
            raise GuiSettingsError("catalog default profile does not validate")

        arduino_port = _validated_port(raw.get("arduinoPort"))
        overlay = raw.get("overlayEnabled", False)
        overlay_enabled = overlay if isinstance(overlay, bool) else False
        keep_terminal = raw.get("keepTerminalSummaryVisible", True)
        keep_terminal_summary_visible = (
            keep_terminal if isinstance(keep_terminal, bool) else True
        )
        geometry = _validated_geometry(raw.get("geometry"))
        last_demo_directory = _validated_directory(
            raw.get("lastDemonstrationDirectory")
        )
        return GuiSettings(
            profile_id=profile_id,
            arduino_port=arduino_port,
            overlay_enabled=overlay_enabled,
            keep_terminal_summary_visible=keep_terminal_summary_visible,
            geometry=geometry,
            last_demo_directory=last_demo_directory,
        )


def _profile_defaults(catalog: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(catalog, Mapping):
        raise TypeError("catalog must be a mapping")
    profile = catalog.get("profile")
    if not isinstance(profile, Mapping):
        raise GuiSettingsError("catalog profile contract is missing")
    fields = profile.get("fields")
    if not isinstance(fields, list):
        raise GuiSettingsError("catalog profile fields are missing")
    values: dict[str, object] = {}
    for field in fields:
        if not isinstance(field, Mapping):
            raise GuiSettingsError("catalog profile field is malformed")
        name = field.get("name")
        if isinstance(name, str) and "default" in field:
            values[name] = field["default"]
    if "profileId" not in values:
        raise GuiSettingsError("catalog profileId default is missing")
    return values


def _profile_is_valid(
    validator: Callable[[Mapping[str, object]], object], values: Mapping[str, object]
) -> bool:
    try:
        validator(values)
    except (TypeError, ValueError, RuntimeError):
        return False
    return True


def _validated_port(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip().upper()
    return candidate if _ARDUINO_PORT.fullmatch(candidate) else ""


def _validated_geometry(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    match = _GEOMETRY.fullmatch(candidate)
    if match is None:
        return None
    width = int(match.group("width"))
    height = int(match.group("height"))
    if not 300 <= width <= 10_000 or not 240 <= height <= 10_000:
        return None
    return candidate


def _validated_directory(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 32_767:
        return None
    if any(ord(character) < 32 for character in candidate):
        return None
    return candidate


def settings_payload(settings: GuiSettings) -> dict[str, Any]:
    """Return the allowlisted public representation used by diagnostics/tests."""

    if not isinstance(settings, GuiSettings):
        raise TypeError("settings must be GuiSettings")
    return {
        "schema": SETTINGS_SCHEMA,
        "profileId": settings.profile_id,
        "arduinoPort": settings.arduino_port,
        "overlayEnabled": settings.overlay_enabled,
        "keepTerminalSummaryVisible": settings.keep_terminal_summary_visible,
        "geometry": settings.geometry,
        "lastDemonstrationDirectory": settings.last_demo_directory,
    }
