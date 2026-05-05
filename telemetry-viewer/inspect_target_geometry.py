import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


MISSING_WORLD_MESSAGE = "Run python telemetry-viewer\\build_world_target_geometry.py first."
MISSING_UI_MESSAGE = "Run python telemetry-viewer\\build_ui_target_geometry.py first."
TARGET_TYPES = {
    "npc",
    "player",
    "sceneObject",
    "groundItem",
    "tile",
    "inventorySlot",
    "equipmentSlot",
    "prayerIcon",
    "magicSpell",
    "baseUiRegion",
    "all",
}
TARGET_ROLES = {
    "interactable",
    "obstacle",
    "navigation",
    "decoration",
    "entity",
    "item",
    "ui",
    "unknown",
}


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


def compact_counts(counter: Counter, limit: int | None = None) -> dict:
    return {str(key): count for key, count in counter.most_common(limit)}


def target_for(record: dict) -> dict:
    value = record.get("target")
    return value if isinstance(value, dict) else {}


def geometry_for(record: dict) -> dict:
    value = record.get("geometry")
    return value if isinstance(value, dict) else {}


def frame_for(record: dict) -> dict:
    value = record.get("frame")
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

    for key in ("id", "rawId", "targetId"):
        value = target.get(key)

        if value is not None:
            return value

    return None


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

    source_kind = record.get("_sourceKind")

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

    source_kind = record.get("_sourceKind")

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

    if record.get("_sourceKind") == "ui":
        return ["ui"]

    return []


def geometry_available_for(record: dict) -> bool:
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
    geometry = geometry_for(record)
    value = geometry.get("onScreen")

    if isinstance(value, bool):
        return value

    return None


def point_summary(record: dict) -> str:
    geometry = geometry_for(record)

    for key in ("canvasPoint", "canvasLocation", "canvasCenter", "center"):
        point = geometry.get(key)

        if isinstance(point, dict):
            x = point.get("x")
            y = point.get("y")

            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return f"{key}={x:.0f},{y:.0f}"

    return ""


def bounds_summary(record: dict) -> str:
    geometry = geometry_for(record)

    for key in ("clickboxBounds", "convexHullBounds", "pixelBox"):
        bounds = geometry.get(key)

        if isinstance(bounds, dict):
            x = bounds.get("x")
            y = bounds.get("y")
            w = bounds.get("w")
            h = bounds.get("h")

            if all(isinstance(value, (int, float)) for value in (x, y, w, h)):
                return f"{key}={x:.0f},{y:.0f} {w:.0f}x{h:.0f}"

    for key in ("tilePolygon", "clickboxPolygon", "convexHullPolygon"):
        polygon = geometry.get(key)

        if isinstance(polygon, list) and polygon:
            return f"{key}={len(polygon)}pts"

    return ""


def position_summary(record: dict) -> str:
    target = target_for(record)
    parts = []

    for label, key in (("world", "world"), ("local", "local")):
        value = target.get(key)

        if not isinstance(value, dict):
            continue

        if label == "world":
            x = value.get("x")
            y = value.get("y")
            plane = value.get("plane")

            if x is not None or y is not None or plane is not None:
                parts.append(f"world={x},{y},{plane}")
        else:
            x = value.get("x")
            y = value.get("y")

            if x is not None or y is not None:
                parts.append(f"local={x},{y}")

    return " ".join(parts)


def frame_path_text(record: dict) -> str | None:
    path = frame_for(record).get("path")
    return str(path) if path else None


def bool_text(value: bool | None) -> str:
    if value is True:
        return "true"

    if value is False:
        return "false"

    return "unknown"


class TargetGeometryDataset:
    def __init__(self, session: Path | None):
        self.session = session
        self.geometry_dir = session / "interaction_geometry" if session else None
        self.world_targets_path = self.geometry_dir / "world_targets.jsonl" if self.geometry_dir else None
        self.world_index_path = self.geometry_dir / "world_geometry_index.json" if self.geometry_dir else None
        self.ui_targets_path = self.geometry_dir / "ui_targets.jsonl" if self.geometry_dir else None
        self.ui_index_path = self.geometry_dir / "ui_geometry_index.json" if self.geometry_dir else None
        self.world_index = {}
        self.ui_index = {}
        self.world_records = []
        self.ui_records = []
        self.records = []
        self.records_by_tick = defaultdict(list)
        self.messages = []
        self.warnings = []

    def load(self) -> None:
        if self.session is None:
            self.messages.append("No telemetry session found.")
            return

        self.world_index = self._read_index(self.world_index_path)
        self.ui_index = self._read_index(self.ui_index_path)

        self.world_records, world_warnings = (
            read_jsonl(self.world_targets_path) if self.world_targets_path else ([], [])
        )
        self.ui_records, ui_warnings = read_jsonl(self.ui_targets_path) if self.ui_targets_path else ([], [])
        self.warnings.extend(world_warnings)
        self.warnings.extend(ui_warnings)

        if not (self.world_targets_path and self.world_targets_path.exists()):
            self.messages.append(MISSING_WORLD_MESSAGE)

        if not (self.ui_targets_path and self.ui_targets_path.exists()):
            self.messages.append(MISSING_UI_MESSAGE)

        for source_kind, source_records in (("world", self.world_records), ("ui", self.ui_records)):
            for source_index, record in enumerate(source_records):
                tick_id = record.get("tickId")

                if not isinstance(tick_id, int):
                    continue

                decorated = dict(record)
                decorated["_sourceKind"] = source_kind
                decorated["_sourceIndex"] = source_index
                self.records.append(decorated)
                self.records_by_tick[tick_id].append(decorated)

    def _read_index(self, path: Path | None) -> dict:
        value = safe_read_json(path) if path else None
        return value if isinstance(value, dict) else {}

    def resolve_session_path(self, value: str | None) -> Path | None:
        if self.session is None or not value:
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

    def frame_exists_for_record(self, record: dict) -> bool | None:
        path = frame_path_text(record)

        if not path:
            return None

        resolved = self.resolve_session_path(path)
        return bool(resolved and resolved.exists() and resolved.is_file())

    def ticks(self) -> list[int]:
        return sorted(self.records_by_tick)

    def selected_ticks(self, args) -> set[int] | None:
        if args.tick is not None:
            return {args.tick}

        if args.tick_range is not None:
            start, end = args.tick_range
            return {tick for tick in self.ticks() if start <= tick <= end}

        if args.latest is not None:
            return set(self.ticks()[-args.latest :])

        return None

    def matching_records(self, args) -> list[dict]:
        selected_ticks = self.selected_ticks(args)
        matches = []

        for record in self.records:
            tick_id = record.get("tickId")

            if selected_ticks is not None and tick_id not in selected_ticks:
                continue

            if args.target_type != "all" and target_type_for(record) != args.target_type:
                continue

            if args.role and target_role_for(record).lower() != args.role.lower():
                continue

            if args.category and target_category_for(record).lower() != args.category.lower():
                continue

            if args.tag:
                needle = args.tag.lower()
                tags = [tag.lower() for tag in target_tags_for(record)]

                if needle not in tags:
                    continue

            if args.name:
                target = target_for(record)
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        target_name_for(record),
                        target.get("name"),
                        target.get("targetName"),
                        target.get("kind"),
                        target.get("regionName"),
                        target_type_for(record),
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

                if args.name.lower() not in haystack:
                    continue

            if args.id:
                needle = str(args.id).lower()
                ids = [value.lower() for value in target_id_values(record)]

                if not any(needle in value for value in ids):
                    continue

            if args.only_on_screen and on_screen_for(record) is not True:
                continue

            if args.geometry_available and not geometry_available_for(record):
                continue

            matches.append(record)

        return matches[: args.limit]

    def summary(self) -> dict:
        target_type_counts = Counter()
        target_role_counts = Counter()
        target_category_counts = Counter()
        target_tag_counts = Counter()
        on_screen_counts = Counter()
        geometry_available_counts = Counter()

        for record in self.records:
            target_type_counts[target_type_for(record)] += 1
            target_role_counts[target_role_for(record)] += 1
            target_category_counts[target_category_for(record)] += 1

            for tag in target_tags_for(record):
                target_tag_counts[tag] += 1

            on_screen_counts[bool_text(on_screen_for(record))] += 1
            geometry_available_counts[str(bool(geometry_available_for(record))).lower()] += 1

        ticks = self.ticks()
        return {
            "sessionPath": str(self.session) if self.session else None,
            "worldTargetCount": len(self.world_records),
            "uiTargetCount": len(self.ui_records),
            "totalTargetCount": len(self.records),
            "countsByTargetType": compact_counts(target_type_counts),
            "countsByTargetRole": compact_counts(target_role_counts),
            "countsByTargetCategory": compact_counts(target_category_counts),
            "topTargetTags": compact_counts(target_tag_counts, 25),
            "countsByOnScreen": compact_counts(on_screen_counts),
            "countsByGeometryAvailable": compact_counts(geometry_available_counts),
            "firstTickId": ticks[0] if ticks else None,
            "lastTickId": ticks[-1] if ticks else None,
            "tickCount": len(ticks),
            "retainedFrameOverlap": {
                "world": retained_frame_overlap(self.world_index),
                "ui": retained_frame_overlap(self.ui_index),
            },
            "indexes": {
                "world": compact_index_fields(self.world_index),
                "ui": compact_index_fields(self.ui_index),
            },
            "messages": self.messages,
            "warnings": self.warnings,
        }


def retained_frame_overlap(index: dict) -> bool | None:
    if not index:
        return None

    selected = index.get("selectedFrameTickCount")

    if isinstance(selected, int):
        return selected > 0

    selected_range = index.get("selectedFrameTickRange")

    if isinstance(selected_range, list) and selected_range:
        return True

    return None


def compact_index_fields(index: dict) -> dict:
    if not index:
        return {}

    return {
        "selectedBy": index.get("selectedBy"),
        "selectedTickRange": index.get("selectedTickRange"),
        "retainedFrameTickRange": index.get("retainedFrameTickRange"),
        "selectedFrameTickCount": index.get("selectedFrameTickCount"),
        "selectedFrameTickRange": index.get("selectedFrameTickRange"),
        "targetRecordCount": index.get("targetRecordCount"),
        "warnings": index.get("warnings", [])[:5],
    }


def print_counts(title: str, counts: dict) -> None:
    print(f"{title}:")

    if counts:
        for key, value in counts.items():
            print(f"  {key}: {value}")
    else:
        print("  none")


def print_summary(summary: dict) -> None:
    print(f"session: {summary.get('sessionPath') or 'none'}")
    print(f"world target count: {summary.get('worldTargetCount', 0)}")
    print(f"UI target count: {summary.get('uiTargetCount', 0)}")
    print(f"total target count: {summary.get('totalTargetCount', 0)}")
    print(f"tick range: {summary.get('firstTickId')}-{summary.get('lastTickId')}")
    print(f"tick count: {summary.get('tickCount', 0)}")
    print_counts("counts by targetType", summary.get("countsByTargetType") or {})
    print_counts("counts by role", summary.get("countsByTargetRole") or {})
    print_counts("counts by category", summary.get("countsByTargetCategory") or {})
    print_counts("top tags", summary.get("topTargetTags") or {})
    print_counts("onScreen counts", summary.get("countsByOnScreen") or {})
    print_counts("geometryAvailable counts", summary.get("countsByGeometryAvailable") or {})
    overlap = summary.get("retainedFrameOverlap") or {}
    print(f"retained frame overlap: world={overlap.get('world')} ui={overlap.get('ui')}")

    for message in summary.get("messages") or []:
        print(f"message: {message}")

    for warning in summary.get("warnings") or []:
        print(f"warning: {warning}")


def compact_record_line(record: dict) -> str:
    target = target_for(record)
    frame_path = frame_path_text(record)
    frame_exists = frame_for(record).get("exists")

    if frame_exists is None:
        frame_exists = "unknown"

    pieces = [
        f"tick={record.get('tickId')}",
        f"type={target_type_for(record)}",
        f"name={target_name_for(record) or '-'}",
    ]
    target_id = target_id_for(record)

    if target_id is not None:
        pieces.append(f"id={target_id}")

    pieces.extend(
        [
            f"role={target_role_for(record)}",
            f"category={target_category_for(record)}",
            f"tags={','.join(target_tags_for(record)) or '-'}",
            f"onScreen={bool_text(on_screen_for(record))}",
            f"geometryAvailable={str(bool(geometry_available_for(record))).lower()}",
        ]
    )
    point = point_summary(record)
    bounds = bounds_summary(record)
    position = position_summary(record)

    if point:
        pieces.append(point)

    if bounds:
        pieces.append(bounds)

    if position:
        pieces.append(position)

    if frame_path:
        pieces.append(f"frameExists={frame_exists}")
        pieces.append(f"frame={frame_path}")

    source_kind = record.get("_sourceKind")

    if source_kind:
        pieces.append(f"source={source_kind}")

    return " ".join(pieces)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Inspect derived world/UI target geometry from existing JSONL outputs. "
            "This is read-only and does not generate actions."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--summary", action="store_true", help="Print target geometry summary counts.")
    parser.add_argument("--tick", type=int, help="Only show records for one tick.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--latest", type=int, metavar="N", help="Only show records from the latest N target ticks.")
    parser.add_argument("--target-type", choices=sorted(TARGET_TYPES), default="all", help="Target type filter.")
    parser.add_argument("--role", choices=sorted(TARGET_ROLES), help="Target role filter.")
    parser.add_argument("--category", help="Case-insensitive exact target category filter.")
    parser.add_argument("--tag", help="Exact target tag filter.")
    parser.add_argument("--name", help="Case-insensitive text filter against name/type/role/category/tags/id fields.")
    parser.add_argument("--id", help="Text filter against id/rawId/targetId.")
    parser.add_argument("--only-on-screen", action="store_true", help="Only show targets with onScreen=true.")
    parser.add_argument("--geometry-available", action="store_true", help="Only show targets with usable geometry.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum compact rows or JSON lines to print. Default: 50.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    if args.tick is not None and args.tick_range is not None:
        parser.error("--tick cannot be combined with --range")

    if args.tick is not None and args.latest is not None:
        parser.error("--tick cannot be combined with --latest")

    if args.tick_range is not None and args.latest is not None:
        parser.error("--range cannot be combined with --latest")

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    if args.latest is not None and args.latest < 1:
        parser.error("--latest must be positive")

    if args.limit < 1:
        parser.error("--limit must be positive")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    dataset = TargetGeometryDataset(session)
    dataset.load()

    if args.summary:
        summary = dataset.summary()

        if args.json:
            print(json.dumps(summary, separators=(",", ":")))
        else:
            print_summary(summary)

        return 0

    diagnostic_output = sys.stderr if args.json else sys.stdout

    for message in dataset.messages:
        print(message, file=diagnostic_output)

    for warning in dataset.warnings:
        print(f"warning: {warning}", file=diagnostic_output)

    matches = dataset.matching_records(args)

    if args.json:
        for record in matches:
            cleaned = dict(record)
            cleaned.pop("_sourceKind", None)
            cleaned.pop("_sourceIndex", None)
            print(json.dumps(cleaned, separators=(",", ":")))
    else:
        if not matches:
            print("No targets matched.")

        for record in matches:
            print(compact_record_line(record))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
