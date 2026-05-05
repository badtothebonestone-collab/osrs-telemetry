import argparse
import json
import mimetypes
import re
from collections import Counter, defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


HOST = "127.0.0.1"
DEFAULT_PORT = 8800
MISSING_WORLD_MESSAGE = "Run python telemetry-viewer\\build_world_target_geometry.py first."
MISSING_UI_MESSAGE = "Run python telemetry-viewer\\build_ui_target_geometry.py first."
MISSING_CANDIDATES_MESSAGE = "Run python telemetry-viewer\\select_target_candidates.py first."
FRAME_TICK_RE = re.compile(r"frame-tick-(\d+)\.[^.]+$", re.IGNORECASE)
FRAME_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    records = []
    warnings = []

    if not path.exists():
        return records, warnings

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()

                if not text:
                    continue

                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"{path.name}:{line_number}: invalid JSON: {error.msg}")
                    continue

                if isinstance(record, dict):
                    records.append(record)
                else:
                    warnings.append(f"{path.name}:{line_number}: expected JSON object")
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


def first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[0] if values else None


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "y"}:
        return True

    if normalized in {"0", "false", "no", "n"}:
        return False

    return None


def parse_tick(value: str | None) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def count_files(path: Path) -> int:
    if not path.exists():
        return 0

    try:
        return sum(1 for child in path.rglob("*") if child.is_file())
    except OSError:
        return 0


def compact_counts(counter: Counter, limit: int | None = None) -> dict:
    items = counter.most_common(limit)
    return {str(key): count for key, count in items}


def target_for(record: dict) -> dict:
    value = record.get("target")
    return value if isinstance(value, dict) else {}


def geometry_for(record: dict) -> dict:
    value = record.get("geometry")
    return value if isinstance(value, dict) else {}


def frame_for(record: dict) -> dict:
    value = record.get("frame")
    return value if isinstance(value, dict) else {}


def canvas_for(record: dict) -> dict:
    value = record.get("canvas")
    return value if isinstance(value, dict) else {}


def target_type_for(record: dict) -> str:
    return str(target_for(record).get("targetType") or "unknown")


def target_name_for(record: dict) -> str:
    target = target_for(record)
    value = target.get("name") or target.get("targetName") or target.get("kind") or target.get("regionName")

    if value is not None and str(value).strip():
        return str(value)

    target_type = target_type_for(record)
    target_id = target.get("rawId")

    if target_id is None:
        target_id = target.get("id")

    if target_type == "sceneObject":
        return f"SceneObject[{target_id if target_id is not None else 'unknown'}]"

    if target_type == "groundItem":
        return f"GroundItem[{target_id if target_id is not None else 'unknown'}]"

    if target_type == "tile":
        world = target.get("world")

        if isinstance(world, dict):
            return f"Tile[{world.get('x')},{world.get('y')},{world.get('plane')}]"

        return "Tile[unknown]"

    return ""


def target_id_for(record: dict):
    target = target_for(record)
    value = target.get("id")

    if value is None:
        value = target.get("rawId")

    if value is None:
        value = target.get("targetId")

    return value


def target_id_values(record: dict) -> list[str]:
    target = target_for(record)
    values = []

    for key in ("id", "rawId", "targetId"):
        value = target.get(key)

        if value is not None:
            values.append(str(value))

    return values


def target_role_for(record: dict) -> str:
    target = target_for(record)
    value = target.get("targetRole")

    if value:
        return str(value)

    source_kind = record.get("_inspector", {}).get("sourceKind")

    if source_kind == "ui":
        return "ui"

    target_type = target_type_for(record)

    if target_type in {"npc", "player"}:
        return "entity"

    if target_type == "groundItem":
        return "item"

    if target_type == "tile":
        return "navigation"

    return "unknown"


def target_category_for(record: dict) -> str:
    target = target_for(record)
    value = target.get("targetCategory")

    if value:
        return str(value)

    source_kind = record.get("_inspector", {}).get("sourceKind")

    if source_kind == "ui":
        return "ui"

    target_type = target_type_for(record)

    if target_type in {"npc", "player", "groundItem", "tile", "sceneObject"}:
        return target_type

    return "unknown"


def target_tags_for(record: dict) -> list[str]:
    target = target_for(record)
    value = target.get("targetTags")

    if isinstance(value, list):
        return [str(item) for item in value if item is not None]

    source_kind = record.get("_inspector", {}).get("sourceKind")

    if source_kind == "ui":
        return ["ui"]

    return []


def geometry_available_for(record: dict) -> bool:
    if record.get("_inspector", {}).get("sourceKind") == "candidate":
        return candidate_geometry_available(record)

    geometry = geometry_for(record)
    value = geometry.get("geometryAvailable")

    if isinstance(value, bool):
        return value

    return any(
        geometry.get(key) is not None
        for key in (
            "canvasPoint",
            "canvasLocation",
            "canvasCenter",
            "center",
            "pixelBox",
            "tilePolygon",
            "clickboxBounds",
            "clickboxPolygon",
            "convexHullBounds",
            "convexHullPolygon",
        )
    )


def on_screen_for(record: dict) -> bool | None:
    inspector = record.get("_inspector", {})

    if inspector.get("sourceKind") == "candidate":
        scoring = record.get("scoring") if isinstance(record.get("scoring"), dict) else {}
        reasons = scoring.get("reasons") if isinstance(scoring.get("reasons"), list) else []
        penalties = scoring.get("penalties") if isinstance(scoring.get("penalties"), list) else []

        if "onScreen" in reasons:
            return True

        if "offScreen" in penalties:
            return False

    geometry = geometry_for(record)
    value = geometry.get("onScreen")

    if isinstance(value, bool):
        return value

    return None


def candidate_geometry_available(record: dict) -> bool:
    geom = geometry_for(record)

    if geom.get("preferredAimGeometryType"):
        return True

    if isinstance(geom.get("availableGeometryTypes"), list) and geom.get("availableGeometryTypes"):
        return True

    return geom.get("aimPoint") is not None or geom.get("preferredAimGeometry") is not None


def point_summary(record: dict) -> str:
    geometry = geometry_for(record)

    for key in ("aimPoint", "canvasPoint", "canvasLocation", "canvasCenter", "center"):
        point = geometry.get(key)

        if isinstance(point, dict):
            x = point.get("x")
            y = point.get("y")

            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return f"{key} {x:.0f},{y:.0f}"

    return ""


def bounds_summary(record: dict) -> str:
    geometry = geometry_for(record)

    for key in ("aimBounds", "clickboxBounds", "convexHullBounds", "pixelBox"):
        bounds = geometry.get(key)

        if isinstance(bounds, dict):
            x = bounds.get("x")
            y = bounds.get("y")
            w = bounds.get("w")
            h = bounds.get("h")

            if all(isinstance(value, (int, float)) for value in (x, y, w, h)):
                return f"{key} {x:.0f},{y:.0f} {w:.0f}x{h:.0f}"

    polygons = (
        ("tilePolygon", geometry.get("tilePolygon")),
        ("clickboxPolygon", geometry.get("clickboxPolygon")),
        ("convexHullPolygon", geometry.get("convexHullPolygon")),
    )

    for key, polygon in polygons:
        if isinstance(polygon, list) and polygon:
            return f"{key} {len(polygon)} pts"

    return ""


def frame_path_text(record: dict) -> str | None:
    frame = frame_for(record)
    path = frame.get("path")
    return str(path) if path else None


class GeometryDataset:
    def __init__(self, session: Path | None):
        self.session = session
        self.geometry_dir = session / "interaction_geometry" if session else None
        self.world_targets_path = self.geometry_dir / "world_targets.jsonl" if self.geometry_dir else None
        self.world_index_path = self.geometry_dir / "world_geometry_index.json" if self.geometry_dir else None
        self.ui_targets_path = self.geometry_dir / "ui_targets.jsonl" if self.geometry_dir else None
        self.ui_index_path = self.geometry_dir / "ui_geometry_index.json" if self.geometry_dir else None
        self.candidates_path = self.geometry_dir / "target_candidates.jsonl" if self.geometry_dir else None
        self.candidates_index_path = self.geometry_dir / "target_candidates_index.json" if self.geometry_dir else None
        self.world_index = {}
        self.ui_index = {}
        self.candidates_index = {}
        self.world_records = []
        self.ui_records = []
        self.candidate_records = []
        self.records = []
        self.records_by_tick = defaultdict(list)
        self.candidates_by_tick = defaultdict(list)
        self.tick_summaries = {}
        self.warnings = []
        self.messages = []

    def load(self) -> None:
        if self.session is None:
            self.messages.append("No telemetry session found.")
            return

        self.world_index = self._read_index(self.world_index_path)
        self.ui_index = self._read_index(self.ui_index_path)
        self.candidates_index = self._read_index(self.candidates_index_path)
        self.world_records, world_warnings = read_jsonl(self.world_targets_path) if self.world_targets_path else ([], [])
        self.ui_records, ui_warnings = read_jsonl(self.ui_targets_path) if self.ui_targets_path else ([], [])
        self.candidate_records, candidate_warnings = (
            read_jsonl(self.candidates_path) if self.candidates_path else ([], [])
        )
        self.warnings.extend(world_warnings)
        self.warnings.extend(ui_warnings)
        self.warnings.extend(candidate_warnings)

        if not (self.world_targets_path and self.world_targets_path.exists()):
            self.messages.append(MISSING_WORLD_MESSAGE)

        if not (self.ui_targets_path and self.ui_targets_path.exists()):
            self.messages.append(MISSING_UI_MESSAGE)

        if not (self.candidates_path and self.candidates_path.exists()):
            self.messages.append(MISSING_CANDIDATES_MESSAGE)

        for source_kind, source_records in (("world", self.world_records), ("ui", self.ui_records)):
            for source_index, record in enumerate(source_records):
                tick_id = record.get("tickId")

                if not isinstance(tick_id, int):
                    continue

                decorated = dict(record)
                decorated["_inspector"] = {
                    "sourceKind": source_kind,
                    "sourceIndex": source_index,
                    "globalIndex": len(self.records),
                }
                self.records.append(decorated)
                self.records_by_tick[tick_id].append(decorated)

        for source_index, record in enumerate(self.candidate_records):
            tick_id = record.get("tickId")

            if not isinstance(tick_id, int):
                continue

            decorated = dict(record)
            decorated["_inspector"] = {
                "sourceKind": "candidate",
                "sourceIndex": source_index,
                "globalIndex": len(self.records) + source_index,
            }
            self.candidate_records[source_index] = decorated
            self.candidates_by_tick[tick_id].append(decorated)

        self._build_tick_summaries()

    def _read_index(self, path: Path | None) -> dict:
        if path is None:
            return {}

        value = safe_read_json(path)
        return value if isinstance(value, dict) else {}

    def _build_tick_summaries(self) -> None:
        tick_ids = sorted(set(self.records_by_tick) | set(self.candidates_by_tick))

        for tick_id in tick_ids:
            records = self.records_by_tick.get(tick_id, [])
            candidates = self.candidates_by_tick.get(tick_id, [])
            world_count = 0
            ui_count = 0
            frame_record = None
            canvas_record = None
            target_counts = Counter()

            for record in records:
                source_kind = record.get("_inspector", {}).get("sourceKind")

                if source_kind == "world":
                    world_count += 1
                elif source_kind == "ui":
                    ui_count += 1

                target_counts[target_type_for(record)] += 1

                if frame_record is None and frame_path_text(record):
                    frame_record = record

                if canvas_record is None and canvas_for(record):
                    canvas_record = record

            for candidate in candidates:
                target_counts[target_type_for(candidate)] += 1

                if frame_record is None and frame_path_text(candidate):
                    frame_record = candidate

            frame = frame_for(frame_record) if frame_record else {}
            canvas = canvas_for(canvas_record) if canvas_record else {}
            frame_path = frame.get("path")
            resolved = self.resolve_session_path(str(frame_path)) if frame_path else None
            frame_exists = bool(resolved and resolved.exists() and resolved.is_file())

            self.tick_summaries[tick_id] = {
                "tickId": tick_id,
                "targetCount": len(records),
                "worldTargetCount": world_count,
                "uiTargetCount": ui_count,
                "candidateCount": len(candidates),
                "countsByTargetType": compact_counts(target_counts),
                "framePath": str(frame_path) if frame_path else None,
                "frameExists": frame_exists,
                "frameExistsRecorded": frame.get("exists"),
                "frameWidth": frame.get("width"),
                "frameHeight": frame.get("height"),
                "canvasWidth": canvas.get("width"),
                "canvasHeight": canvas.get("height"),
            }

    def resolve_session_path(self, value: str) -> Path | None:
        if self.session is None:
            return None

        text = unquote(value).strip()

        if not text:
            return None

        path = Path(text)

        if not path.is_absolute():
            path = self.session / path

        try:
            resolved = path.resolve()
            resolved.relative_to(self.session.resolve())
        except (OSError, ValueError):
            return None

        return resolved

    def frame_path_for_tick(self, tick_id: int) -> Path | None:
        summary = self.tick_summaries.get(tick_id)

        if not summary or not summary.get("framePath"):
            return None

        return self.resolve_session_path(str(summary["framePath"]))

    def frame_files(self) -> list[Path]:
        if self.session is None:
            return []

        frames_dir = self.session / "frames"

        if not frames_dir.exists():
            return []

        try:
            return sorted(
                path
                for path in frames_dir.iterdir()
                if path.is_file() and path.suffix.lower() in FRAME_IMAGE_SUFFIXES
            )
        except OSError:
            return []

    def frame_file_summary(self) -> dict:
        files = self.frame_files()
        ticks = [tick for tick in (frame_tick_from_path(path) for path in files) if tick is not None]
        latest = max(files, key=lambda path: path.stat().st_mtime, default=None)
        return {
            "frameDir": str(self.session / "frames") if self.session else None,
            "frameFileCount": len(files),
            "firstFrameTick": min(ticks) if ticks else None,
            "lastFrameTick": max(ticks) if ticks else None,
            "latestFramePath": str(latest) if latest else None,
        }

    def frame_missing_count(self) -> int:
        return sum(1 for record in self.records if frame_path_text(record) and not self.frame_exists_for_record(record))

    def frame_exists_for_record(self, record: dict) -> bool:
        path = frame_path_text(record)

        if not path:
            return False

        resolved = self.resolve_session_path(path)
        return bool(resolved and resolved.exists() and resolved.is_file())

    def available_frame_tick_count(self) -> int:
        return sum(1 for summary in self.tick_summaries.values() if summary["frameExists"])

    def summary(self) -> dict:
        target_type_counts = Counter()
        target_role_counts = Counter()
        target_category_counts = Counter()
        target_tag_counts = Counter()
        name_counts = Counter()
        on_screen_counts = Counter()
        candidate_type_counts = Counter()
        candidate_category_counts = Counter()
        candidate_geometry_counts = Counter()
        recorded_frame_exists_true = 0
        recorded_frame_exists_false = 0

        for record in self.records:
            target_type_counts[target_type_for(record)] += 1
            target_role_counts[target_role_for(record)] += 1
            target_category_counts[target_category_for(record)] += 1

            for tag in target_tags_for(record):
                target_tag_counts[tag] += 1

            name = target_name_for(record)

            if name:
                name_counts[name] += 1

            on_screen = on_screen_for(record)
            on_screen_counts[str(on_screen).lower() if on_screen is not None else "unknown"] += 1

            frame_exists = frame_for(record).get("exists")

            if frame_exists is True:
                recorded_frame_exists_true += 1
            elif frame_exists is False:
                recorded_frame_exists_false += 1

        for candidate in self.candidate_records:
            candidate_type_counts[target_type_for(candidate)] += 1
            candidate_category_counts[target_category_for(candidate)] += 1
            preferred = geometry_for(candidate).get("preferredAimGeometryType") or "none"
            candidate_geometry_counts[preferred] += 1

        ticks = sorted(self.tick_summaries)
        frame_files = self.frame_file_summary()
        messages = list(self.messages)

        if ticks and frame_files["frameFileCount"] == 0:
            messages.append("No retained frame files were found under the selected session frames folder.")
        elif ticks and frame_files["firstFrameTick"] is not None and frame_files["lastFrameTick"] is not None:
            target_first = ticks[0]
            target_last = ticks[-1]
            frames_first = frame_files["firstFrameTick"]
            frames_last = frame_files["lastFrameTick"]

            if target_last < frames_first or target_first > frames_last:
                messages.append(
                    "Target geometry ticks do not overlap retained frame files. "
                    "Rebuild geometry with: "
                    "python telemetry-viewer\\build_world_target_geometry.py --latest-with-frames 100; "
                    "python telemetry-viewer\\build_ui_target_geometry.py --latest-with-frames 100 --include-base-regions"
                )

            if self.available_frame_tick_count() == 0 and self.frame_missing_count():
                messages.append(
                    "All target records with frame paths point at missing files. "
                    "This is usually retention or stale derived output, not a geometry error."
                )

        return {
            "sessionPath": str(self.session) if self.session else None,
            "geometryDir": str(self.geometry_dir) if self.geometry_dir else None,
            "worldTargetsPath": str(self.world_targets_path) if self.world_targets_path else None,
            "uiTargetsPath": str(self.ui_targets_path) if self.ui_targets_path else None,
            "targetCandidatesPath": str(self.candidates_path) if self.candidates_path else None,
            "worldGeometryExists": bool(self.world_targets_path and self.world_targets_path.exists()),
            "uiGeometryExists": bool(self.ui_targets_path and self.ui_targets_path.exists()),
            "targetCandidatesExist": bool(self.candidates_path and self.candidates_path.exists()),
            "worldTargetCount": len(self.world_records),
            "uiTargetCount": len(self.ui_records),
            "totalTargetCount": len(self.records),
            "targetCandidateCount": len(self.candidate_records),
            "targetCandidatesGeneratedAtUtc": self.candidates_index.get("generatedAtUtc"),
            "countsByCandidatePreferredAimGeometryType": (
                self.candidates_index.get("countsByPreferredAimGeometryType")
                if isinstance(self.candidates_index.get("countsByPreferredAimGeometryType"), dict)
                else compact_counts(candidate_geometry_counts)
            ),
            "topCandidateTargetTypes": (
                self.candidates_index.get("countsByTargetType")
                if isinstance(self.candidates_index.get("countsByTargetType"), dict)
                else compact_counts(candidate_type_counts, 25)
            ),
            "topCandidateTargetCategories": (
                self.candidates_index.get("countsByCategory")
                if isinstance(self.candidates_index.get("countsByCategory"), dict)
                else compact_counts(candidate_category_counts, 25)
            ),
            "countsByTargetType": compact_counts(target_type_counts),
            "countsByTargetRole": compact_counts(target_role_counts),
            "countsByTargetCategory": compact_counts(target_category_counts),
            "topTargetTags": compact_counts(target_tag_counts, 50),
            "topTargetNames": compact_counts(name_counts, 50),
            "countsByOnScreen": compact_counts(on_screen_counts),
            "firstTickId": ticks[0] if ticks else None,
            "lastTickId": ticks[-1] if ticks else None,
            "tickCount": len(ticks),
            "availableFrameTickCount": self.available_frame_tick_count(),
            "referencedFrameMissingTargetCount": self.frame_missing_count(),
            "frameFileCount": frame_files["frameFileCount"],
            "firstFrameFileTick": frame_files["firstFrameTick"],
            "lastFrameFileTick": frame_files["lastFrameTick"],
            "latestFrameFilePath": frame_files["latestFramePath"],
            "recordedFrameExistsTrueTargetCount": recorded_frame_exists_true,
            "recordedFrameExistsFalseTargetCount": recorded_frame_exists_false,
            "messages": messages,
            "warnings": self.warnings,
            "indexes": {
                "world": self.world_index,
                "ui": self.ui_index,
                "candidates": self.candidates_index,
            },
        }

    def ticks(self, *, frame_exists_only: bool = False) -> list[dict]:
        ticks = [self.tick_summaries[tick_id] for tick_id in sorted(self.tick_summaries)]

        if frame_exists_only:
            ticks = [tick for tick in ticks if tick["frameExists"]]

        return ticks

    def targets_for_tick(self, tick_id: int, filters: dict) -> list[dict]:
        records = self.records_by_tick.get(tick_id, [])
        matches = []

        for record in records:
            inspector = record.get("_inspector", {})
            source_kind = inspector.get("sourceKind")

            if source_kind == "ui" and filters.get("showUI") is False:
                continue

            if source_kind == "world" and filters.get("showWorld") is False:
                continue

            target_type = filters.get("targetType")
            target_types = filters.get("targetTypes") or set()

            if target_types and target_type_for(record) not in target_types:
                continue

            if target_type and target_type != "all" and target_type_for(record) != target_type:
                continue

            target_roles = filters.get("targetRoles") or set()

            if target_roles and target_role_for(record) not in target_roles:
                continue

            target_categories = filters.get("targetCategories") or set()

            if target_categories and target_category_for(record) not in target_categories:
                continue

            on_screen = filters.get("onScreen")

            if on_screen is True and on_screen_for(record) is not True:
                continue

            if filters.get("geometryAvailable") is True and not geometry_available_for(record):
                continue

            if filters.get("frameExists") is True and not self.frame_exists_for_record(record):
                continue

            name = filters.get("name")

            if name:
                target = target_for(record)
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        target_name_for(record),
                        target.get("name"),
                        target.get("targetName"),
                        target.get("kind"),
                        target.get("regionName"),
                        target.get("targetType"),
                        target_role_for(record),
                        target_category_for(record),
                        " ".join(target_tags_for(record)),
                        target.get("id"),
                        target.get("rawId"),
                        target.get("targetId"),
                        target.get("nameSource"),
                        target.get("npcNameSource"),
                        target.get("objectNameSource"),
                        target.get("itemNameSource"),
                        target.get("fallbackName"),
                    )
                ).lower()

                if name.lower() not in haystack:
                    continue

            tag_filter = filters.get("tag")

            if tag_filter:
                tags = " ".join(target_tags_for(record)).lower()

                if tag_filter.lower() not in tags:
                    continue

            id_filter = filters.get("id")

            if id_filter:
                needle = str(id_filter).lower()
                ids = [value.lower() for value in target_id_values(record)]

                if filters.get("idExact") is True:
                    if needle not in ids:
                        continue
                elif not any(needle in value for value in ids):
                    continue

            matches.append(self.decorate_target(record))

        return matches

    def candidates_for_tick(self, tick_id: int, filters: dict) -> list[dict]:
        records = self.candidates_by_tick.get(tick_id, [])
        matches = []
        limit = filters.get("limit")

        for record in records:
            target_type = filters.get("targetType")
            target_types = filters.get("targetTypes") or set()

            if target_types and target_type_for(record) not in target_types:
                continue

            if target_type and target_type != "all" and target_type_for(record) != target_type:
                continue

            target_roles = filters.get("targetRoles") or set()

            if target_roles and target_role_for(record) not in target_roles:
                continue

            target_categories = filters.get("targetCategories") or set()

            if target_categories and target_category_for(record) not in target_categories:
                continue

            on_screen = filters.get("onScreen")

            if on_screen is True and on_screen_for(record) is not True:
                continue

            if filters.get("geometryAvailable") is True and not geometry_available_for(record):
                continue

            if filters.get("frameExists") is True and not self.frame_exists_for_record(record):
                continue

            name = filters.get("name")

            if name:
                target = target_for(record)
                scoring = record.get("scoring") if isinstance(record.get("scoring"), dict) else {}
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        target_name_for(record),
                        target.get("name"),
                        target.get("targetName"),
                        target.get("targetType"),
                        target_role_for(record),
                        target_category_for(record),
                        " ".join(target_tags_for(record)),
                        target.get("id"),
                        target.get("rawId"),
                        target.get("targetId"),
                        " ".join(scoring.get("reasons") or []),
                        " ".join(scoring.get("penalties") or []),
                    )
                ).lower()

                if name.lower() not in haystack:
                    continue

            tag_filter = filters.get("tag")

            if tag_filter:
                tags = " ".join(target_tags_for(record)).lower()

                if tag_filter.lower() not in tags:
                    continue

            id_filter = filters.get("id")

            if id_filter:
                needle = str(id_filter).lower()
                ids = [value.lower() for value in target_id_values(record)]

                if filters.get("idExact") is True:
                    if needle not in ids:
                        continue
                elif not any(needle in value for value in ids):
                    continue

            matches.append(self.decorate_candidate(record))

            if isinstance(limit, int) and len(matches) >= limit:
                break

        return matches

    def decorate_target(self, record: dict) -> dict:
        target = target_for(record)
        geometry = geometry_for(record)
        inspector = record.get("_inspector", {})
        return {
            **record,
            "_inspector": {
                **inspector,
                "targetType": target_type_for(record),
                "name": target_name_for(record),
                "targetId": target.get("targetId"),
                "id": target_id_for(record),
                "rawId": target.get("rawId"),
                "nameSource": target.get("nameSource"),
                "targetRole": target_role_for(record),
                "targetCategory": target_category_for(record),
                "targetTags": target_tags_for(record),
                "onScreen": on_screen_for(record),
                "geometryAvailable": geometry_available_for(record),
                "pointSummary": point_summary(record),
                "boundsSummary": bounds_summary(record),
                "frameExists": self.frame_exists_for_record(record),
                "coordinateSpace": geometry.get("coordinateSpace"),
            },
        }

    def decorate_candidate(self, record: dict) -> dict:
        target = target_for(record)
        geom = geometry_for(record)
        scoring = record.get("scoring") if isinstance(record.get("scoring"), dict) else {}
        inspector = record.get("_inspector", {})
        return {
            **record,
            "_inspector": {
                **inspector,
                "targetType": target_type_for(record),
                "name": target_name_for(record),
                "targetId": target.get("targetId"),
                "id": target_id_for(record),
                "rawId": target.get("rawId"),
                "nameSource": target.get("nameSource"),
                "targetRole": target_role_for(record),
                "targetCategory": target_category_for(record),
                "targetTags": target_tags_for(record),
                "onScreen": on_screen_for(record),
                "geometryAvailable": geometry_available_for(record),
                "pointSummary": point_summary(record),
                "boundsSummary": bounds_summary(record),
                "frameExists": self.frame_exists_for_record(record),
                "coordinateSpace": geom.get("coordinateSpace"),
                "rank": record.get("rank"),
                "score": record.get("score"),
                "preferredAimGeometryType": geom.get("preferredAimGeometryType"),
                "reasons": scoring.get("reasons") if isinstance(scoring.get("reasons"), list) else [],
                "penalties": scoring.get("penalties") if isinstance(scoring.get("penalties"), list) else [],
            },
        }


def html_page() -> str:
    return r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Target Geometry Inspector</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d7dde8;
      --ink: #1f2937;
      --muted: #64748b;
      --world: #e11d48;
      --ui: #0f766e;
      --candidate: #7c3aed;
      --selected: #f59e0b;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 13px/1.35 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }

    h1 {
      margin: 0;
      font-size: 17px;
      font-weight: 650;
    }

    button, select, input {
      font: inherit;
    }

    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 6px 9px;
      cursor: pointer;
    }

    button:hover {
      border-color: #94a3b8;
    }

    input, select {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 5px 7px;
    }

    label {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      white-space: nowrap;
    }

    .summary {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 8px;
      padding: 10px 14px;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 8px 10px;
      min-width: 0;
    }

    .card b {
      display: block;
      font-size: 18px;
    }

    .card span {
      color: var(--muted);
      font-size: 12px;
    }

    .messages {
      padding: 0 14px 8px;
    }

    .warning {
      border: 1px solid #fbbf24;
      border-radius: 8px;
      background: #fffbeb;
      color: #78350f;
      margin-top: 6px;
      padding: 8px 10px;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 390px;
      gap: 10px;
      padding: 0 14px 14px;
      height: calc(100vh - 128px);
      min-height: 620px;
    }

    .panel {
      min-width: 0;
      min-height: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }

    .left-panel {
      display: grid;
      grid-template-rows: minmax(260px, 1fr) 230px 190px;
      gap: 10px;
      border: 0;
      background: transparent;
    }

    .stage-shell {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }

    .toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      padding: 8px;
      border-bottom: 1px solid var(--line);
    }

    .stage-wrap {
      min-height: 0;
      overflow: auto;
      background: #111827;
      padding: 10px;
    }

    .stage {
      position: relative;
      max-width: 100%;
      margin: 0 auto;
      background: #020617;
      color: #cbd5e1;
    }

    #frameImage {
      display: block;
      width: 100%;
      height: auto;
    }

    #blankFrame {
      display: none;
      width: 100%;
      min-height: 360px;
      place-items: center;
      text-align: center;
      padding: 32px;
      background:
        linear-gradient(45deg, rgba(148, 163, 184, 0.12) 25%, transparent 25%),
        linear-gradient(-45deg, rgba(148, 163, 184, 0.12) 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, rgba(148, 163, 184, 0.12) 75%),
        linear-gradient(-45deg, transparent 75%, rgba(148, 163, 184, 0.12) 75%);
      background-size: 28px 28px;
      background-position: 0 0, 0 14px, 14px -14px, -14px 0;
    }

    #overlay {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      overflow: visible;
    }

    .target-shape {
      cursor: pointer;
      vector-effect: non-scaling-stroke;
    }

    .world {
      stroke: var(--world);
      fill: rgba(225, 29, 72, 0.08);
    }

    .ui {
      stroke: var(--ui);
      fill: rgba(15, 118, 110, 0.08);
    }

    .candidate {
      stroke: var(--candidate);
      fill: rgba(124, 58, 237, 0.12);
    }

    .selected {
      stroke: var(--selected);
      fill: rgba(245, 158, 11, 0.18);
      stroke-width: 3;
    }

    .dot {
      fill: currentColor;
      stroke: #fff;
      stroke-width: 1.5;
      vector-effect: non-scaling-stroke;
    }

    .label {
      paint-order: stroke;
      stroke: #111827;
      stroke-width: 3;
      fill: #fff;
      font-size: 12px;
      pointer-events: none;
    }

    .table-shell {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 12px;
    }

    th, td {
      border-bottom: 1px solid #eef2f7;
      padding: 5px 7px;
      vertical-align: middle;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f8fafc;
      color: #475569;
      font-weight: 650;
      text-align: left;
    }

    tbody tr {
      cursor: pointer;
      height: 34px;
    }

    tbody tr:hover {
      background: #f8fafc;
    }

    tbody tr.active {
      background: #fff7ed;
    }

    .scroll {
      min-height: 0;
      overflow: auto;
    }

    .right-panel {
      display: grid;
      grid-template-rows: auto minmax(130px, 0.72fr) minmax(180px, 1fr);
    }

    .filters {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }

    .filters .full {
      grid-column: 1 / -1;
    }

    .filters label {
      align-items: flex-start;
      flex-direction: column;
      gap: 3px;
    }

    .check-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      grid-column: 1 / -1;
    }

    .type-grid,
    .quick-row,
    .visual-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      grid-column: 1 / -1;
      align-items: center;
    }

    .type-grid label,
    .quick-row label,
    .visual-controls label,
    .check-row label {
      align-items: center;
      flex-direction: row;
      gap: 5px;
    }

    .section-label {
      width: 100%;
      color: var(--muted);
      font-weight: 650;
      font-size: 12px;
    }

    .quick-row button {
      padding: 4px 7px;
      font-size: 12px;
    }

    .visual-controls select {
      width: 118px;
    }

    .visual-controls input[type="number"] {
      width: 84px;
    }

    .visual-controls input[type="range"] {
      width: 96px;
    }

    .tick-list {
      border-bottom: 1px solid var(--line);
    }

    .tick-row {
      display: grid;
      grid-template-columns: 78px 1fr 70px;
      gap: 8px;
      padding: 5px 8px;
      border-bottom: 1px solid #eef2f7;
      cursor: pointer;
      white-space: nowrap;
    }

    .tick-row:hover,
    .tick-row.active {
      background: #eef6ff;
    }

    .muted {
      color: var(--muted);
    }

    .badge {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 6px;
      font-size: 11px;
      color: #475569;
      background: #f8fafc;
      max-width: 100%;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.35 Consolas, "SFMono-Regular", monospace;
    }

    .details {
      padding: 10px;
      overflow: auto;
    }

    @media (max-width: 1180px) {
      .layout {
        grid-template-columns: 1fr;
        height: auto;
      }

      .right-panel {
        min-height: 720px;
      }

      .summary {
        grid-template-columns: repeat(3, minmax(120px, 1fr));
      }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Target Geometry Inspector</h1>
      <div id="sessionPath" class="muted"></div>
    </div>
    <div class="muted">Read-only QA overlay for existing UI/world target geometry</div>
  </header>

  <section class="summary" id="summaryCards"></section>
  <section class="messages" id="messages"></section>

  <main class="layout">
    <section class="left-panel">
      <section class="panel stage-shell">
        <div class="toolbar">
          <button id="prevTick">Previous Tick</button>
          <button id="nextTick">Next Tick</button>
          <label>Jump to tick <input id="jumpTick" type="number" min="0"></label>
          <button id="jumpTickButton">Jump</button>
          <label><input id="scaleCanvas" type="checkbox" checked> Scale canvas geometry to frame</label>
          <span id="status" class="muted"></span>
        </div>
        <div class="stage-wrap">
          <div class="stage" id="stage">
            <img id="frameImage" alt="selected frame">
            <div id="blankFrame">Frame file missing or expired by retention. Geometry data is still available.</div>
            <svg id="overlay" aria-label="target geometry overlay"></svg>
          </div>
        </div>
      </section>

      <section class="panel table-shell">
        <div class="toolbar">
          <strong>Targets</strong>
          <span id="targetCount" class="muted"></span>
        </div>
        <div class="scroll">
          <table>
            <thead>
              <tr>
                <th style="width:68px">Tick</th>
                <th style="width:58px">Kind</th>
                <th style="width:105px">Type</th>
                <th style="width:92px">Role</th>
                <th style="width:112px">Category</th>
                <th>Name</th>
                <th style="width:80px">ID</th>
                <th style="width:112px">Name source</th>
                <th style="width:76px">On</th>
                <th style="width:78px">Geom</th>
                <th style="width:135px">Point</th>
                <th style="width:170px">Bounds</th>
                <th style="width:78px">Frame</th>
              </tr>
            </thead>
            <tbody id="targetRows"></tbody>
          </table>
        </div>
      </section>

      <section class="panel table-shell">
        <div class="toolbar">
          <strong>Ranked Candidates</strong>
          <span id="candidateCount" class="muted"></span>
        </div>
        <div class="scroll">
          <table>
            <thead>
              <tr>
                <th style="width:54px">Rank</th>
                <th style="width:58px">Score</th>
                <th style="width:68px">Tick</th>
                <th style="width:102px">Type</th>
                <th>Name</th>
                <th style="width:78px">ID</th>
                <th style="width:90px">Role</th>
                <th style="width:100px">Category</th>
                <th style="width:132px">Geometry</th>
                <th style="width:96px">Aim</th>
                <th style="width:220px">Reasons</th>
              </tr>
            </thead>
            <tbody id="candidateRows"></tbody>
          </table>
        </div>
      </section>
    </section>

    <aside class="panel right-panel">
      <section class="filters">
        <div class="full type-grid">
          <div class="section-label">Target categories</div>
          <label><input class="target-type-box" type="checkbox" value="npc" checked> NPC</label>
          <label><input class="target-type-box" type="checkbox" value="player" checked> Player</label>
          <label><input class="target-type-box" type="checkbox" value="sceneObject" checked> Scene object</label>
          <label><input class="target-type-box" type="checkbox" value="groundItem" checked> Ground item</label>
          <label><input class="target-type-box" type="checkbox" value="tile" checked> Tile</label>
          <label><input id="showUI" type="checkbox" checked> UI targets</label>
        </div>
        <label>Target role
          <select id="targetRole">
            <option value="">all roles</option>
          </select>
        </label>
        <label>Target category
          <select id="targetCategory">
            <option value="">all categories</option>
          </select>
        </label>
        <label>Name contains
          <input id="nameFilter" type="search" placeholder="Goblin, banker, inventory">
        </label>
        <label>ID contains
          <input id="idFilter" type="search" placeholder="12345">
        </label>
        <label class="full">Tag contains
          <input id="tagFilter" type="search" placeholder="bank, wall, navigation_geometry">
        </label>
        <div class="check-row">
          <label><input id="showRawTargets" type="checkbox" checked> show raw targets</label>
          <label><input id="showCandidates" type="checkbox"> show ranked candidates</label>
          <label><input id="onScreenOnly" type="checkbox"> onScreen only</label>
          <label><input id="geometryOnly" type="checkbox"> geometry available only</label>
          <label><input id="frameExistsOnly" type="checkbox"> only ticks with frames</label>
          <label><input id="idExact" type="checkbox"> exact ID</label>
        </div>
        <div class="full quick-row">
          <div class="section-label">Quick filters</div>
          <button type="button" data-quick-filter="npcs">NPCs only</button>
          <button type="button" data-quick-filter="sceneObjects">Scene objects only</button>
          <button type="button" data-quick-filter="groundItems">Ground items only</button>
          <button type="button" data-quick-filter="interactables">Interactables</button>
          <button type="button" data-quick-filter="obstacles">Obstacles/Walls</button>
          <button type="button" data-quick-filter="navigation">Navigation geometry</button>
          <button type="button" data-quick-filter="bank">Bank-related</button>
          <button type="button" data-quick-filter="trees">Trees</button>
          <button type="button" data-quick-filter="doors">Doors</button>
          <button type="button" data-quick-filter="clear">Clear filters</button>
        </div>
        <div class="full visual-controls">
          <div class="section-label">Overlay clutter</div>
          <label>Labels
            <select id="showLabels">
              <option value="selected">selected only</option>
              <option value="all">all</option>
              <option value="none">none</option>
            </select>
          </label>
          <label>Polygons
            <select id="showPolygons">
              <option value="selected">selected only</option>
              <option value="all">all</option>
              <option value="none">none</option>
            </select>
          </label>
          <label>Bounds
            <select id="showBounds">
              <option value="all">all</option>
              <option value="selected">selected only</option>
              <option value="none">none</option>
            </select>
          </label>
          <label>Opacity <input id="overlayOpacity" type="range" min="0.15" max="1" step="0.05" value="0.7"></label>
          <label>Max draw <input id="maxTargets" type="number" min="1" max="5000" value="200"></label>
        </div>
        <div class="full visual-controls">
          <div class="section-label">Candidate overlay</div>
          <label><input id="showCandidateAim" type="checkbox" checked> aim point</label>
          <label><input id="showCandidateGeometry" type="checkbox" checked> preferred geometry</label>
          <label>Candidate label
            <select id="candidateLabelMode">
              <option value="nameRank" selected>name + rank</option>
              <option value="rankScore">rank + score</option>
              <option value="name">name</option>
              <option value="nameScore">name + score</option>
              <option value="none">none</option>
            </select>
          </label>
        </div>
        <button id="applyFilters">Apply Filters</button>
        <button id="clearFilters">Clear</button>
        <div class="full warning" id="diagnostics" style="display:none"></div>
      </section>

      <section class="tick-list">
        <div class="toolbar">
          <strong>Ticks</strong>
          <span id="tickCount" class="muted"></span>
        </div>
        <div class="scroll" id="tickRows"></div>
      </section>

      <section class="details">
        <h2 style="font-size:14px;margin:0 0 8px">Selected Target</h2>
        <pre id="targetDetails">Select a target row or overlay shape.</pre>
      </section>
    </aside>
  </main>

  <script>
    const state = {
      summary: null,
      ticks: [],
      selectedTickId: null,
      targets: [],
      candidates: [],
      selectedGlobalIndex: null,
      frameNatural: null,
      scaleCanvas: true,
      hiddenByDrawLimit: 0,
      drawLimit: 200,
    };

    const el = {
      sessionPath: document.getElementById("sessionPath"),
      summaryCards: document.getElementById("summaryCards"),
      messages: document.getElementById("messages"),
      status: document.getElementById("status"),
      stage: document.getElementById("stage"),
      frameImage: document.getElementById("frameImage"),
      blankFrame: document.getElementById("blankFrame"),
      overlay: document.getElementById("overlay"),
      targetRows: document.getElementById("targetRows"),
      candidateRows: document.getElementById("candidateRows"),
      targetCount: document.getElementById("targetCount"),
      candidateCount: document.getElementById("candidateCount"),
      tickRows: document.getElementById("tickRows"),
      tickCount: document.getElementById("tickCount"),
      targetDetails: document.getElementById("targetDetails"),
      targetTypeBoxes: Array.from(document.querySelectorAll(".target-type-box")),
      targetRole: document.getElementById("targetRole"),
      targetCategory: document.getElementById("targetCategory"),
      nameFilter: document.getElementById("nameFilter"),
      idFilter: document.getElementById("idFilter"),
      tagFilter: document.getElementById("tagFilter"),
      idExact: document.getElementById("idExact"),
      showRawTargets: document.getElementById("showRawTargets"),
      showCandidates: document.getElementById("showCandidates"),
      onScreenOnly: document.getElementById("onScreenOnly"),
      geometryOnly: document.getElementById("geometryOnly"),
      showUI: document.getElementById("showUI"),
      frameExistsOnly: document.getElementById("frameExistsOnly"),
      showLabels: document.getElementById("showLabels"),
      showPolygons: document.getElementById("showPolygons"),
      showBounds: document.getElementById("showBounds"),
      overlayOpacity: document.getElementById("overlayOpacity"),
      maxTargets: document.getElementById("maxTargets"),
      showCandidateAim: document.getElementById("showCandidateAim"),
      showCandidateGeometry: document.getElementById("showCandidateGeometry"),
      candidateLabelMode: document.getElementById("candidateLabelMode"),
      applyFilters: document.getElementById("applyFilters"),
      clearFilters: document.getElementById("clearFilters"),
      diagnostics: document.getElementById("diagnostics"),
      prevTick: document.getElementById("prevTick"),
      nextTick: document.getElementById("nextTick"),
      jumpTick: document.getElementById("jumpTick"),
      jumpTickButton: document.getElementById("jumpTickButton"),
      scaleCanvas: document.getElementById("scaleCanvas"),
    };

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function nonEmptyText(value) {
      const text = String(value ?? "").trim();
      return text ? text : "";
    }

    function bestDisplayName(record) {
      const target = record.target || {};
      const info = record._inspector || {};
      const values = [
        target.name,
        target.targetName,
        target.objectName,
        target.itemName,
        target.npcName,
        target.fallbackName,
        target.targetId,
        info.name,
      ];

      for (const value of values) {
        const text = nonEmptyText(value);
        if (text) return text;
      }

      const targetType = target.targetType || info.targetType || "Target";
      const id = target.rawId ?? target.id ?? info.rawId ?? info.id;
      return id !== undefined && id !== null ? `${targetType}[${id}]` : targetType;
    }

    function candidateLabel(candidate) {
      const mode = el.candidateLabelMode.value || "nameRank";
      const name = bestDisplayName(candidate);
      const rank = candidate.rank !== undefined && candidate.rank !== null ? `#${candidate.rank}` : "";
      const score = candidate.score !== undefined && candidate.score !== null ? String(candidate.score) : "";

      if (mode === "none") return "";
      if (mode === "rankScore") return [rank, score].filter(Boolean).join(" ");
      if (mode === "name") return name;
      if (mode === "nameScore") return [name, score].filter(Boolean).join(" ");
      return [rank, name].filter(Boolean).join(" ");
    }

    function jsonUrl(path, params = {}) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== "") query.set(key, value);
      }
      const suffix = query.toString();
      return suffix ? `${path}?${suffix}` : path;
    }

    async function fetchJson(path, params = {}) {
      const response = await fetch(jsonUrl(path, params));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }

    function selectedTick() {
      return state.ticks.find((tick) => tick.tickId === state.selectedTickId) || null;
    }

    function renderSummary(summary) {
      el.sessionPath.textContent = summary.sessionPath || "No session selected";
        const cards = [
          ["World targets", summary.worldTargetCount],
          ["UI targets", summary.uiTargetCount],
          ["Candidates", summary.targetCandidateCount],
          ["Ticks", summary.tickCount],
          ["Target ticks with frames", summary.availableFrameTickCount],
          ["JPG/PNG files on disk", summary.frameFileCount],
          ["Missing-frame targets", summary.referencedFrameMissingTargetCount],
          ["On-screen true", summary.countsByOnScreen?.["true"] || 0],
        ];
      el.summaryCards.innerHTML = cards.map(([label, value]) => (
        `<div class="card"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`
      )).join("");

      const notes = [];
      for (const message of summary.messages || []) notes.push(message);
      for (const warning of summary.warnings || []) notes.push(warning);
      if (summary.firstTickId !== null && summary.firstFrameFileTick !== null) {
        notes.push(`Target tick range ${summary.firstTickId}-${summary.lastTickId}; retained frame tick range ${summary.firstFrameFileTick}-${summary.lastFrameFileTick}.`);
      }
      el.messages.innerHTML = notes.map((message) => `<div class="warning">${escapeHtml(message)}</div>`).join("");

      populateSelect(el.targetRole, summary.countsByTargetRole || {}, "all roles");
      populateSelect(el.targetCategory, summary.countsByTargetCategory || {}, "all categories");

      if (summary.targetCandidatesExist && Number(summary.targetCandidateCount || 0) > 0) {
        el.showCandidates.checked = true;
      }
    }

    function populateSelect(select, counts, allLabel) {
      const current = select.value;
      const values = Object.keys(counts || {}).sort((a, b) => a.localeCompare(b));
      select.innerHTML = `<option value="">${escapeHtml(allLabel)}</option>` + values.map((value) => (
        `<option value="${escapeHtml(value)}">${escapeHtml(value)} (${escapeHtml(counts[value])})</option>`
      )).join("");
      if (values.includes(current)) select.value = current;
    }

    function renderTicks() {
      el.tickCount.textContent = `${state.ticks.length} ticks`;
      el.tickRows.innerHTML = state.ticks.map((tick) => {
        const frame = tick.frameExists ? "frame ok" : "missing";
        const active = tick.tickId === state.selectedTickId ? " active" : "";
        return `
          <div class="tick-row${active}" data-tick="${tick.tickId}">
            <strong>${tick.tickId}</strong>
            <span title="${escapeHtml(tick.framePath || "")}">
              ${tick.worldTargetCount} world / ${tick.uiTargetCount} ui / ${tick.candidateCount || 0} cand
            </span>
            <span class="badge">${frame}</span>
          </div>
        `;
      }).join("");
    }

    function selectedTargetTypes() {
      return el.targetTypeBoxes
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function setTargetTypes(types) {
      const selected = new Set(types);
      for (const input of el.targetTypeBoxes) {
        input.checked = selected.has(input.value);
      }
    }

    function maxTargetsToDraw() {
      const value = Number(el.maxTargets.value);
      return Number.isFinite(value) && value > 0 ? Math.max(1, Math.floor(value)) : 200;
    }

    function filterParams() {
      const targetTypes = selectedTargetTypes();
      return {
        tick: state.selectedTickId,
        targetTypes: targetTypes.join(","),
        targetRoles: el.targetRole.value,
        targetCategories: el.targetCategory.value,
        name: el.nameFilter.value.trim(),
        tag: el.tagFilter.value.trim(),
        id: el.idFilter.value.trim(),
        idExact: el.idExact.checked ? "true" : "",
        onScreen: el.onScreenOnly.checked ? "true" : "",
        geometryAvailable: el.geometryOnly.checked ? "true" : "",
        showUI: el.showUI.checked ? "true" : "false",
        showWorld: targetTypes.length ? "true" : "false",
        frameExists: el.frameExistsOnly.checked ? "true" : "",
      };
    }

    async function loadTicks({ keepSelection = false } = {}) {
      const ticks = await fetchJson("/api/ticks", { frameExistsOnly: el.frameExistsOnly.checked ? "true" : "" });
      state.ticks = ticks.ticks || [];

      if (!keepSelection || !state.ticks.some((tick) => tick.tickId === state.selectedTickId)) {
        const firstWithFrame = state.ticks.find((tick) => tick.frameExists);
        state.selectedTickId = (firstWithFrame || state.ticks[0] || {}).tickId ?? null;
      }

      renderTicks();
      await loadTargets();
    }

    async function loadTargets() {
      if (state.selectedTickId === null) {
        state.targets = [];
        state.candidates = [];
        renderTargets();
        renderCandidates();
        renderFrame();
        return;
      }

      const params = filterParams();
      if (el.showRawTargets.checked) {
        const data = await fetchJson("/api/targets", params);
        state.targets = data.targets || [];
      } else {
        state.targets = [];
      }
      if (el.showCandidates.checked) {
        const candidateData = await fetchJson("/api/candidates", { ...params, limit: 250 });
        state.candidates = candidateData.candidates || [];
      } else {
        state.candidates = [];
      }
      const selectedExists = [...state.targets, ...state.candidates].some((record) => (record._inspector || {}).globalIndex === state.selectedGlobalIndex);
      if (!selectedExists) {
        state.selectedGlobalIndex = state.candidates[0]?._inspector?.globalIndex ?? state.targets[0]?._inspector?.globalIndex ?? null;
      }
      renderTicks();
      renderTargets();
      renderCandidates();
      renderFrame();
      renderDiagnostics();
      renderDetails();
    }

    function renderTargets() {
      el.targetCount.textContent = `${state.targets.length} matching targets`;
      el.targetRows.innerHTML = state.targets.map((record) => {
        const info = record._inspector || {};
        const active = info.globalIndex === state.selectedGlobalIndex ? " active" : "";
        return `
          <tr class="${active}" data-index="${info.globalIndex}">
            <td>${escapeHtml(record.tickId)}</td>
            <td>${escapeHtml(info.sourceKind || "")}</td>
            <td>${escapeHtml(info.targetType || "")}</td>
            <td title="${escapeHtml(info.targetRole || "")}">${escapeHtml(info.targetRole || "")}</td>
            <td title="${escapeHtml(info.targetCategory || "")}">${escapeHtml(info.targetCategory || "")}</td>
            <td title="${escapeHtml(info.name || "")}">${escapeHtml(info.name || "")}</td>
            <td title="${escapeHtml(info.id ?? "")}">${escapeHtml(info.id ?? "")}</td>
            <td title="${escapeHtml(info.nameSource ?? "")}">${escapeHtml(info.nameSource ?? "")}</td>
            <td>${info.onScreen === true ? "true" : info.onScreen === false ? "false" : "?"}</td>
            <td>${info.geometryAvailable ? "yes" : "no"}</td>
            <td title="${escapeHtml(info.pointSummary || "")}">${escapeHtml(info.pointSummary || "")}</td>
            <td title="${escapeHtml(info.boundsSummary || "")}">${escapeHtml(info.boundsSummary || "")}</td>
            <td>${info.frameExists ? "ok" : "missing"}</td>
          </tr>
        `;
      }).join("");
    }

    function candidateAimText(candidate) {
      const point = (candidate.geometry || {}).aimPoint;
      if (point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))) {
        return `${Math.round(Number(point.x))},${Math.round(Number(point.y))}`;
      }
      return "";
    }

    function renderCandidates() {
      el.candidateCount.textContent = `${state.candidates.length} matching candidates`;
      el.candidateRows.innerHTML = state.candidates.map((record) => {
        const info = record._inspector || {};
        const target = record.target || {};
        const geom = record.geometry || {};
        const scoring = record.scoring || {};
        const active = info.globalIndex === state.selectedGlobalIndex ? " active" : "";
        const reasons = Array.isArray(scoring.reasons) ? scoring.reasons.join(",") : "";
        return `
          <tr class="${active}" data-index="${info.globalIndex}">
            <td>${escapeHtml(record.rank ?? "")}</td>
            <td>${escapeHtml(record.score ?? "")}</td>
            <td>${escapeHtml(record.tickId ?? "")}</td>
            <td>${escapeHtml(info.targetType || target.targetType || "")}</td>
            <td title="${escapeHtml(info.name || target.name || "")}">${escapeHtml(info.name || target.name || "")}</td>
            <td title="${escapeHtml(info.id ?? target.id ?? target.rawId ?? "")}">${escapeHtml(info.id ?? target.id ?? target.rawId ?? "")}</td>
            <td title="${escapeHtml(info.targetRole || target.targetRole || "")}">${escapeHtml(info.targetRole || target.targetRole || "")}</td>
            <td title="${escapeHtml(info.targetCategory || target.targetCategory || "")}">${escapeHtml(info.targetCategory || target.targetCategory || "")}</td>
            <td title="${escapeHtml(geom.preferredAimGeometryType || "")}">${escapeHtml(geom.preferredAimGeometryType || "")}</td>
            <td>${escapeHtml(candidateAimText(record))}</td>
            <td title="${escapeHtml(reasons)}">${escapeHtml(reasons)}</td>
          </tr>
        `;
      }).join("");
    }

    function tickDims() {
      const tick = selectedTick() || {};
      const frameWidth = Number(tick.frameWidth) || state.frameNatural?.width || null;
      const frameHeight = Number(tick.frameHeight) || state.frameNatural?.height || null;
      const canvasWidth = Number(tick.canvasWidth) || null;
      const canvasHeight = Number(tick.canvasHeight) || null;
      return { frameWidth, frameHeight, canvasWidth, canvasHeight };
    }

    function viewDims() {
      const dims = tickDims();
      if (state.scaleCanvas && dims.frameWidth && dims.frameHeight) {
        return { width: dims.frameWidth, height: dims.frameHeight, coordinateSpace: "framePixels" };
      }
      if (!state.scaleCanvas && dims.canvasWidth && dims.canvasHeight) {
        return { width: dims.canvasWidth, height: dims.canvasHeight, coordinateSpace: "canvasPixels" };
      }
      if (dims.frameWidth && dims.frameHeight) {
        return { width: dims.frameWidth, height: dims.frameHeight, coordinateSpace: "framePixels" };
      }
      if (dims.canvasWidth && dims.canvasHeight) {
        return { width: dims.canvasWidth, height: dims.canvasHeight, coordinateSpace: "canvasPixels" };
      }
      return { width: 800, height: 600, coordinateSpace: "unknown" };
    }

    function scalePair(x, y, sourceSpace) {
      const dims = tickDims();
      const desired = viewDims().coordinateSpace;
      if (!Number.isFinite(x) || !Number.isFinite(y) || sourceSpace === desired || desired === "unknown") {
        return { x, y };
      }
      if (sourceSpace === "canvasPixels" && desired === "framePixels" && dims.canvasWidth && dims.canvasHeight && dims.frameWidth && dims.frameHeight) {
        return { x: x * dims.frameWidth / dims.canvasWidth, y: y * dims.frameHeight / dims.canvasHeight };
      }
      if (sourceSpace === "framePixels" && desired === "canvasPixels" && dims.canvasWidth && dims.canvasHeight && dims.frameWidth && dims.frameHeight) {
        return { x: x * dims.canvasWidth / dims.frameWidth, y: y * dims.canvasHeight / dims.frameHeight };
      }
      return { x, y };
    }

    function scaleBounds(bounds, sourceSpace) {
      if (!bounds) return null;
      const start = scalePair(Number(bounds.x), Number(bounds.y), sourceSpace);
      const end = scalePair(Number(bounds.x) + Number(bounds.w), Number(bounds.y) + Number(bounds.h), sourceSpace);
      if (![start.x, start.y, end.x, end.y].every(Number.isFinite)) return null;
      return {
        x: Math.min(start.x, end.x),
        y: Math.min(start.y, end.y),
        w: Math.abs(end.x - start.x),
        h: Math.abs(end.y - start.y),
      };
    }

    function scalePoint(point, sourceSpace) {
      if (!point) return null;
      const scaled = scalePair(Number(point.x), Number(point.y), sourceSpace);
      return Number.isFinite(scaled.x) && Number.isFinite(scaled.y) ? scaled : null;
    }

    function scalePolygon(points, sourceSpace) {
      if (!Array.isArray(points)) return null;
      const scaled = [];
      for (const point of points) {
        if (!Array.isArray(point) || point.length < 2) return null;
        const converted = scalePair(Number(point[0]), Number(point[1]), sourceSpace);
        if (!Number.isFinite(converted.x) || !Number.isFinite(converted.y)) return null;
        scaled.push(`${converted.x},${converted.y}`);
      }
      return scaled.join(" ");
    }

    function svgElement(tag, attrs) {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attrs)) {
        if (value !== undefined && value !== null) node.setAttribute(key, value);
      }
      return node;
    }

    function modeAllows(mode, selected) {
      return mode === "all" || (mode === "selected" && selected);
    }

    function addShape(node, record) {
      const info = record._inspector || {};
      const selected = info.globalIndex === state.selectedGlobalIndex;
      node.classList.add("target-shape", info.sourceKind === "candidate" ? "candidate" : info.sourceKind === "ui" ? "ui" : "world");
      if (selected) node.classList.add("selected");
      if (!selected) node.style.opacity = String(Number(el.overlayOpacity.value) || 0.7);
      node.dataset.index = info.globalIndex;
      el.overlay.appendChild(node);
    }

    function addCandidateShape(node, record) {
      const info = record._inspector || {};
      const selected = info.globalIndex === state.selectedGlobalIndex;
      node.classList.add("target-shape", "candidate");
      if (selected) node.classList.add("selected");
      if (!selected) node.style.opacity = String(Number(el.overlayOpacity.value) || 0.8);
      node.dataset.index = info.globalIndex;
      el.overlay.appendChild(node);
    }

    function drawTarget(record) {
      const geometry = record.geometry || {};
      const target = record.target || {};
      const info = record._inspector || {};
      const sourceSpace = geometry.coordinateSpace || "framePixels";
      const drawn = [];
      const selected = info.globalIndex === state.selectedGlobalIndex;
      const showPolygons = modeAllows(el.showPolygons.value, selected);
      const showBounds = modeAllows(el.showBounds.value, selected);
      const showLabels = modeAllows(el.showLabels.value, selected);

      if (showPolygons) {
        for (const key of ["tilePolygon", "clickboxPolygon", "convexHullPolygon"]) {
          const points = scalePolygon(geometry[key], sourceSpace);
          if (points) {
            const polygon = svgElement("polygon", { points, fill: "none", "stroke-width": selected ? 3 : 2 });
            addShape(polygon, record);
            drawn.push(polygon);
          }
        }
      }

      if (showBounds) {
        for (const key of ["clickboxBounds", "convexHullBounds", "pixelBox"]) {
          const bounds = scaleBounds(geometry[key], sourceSpace);
          if (bounds && bounds.w > 0 && bounds.h > 0) {
            const rect = svgElement("rect", {
              x: bounds.x,
              y: bounds.y,
              width: bounds.w,
              height: bounds.h,
              fill: "none",
              "stroke-width": selected ? 3 : 2,
            });
            addShape(rect, record);
            drawn.push(rect);
          }
        }
      }

      for (const key of ["canvasPoint", "canvasLocation", "canvasCenter", "center"]) {
        const point = scalePoint(geometry[key], sourceSpace);
        if (point) {
          const group = svgElement("g", {});
          const radius = info.globalIndex === state.selectedGlobalIndex ? 5 : 4;
          const circle = svgElement("circle", { cx: point.x, cy: point.y, r: radius, class: "dot" });
          const lineH = svgElement("line", { x1: point.x - 8, y1: point.y, x2: point.x + 8, y2: point.y, "stroke-width": 1.5 });
          const lineV = svgElement("line", { x1: point.x, y1: point.y - 8, x2: point.x, y2: point.y + 8, "stroke-width": 1.5 });
          group.appendChild(lineH);
          group.appendChild(lineV);
          group.appendChild(circle);
          addShape(group, record);
          drawn.push(group);
          break;
        }
      }

      const anchor = firstGeometryAnchor(record);
      if (anchor && showLabels) {
        const label = svgElement("text", {
          x: anchor.x + 5,
          y: Math.max(12, anchor.y - 5),
          class: "label",
        });
        label.textContent = bestDisplayName(record);
        if (!selected) label.style.opacity = String(Number(el.overlayOpacity.value) || 0.7);
        el.overlay.appendChild(label);
      }

      return drawn.length > 0;
    }

    function drawCandidate(candidate) {
      const geometry = candidate.geometry || {};
      const info = candidate._inspector || {};
      const sourceSpace = geometry.coordinateSpace || "framePixels";
      const selected = info.globalIndex === state.selectedGlobalIndex;
      const drawn = [];

      if (el.showCandidateGeometry.checked) {
        const preferred = geometry.preferredAimGeometry;
        const kind = geometry.preferredAimGeometryType || "";

        if (Array.isArray(preferred)) {
          const points = scalePolygon(preferred, sourceSpace);
          if (points) {
            const polygon = svgElement("polygon", { points, fill: "none", "stroke-width": selected ? 4 : 2.5 });
            addCandidateShape(polygon, candidate);
            drawn.push(polygon);
          }
        } else if (preferred && typeof preferred === "object" && ["clickboxBounds", "convexHullBounds", "pixelBox", "aimBounds", "boundingBox", "bounds"].includes(kind)) {
          const bounds = scaleBounds(preferred, sourceSpace);
          if (bounds && bounds.w > 0 && bounds.h > 0) {
            const rect = svgElement("rect", {
              x: bounds.x,
              y: bounds.y,
              width: bounds.w,
              height: bounds.h,
              fill: "none",
              "stroke-width": selected ? 4 : 2.5,
            });
            addCandidateShape(rect, candidate);
            drawn.push(rect);
          }
        } else if (preferred && typeof preferred === "object") {
          const point = scalePoint(preferred, sourceSpace);
          if (point) {
            const circle = svgElement("circle", { cx: point.x, cy: point.y, r: selected ? 7 : 5, class: "dot" });
            addCandidateShape(circle, candidate);
            drawn.push(circle);
          }
        }
      }

      if (el.showCandidateAim.checked) {
        const point = scalePoint(geometry.aimPoint, sourceSpace);
        if (point) {
          const group = svgElement("g", {});
          const radius = selected ? 7 : 5;
          const circle = svgElement("circle", { cx: point.x, cy: point.y, r: radius, class: "dot" });
          const lineH = svgElement("line", { x1: point.x - 11, y1: point.y, x2: point.x + 11, y2: point.y, "stroke-width": selected ? 2.5 : 2 });
          const lineV = svgElement("line", { x1: point.x, y1: point.y - 11, x2: point.x, y2: point.y + 11, "stroke-width": selected ? 2.5 : 2 });
          group.appendChild(lineH);
          group.appendChild(lineV);
          group.appendChild(circle);
          addCandidateShape(group, candidate);
          drawn.push(group);
        }
      }

      const point = scalePoint(geometry.aimPoint, sourceSpace) || firstGeometryAnchor(candidate);
      const rank = Number(candidate.rank);
      const labelText = candidateLabel(candidate);
      const showLabel = labelText && point && (selected || (Number.isFinite(rank) && rank <= 20) || el.candidateLabelMode.value !== "rankScore");
      if (showLabel) {
        const label = svgElement("text", {
          x: point.x + 7,
          y: Math.max(12, point.y - 7),
          class: "label",
        });
        label.textContent = labelText;
        if (!selected) label.style.opacity = String(Number(el.overlayOpacity.value) || 0.8);
        el.overlay.appendChild(label);
      }

      return drawn.length > 0;
    }

    function firstGeometryAnchor(record) {
      const geometry = record.geometry || {};
      const sourceSpace = geometry.coordinateSpace || "framePixels";
      for (const key of ["aimPoint", "canvasPoint", "canvasLocation", "canvasCenter", "center"]) {
        const point = scalePoint(geometry[key], sourceSpace);
        if (point) return point;
      }
      for (const key of ["aimBounds", "clickboxBounds", "convexHullBounds", "pixelBox"]) {
        const bounds = scaleBounds(geometry[key], sourceSpace);
        if (bounds) return { x: bounds.x, y: bounds.y };
      }
      return null;
    }

    function limitedTargetsForDrawing() {
      const maxTargets = maxTargetsToDraw();
      const selected = state.targets.find((record) => (record._inspector || {}).globalIndex === state.selectedGlobalIndex);
      const unselected = state.targets.filter((record) => (record._inspector || {}).globalIndex !== state.selectedGlobalIndex);
      const limited = unselected.slice(0, maxTargets);
      state.drawLimit = maxTargets;
      state.hiddenByDrawLimit = Math.max(0, unselected.length - limited.length);

      if (selected) {
        limited.push(selected);
      }

      return limited;
    }

    function candidatesForDrawing() {
      const selected = state.candidates.find((record) => (record._inspector || {}).globalIndex === state.selectedGlobalIndex);
      const unselected = state.candidates.filter((record) => (record._inspector || {}).globalIndex !== state.selectedGlobalIndex);
      return selected ? [...unselected, selected] : unselected;
    }

    function renderFrame() {
      const tick = selectedTick();
      const dims = viewDims();
      el.overlay.setAttribute("viewBox", `0 0 ${dims.width} ${dims.height}`);
      el.stage.style.aspectRatio = `${dims.width} / ${dims.height}`;
      el.overlay.innerHTML = "";

      if (!tick) {
        el.status.textContent = "No ticks available.";
        el.frameImage.style.display = "none";
        el.blankFrame.style.display = "grid";
        return;
      }

      if (tick.frameExists) {
        el.blankFrame.style.display = "none";
        el.frameImage.style.display = "block";
        const newSrc = `/api/frame/${tick.tickId}`;
        if (!el.frameImage.src.endsWith(newSrc)) {
          el.frameImage.onload = () => {
            state.frameNatural = { width: el.frameImage.naturalWidth, height: el.frameImage.naturalHeight };
            renderFrame();
          };
          el.frameImage.src = newSrc;
        }
      } else {
        el.frameImage.removeAttribute("src");
        el.frameImage.style.display = "none";
        el.blankFrame.style.display = "grid";
        state.frameNatural = null;
      }

      let drawnCount = 0;
      for (const record of limitedTargetsForDrawing()) {
        if (drawTarget(record)) drawnCount += 1;
      }
      let drawnCandidateCount = 0;
      for (const candidate of candidatesForDrawing()) {
        if (drawCandidate(candidate)) drawnCandidateCount += 1;
      }

      el.status.textContent = `tick ${tick.tickId} - ${drawnCount} raw overlays / ${drawnCandidateCount} candidate overlays - view ${dims.coordinateSpace} ${Math.round(dims.width)}x${Math.round(dims.height)}`;
    }

    function renderDiagnostics() {
      const tick = selectedTick();
      const dims = tickDims();
      const notes = [];

      if (!tick) {
        notes.push("No targets match filters.");
      } else if (!tick.frameExists) {
        notes.push("Frame file missing or expired by retention. Geometry data is still available.");
      }

      if (state.targets.length === 0 && el.showRawTargets.checked) notes.push("No targets match filters.");
      if (state.candidates.length === 0 && el.showCandidates.checked) notes.push("No target candidates match. Try running select_target_candidates.py with matching filters.");

      if (state.hiddenByDrawLimit > 0) {
        notes.push(`Showing ${Math.min(state.drawLimit, state.targets.length)} of ${state.targets.length} targets. Use filters or increase max targets.`);
      }

      if (state.targets.some((record) => !(record._inspector || {}).geometryAvailable)) {
        notes.push("Some targets have missing geometry.");
      }

      const hasCanvasTargets = [...state.targets, ...state.candidates].some((record) => (record.geometry || {}).coordinateSpace === "canvasPixels");
      if (hasCanvasTargets && (!dims.canvasWidth || !dims.canvasHeight || !dims.frameWidth || !dims.frameHeight)) {
        notes.push("Cannot confidently scale canvas geometry to frame; showing raw coordinates where dimensions are missing.");
      }

      if (possibleCoordinateMismatch()) {
        notes.push("Possible coordinate-space mismatch: canvas and frame dimensions differ. Use the scaling toggle to compare.");
      }

      el.diagnostics.style.display = notes.length ? "block" : "none";
      el.diagnostics.innerHTML = notes.map(escapeHtml).join("<br>");
    }

    function possibleCoordinateMismatch() {
      const dims = tickDims();
      return Boolean(
        dims.canvasWidth && dims.canvasHeight && dims.frameWidth && dims.frameHeight &&
        (Math.abs(dims.canvasWidth - dims.frameWidth) > 1 || Math.abs(dims.canvasHeight - dims.frameHeight) > 1)
      );
    }

    function renderDetails() {
      const selected = [...state.targets, ...state.candidates].find((record) => (record._inspector || {}).globalIndex === state.selectedGlobalIndex);
      if (!selected) {
        el.targetDetails.textContent = "Select a target row or overlay shape.";
        return;
      }

      const info = selected._inspector || {};
      const target = selected.target || {};
      const geometry = selected.geometry || {};
      const scoring = selected.scoring || {};
      el.targetDetails.textContent = JSON.stringify({
        kind: info.sourceKind,
        displayName: bestDisplayName(selected),
        rank: selected.rank,
        score: selected.score,
        targetType: info.targetType,
        targetRole: info.targetRole,
        targetCategory: info.targetCategory,
        targetTags: info.targetTags,
        name: info.name,
        id: info.id,
        rawId: info.rawId,
        targetId: info.targetId,
        targetName: target.targetName,
        targetNameField: target.name,
        nameSource: target.nameSource,
        npcNameSource: target.npcNameSource,
        objectNameSource: target.objectNameSource,
        itemNameSource: target.itemNameSource,
        fallbackName: target.fallbackName,
        world: target.world,
        scene: target.scene,
        local: target.local,
        canvasPoint: geometry.canvasPoint,
        canvasLocation: geometry.canvasLocation,
        canvasCenter: geometry.canvasCenter,
        center: geometry.center,
        preferredAimGeometryType: geometry.preferredAimGeometryType,
        preferredAimGeometry: geometry.preferredAimGeometry,
        aimPoint: geometry.aimPoint,
        aimBounds: geometry.aimBounds,
        availableGeometryTypes: geometry.availableGeometryTypes,
        geometryQuality: geometry.geometryQuality,
        clickboxBounds: geometry.clickboxBounds,
        convexHullBounds: geometry.convexHullBounds,
        pixelBox: geometry.pixelBox,
        tilePolygon: geometry.tilePolygon,
        clickboxPolygon: geometry.clickboxPolygon,
        convexHullPolygon: geometry.convexHullPolygon,
        onScreen: info.onScreen,
        geometryAvailable: info.geometryAvailable,
        geometryWarning: geometry.geometryWarning,
        scoreParts: scoring.scoreParts,
        reasons: scoring.reasons,
        penalties: scoring.penalties,
        sourceTarget: selected.sourceTarget,
        record: selected,
      }, null, 2);
    }

    function selectTarget(globalIndex) {
      state.selectedGlobalIndex = Number(globalIndex);
      renderTargets();
      renderCandidates();
      renderFrame();
      renderDiagnostics();
      renderDetails();
    }

    function selectTick(tickId) {
      state.selectedTickId = Number(tickId);
      state.selectedGlobalIndex = null;
      loadTargets().catch((error) => {
        el.status.textContent = String(error);
      });
    }

    function moveTick(delta) {
      if (!state.ticks.length) return;
      const currentIndex = state.ticks.findIndex((tick) => tick.tickId === state.selectedTickId);
      const nextIndex = Math.max(0, Math.min(state.ticks.length - 1, currentIndex + delta));
      selectTick(state.ticks[nextIndex].tickId);
    }

    function applyQuickFilter(kind) {
      el.idFilter.value = "";
      el.tagFilter.value = "";
      el.idExact.checked = false;
      el.onScreenOnly.checked = false;
      el.geometryOnly.checked = false;
      el.targetRole.value = "";
      el.targetCategory.value = "";

      if (kind === "clear") {
        clearFilters();
        return;
      }

      if (kind === "npcs") {
        setTargetTypes(["npc"]);
        el.showUI.checked = false;
        el.nameFilter.value = "";
      } else if (kind === "sceneObjects") {
        setTargetTypes(["sceneObject"]);
        el.showUI.checked = false;
        el.nameFilter.value = "";
      } else if (kind === "groundItems") {
        setTargetTypes(["groundItem"]);
        el.showUI.checked = false;
        el.nameFilter.value = "";
      } else if (kind === "interactables") {
        setTargetTypes(["npc", "sceneObject", "groundItem"]);
        el.showUI.checked = false;
        el.targetRole.value = "interactable";
        el.nameFilter.value = "";
      } else if (kind === "obstacles") {
        setTargetTypes(["sceneObject", "tile"]);
        el.showUI.checked = false;
        el.targetRole.value = "obstacle";
        el.nameFilter.value = "";
      } else if (kind === "navigation") {
        setTargetTypes(["sceneObject", "tile"]);
        el.showUI.checked = false;
        el.tagFilter.value = "navigation_geometry";
        el.nameFilter.value = "";
      } else if (kind === "bank") {
        setTargetTypes(["npc", "sceneObject", "groundItem", "tile"]);
        el.showUI.checked = false;
        el.tagFilter.value = "bank";
        el.nameFilter.value = "";
      } else if (kind === "trees") {
        setTargetTypes(["sceneObject"]);
        el.showUI.checked = false;
        el.tagFilter.value = "tree";
        el.nameFilter.value = "";
      } else if (kind === "doors") {
        setTargetTypes(["sceneObject"]);
        el.showUI.checked = false;
        el.tagFilter.value = "door";
        el.nameFilter.value = "";
      }

      loadTargets();
    }

    function clearFilters() {
      setTargetTypes(["npc", "player", "sceneObject", "groundItem", "tile"]);
      el.targetRole.value = "";
      el.targetCategory.value = "";
      el.nameFilter.value = "";
      el.idFilter.value = "";
      el.tagFilter.value = "";
      el.idExact.checked = false;
      el.onScreenOnly.checked = false;
      el.geometryOnly.checked = false;
      el.showUI.checked = true;
      el.showRawTargets.checked = true;
      el.showCandidates.checked = Boolean(state.summary?.targetCandidatesExist && Number(state.summary?.targetCandidateCount || 0) > 0);
      el.frameExistsOnly.checked = false;
      loadTicks({ keepSelection: true });
    }

    async function init() {
      state.summary = await fetchJson("/api/summary");
      renderSummary(state.summary);
      await loadTicks();
    }

    el.tickRows.addEventListener("click", (event) => {
      const row = event.target.closest("[data-tick]");
      if (row) selectTick(row.dataset.tick);
    });
    el.targetRows.addEventListener("click", (event) => {
      const row = event.target.closest("[data-index]");
      if (row) selectTarget(row.dataset.index);
    });
    el.candidateRows.addEventListener("click", (event) => {
      const row = event.target.closest("[data-index]");
      if (row) selectTarget(row.dataset.index);
    });
    el.overlay.addEventListener("click", (event) => {
      const node = event.target.closest("[data-index]");
      if (node) selectTarget(node.dataset.index);
    });
    el.applyFilters.addEventListener("click", () => loadTargets());
    el.clearFilters.addEventListener("click", clearFilters);
    el.frameExistsOnly.addEventListener("change", () => loadTicks({ keepSelection: true }));
    el.scaleCanvas.addEventListener("change", () => {
      state.scaleCanvas = el.scaleCanvas.checked;
      renderFrame();
      renderDiagnostics();
    });
    for (const button of document.querySelectorAll("[data-quick-filter]")) {
      button.addEventListener("click", () => applyQuickFilter(button.dataset.quickFilter));
    }
    el.prevTick.addEventListener("click", () => moveTick(-1));
    el.nextTick.addEventListener("click", () => moveTick(1));
    el.jumpTickButton.addEventListener("click", () => {
      const tickId = Number(el.jumpTick.value);
      if (Number.isFinite(tickId)) selectTick(tickId);
    });
    el.jumpTick.addEventListener("keydown", (event) => {
      if (event.key === "Enter") el.jumpTickButton.click();
    });
    for (const input of [...el.targetTypeBoxes, el.targetRole, el.targetCategory, el.onScreenOnly, el.geometryOnly, el.showUI, el.showRawTargets, el.showCandidates, el.idExact]) {
      input.addEventListener("change", () => loadTargets());
    }
    el.nameFilter.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadTargets();
    });
    el.idFilter.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadTargets();
    });
    el.tagFilter.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadTargets();
    });
    for (const input of [el.showLabels, el.showPolygons, el.showBounds, el.overlayOpacity, el.maxTargets, el.showCandidateAim, el.showCandidateGeometry, el.candidateLabelMode]) {
      input.addEventListener("change", () => {
        renderFrame();
        renderDiagnostics();
      });
    }
    el.overlayOpacity.addEventListener("input", () => {
      renderFrame();
      renderDiagnostics();
    });

    init().catch((error) => {
      el.messages.innerHTML = `<div class="warning">${escapeHtml(error.message || error)}</div>`;
    });
  </script>
</body>
</html>
"""


class TargetGeometryHandler(BaseHTTPRequestHandler):
    dataset: GeometryDataset

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_html(html_page())
            return

        if path == "/api/summary":
            self.send_json(self.dataset.summary())
            return

        if path == "/api/ticks":
            self.handle_ticks(parsed)
            return

        if path == "/api/targets":
            self.handle_targets(parsed)
            return

        if path == "/api/candidates":
            self.handle_candidates(parsed)
            return

        if path.startswith("/api/frame/"):
            self.handle_frame(path)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def handle_ticks(self, parsed) -> None:
        params = parse_qs(parsed.query)
        frame_exists_only = parse_bool(first_param(params, "frameExistsOnly")) is True
        self.send_json({"ticks": self.dataset.ticks(frame_exists_only=frame_exists_only)})

    def handle_targets(self, parsed) -> None:
        params = parse_qs(parsed.query)
        tick_id = parse_tick(first_param(params, "tick"))

        if tick_id is None:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "tick query parameter is required")
            return

        filters = {
            "targetType": first_param(params, "targetType") or "all",
            "targetTypes": {
                value
                for value in (first_param(params, "targetTypes") or "").split(",")
                if value
            },
            "targetRoles": {
                value
                for value in (first_param(params, "targetRoles") or "").split(",")
                if value
            },
            "targetCategories": {
                value
                for value in (first_param(params, "targetCategories") or "").split(",")
                if value
            },
            "name": first_param(params, "name") or "",
            "tag": first_param(params, "tag") or "",
            "id": first_param(params, "id") or "",
            "idExact": parse_bool(first_param(params, "idExact")),
            "onScreen": parse_bool(first_param(params, "onScreen")),
            "geometryAvailable": parse_bool(first_param(params, "geometryAvailable")),
            "showUI": parse_bool(first_param(params, "showUI")),
            "showWorld": parse_bool(first_param(params, "showWorld")),
            "frameExists": parse_bool(first_param(params, "frameExists")),
        }
        targets = self.dataset.targets_for_tick(tick_id, filters)
        self.send_json({"tickId": tick_id, "targets": targets, "targetCount": len(targets)})

    def handle_candidates(self, parsed) -> None:
        params = parse_qs(parsed.query)
        tick_id = parse_tick(first_param(params, "tick"))

        if tick_id is None:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "tick query parameter is required")
            return

        limit = parse_tick(first_param(params, "limit"))

        filters = {
            "targetType": first_param(params, "targetType") or "all",
            "targetTypes": {
                value
                for value in (first_param(params, "targetTypes") or "").split(",")
                if value
            },
            "targetRoles": {
                value
                for value in (first_param(params, "targetRoles") or "").split(",")
                if value
            },
            "targetCategories": {
                value
                for value in (first_param(params, "targetCategories") or "").split(",")
                if value
            },
            "name": first_param(params, "name") or "",
            "tag": first_param(params, "tag") or "",
            "id": first_param(params, "id") or "",
            "idExact": parse_bool(first_param(params, "idExact")),
            "onScreen": parse_bool(first_param(params, "onScreen")),
            "geometryAvailable": parse_bool(first_param(params, "geometryAvailable")),
            "frameExists": parse_bool(first_param(params, "frameExists")),
            "limit": limit if isinstance(limit, int) and limit > 0 else 250,
        }
        candidates = self.dataset.candidates_for_tick(tick_id, filters)
        self.send_json({"tickId": tick_id, "candidates": candidates, "candidateCount": len(candidates)})

    def handle_frame(self, path: str) -> None:
        tick_text = path.rsplit("/", 1)[-1]
        tick_id = parse_tick(tick_text)

        if tick_id is None:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "tick id must be an integer")
            return

        frame_path = self.dataset.frame_path_for_tick(tick_id)

        if frame_path is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame path unavailable or outside selected session")
            return

        if not frame_path.exists() or not frame_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame file missing or expired by retention")
            return

        content_type = mimetypes.guess_type(str(frame_path))[0] or "application/octet-stream"

        try:
            data = frame_path.read_bytes()
        except OSError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame file could not be read")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status=status)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read-only local browser inspector for derived UI/world target geometry. "
            "It overlays existing geometry on retained frame images for QA only."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local HTTP port, default {DEFAULT_PORT}.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    dataset = GeometryDataset(session)
    dataset.load()
    TargetGeometryHandler.dataset = dataset
    server = ThreadingHTTPServer((HOST, args.port), TargetGeometryHandler)
    url = f"http://{HOST}:{args.port}/"

    print(f"Target geometry inspector: {url}")
    print(f"Session: {session if session else 'none'}")

    for message in dataset.messages:
        print(message)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping target geometry inspector.")
    finally:
        server.server_close()

    return 0


def frame_tick_from_path(path: Path) -> int | None:
    match = FRAME_TICK_RE.search(path.name)

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
