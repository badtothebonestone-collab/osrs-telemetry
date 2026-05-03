import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


SCHEMA_VERSION_INDEX = "visual_perception.index.v1"
SCHEMA_VERSION_TICK_RECORD = "visual_perception.tick_record.v1"
MISSING_PERCEPTION_MESSAGE = (
    "Required perception files not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)
CROP_GENERATION_REASON = "metadata-only mode; rerun with --generate-crops to attempt crops"
PILLOW_UNAVAILABLE_REASON = "Pillow not available"
NO_CROPS_CREATED_REASON = "no crops generated; selected frame files missing or region boxes unavailable"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def perception_paths(session: Path) -> dict[str, Path]:
    perception_dir = session / "perception"
    return {
        "perceptionDir": perception_dir,
        "tickBundles": perception_dir / "tick_bundles.jsonl",
        "screenRegions": perception_dir / "screen_regions.json",
        "visualIndex": perception_dir / "visual_perception_index.json",
        "visualTickRecords": perception_dir / "visual_tick_records.jsonl",
    }


def session_relative(session: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except (OSError, ValueError):
        return str(path)


def safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in ("-", "_") else "_" for character in value)
    return cleaned.strip("_") or "region"


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def signal_filters(args) -> list[str]:
    filters = []

    if args.combat:
        filters.append("hasCombatSignal")

    if args.inventory:
        filters.append("hasInventorySignal")

    if args.ui:
        filters.append("hasUiSignal")

    if args.frame_issues:
        filters.append("hasFrameIssue")

    return filters


def selected_by_tick_args(bundle: dict, args) -> bool:
    tick_id = bundle.get("tickId")

    if args.tick is not None:
        return tick_id == args.tick

    if args.tick_range is not None:
        start, end = args.tick_range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def selected_by_signal_filters(bundle: dict, filters: list[str]) -> bool:
    if not filters:
        return True

    derived = bundle.get("derived")

    if not isinstance(derived, dict):
        return False

    return any(bool(derived.get(key)) for key in filters)


def iter_jsonl_records(path: Path):
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed {path.name} at line {line_number}: {error}") from error

            if isinstance(record, dict):
                yield record


def numeric_value(value):
    return value if isinstance(value, (int, float)) else None


def int_count(value) -> int:
    return value if isinstance(value, int) else 0


def format_pair(values: dict | None) -> str:
    if not isinstance(values, dict):
        return "?/?"

    current = values.get("boosted")
    maximum = values.get("real")
    return f"{current if current is not None else '?'}/{maximum if maximum is not None else '?'}"


def format_position(position: dict | None) -> str:
    if not isinstance(position, dict):
        return "?,?,?"

    return ",".join(
        str(position.get(key) if position.get(key) is not None else "?")
        for key in ("worldX", "worldY", "plane")
    )


def event_preview(events: dict | None, *, limit: int = 8) -> str:
    if not isinstance(events, dict):
        return "none"

    event_types = events.get("onTickEventTypes")

    if not isinstance(event_types, list) or not event_types:
        return "none"

    labels = [str(event_type) for event_type in event_types if event_type is not None]

    if len(labels) <= limit:
        return ", ".join(labels)

    return ", ".join(labels[:limit]) + f", +{len(labels) - limit}"


def pixel_box(normalized_box: dict, width, height) -> dict | None:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None

    if not isinstance(normalized_box, dict):
        return None

    try:
        normalized_x = float(normalized_box["x"])
        normalized_y = float(normalized_box["y"])
        normalized_w = float(normalized_box["w"])
        normalized_h = float(normalized_box["h"])
    except (KeyError, TypeError, ValueError):
        return None

    x = max(0, min(width, round(normalized_x * width)))
    y = max(0, min(height, round(normalized_y * height)))
    w = max(0, min(width - x, round(normalized_w * width)))
    h = max(0, min(height - y, round(normalized_h * height)))

    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }


def region_records(regions: dict, frame: dict) -> tuple[list[dict], bool]:
    width = frame.get("width")
    height = frame.get("height")
    records = []
    missing_pixel_boxes = False

    for name, normalized_box in regions.items():
        calculated_box = pixel_box(normalized_box, width, height)

        if calculated_box is None:
            missing_pixel_boxes = True

        records.append(
            {
                "name": name,
                "normalizedBox": normalized_box if isinstance(normalized_box, dict) else None,
                "pixelBox": calculated_box,
                "cropPath": None,
                "exists": False,
            }
        )

    return records, missing_pixel_boxes


def crop_box(pixel: dict) -> tuple[int, int, int, int] | None:
    if not isinstance(pixel, dict):
        return None

    x = pixel.get("x")
    y = pixel.get("y")
    w = pixel.get("w")
    h = pixel.get("h")

    if not all(isinstance(value, int) for value in (x, y, w, h)):
        return None

    if w <= 0 or h <= 0:
        return None

    return x, y, x + w, y + h


def generate_region_crops(
    session: Path,
    bundle_frame: dict,
    visual_regions: list[dict],
    tick_id,
    temp_crops_dir: Path,
    image_module,
) -> tuple[int, list[str]]:
    warnings = []

    if not isinstance(tick_id, int):
        return 0, ["cannot generate crops for record without integer tickId"]

    if bundle_frame.get("exists") is not True:
        return 0, [f"tick {tick_id} frame file missing; crops skipped"]

    absolute_frame_path = bundle_frame.get("absolutePath")
    frame_path = Path(absolute_frame_path) if absolute_frame_path else None

    if frame_path is None or not frame_path.exists():
        return 0, [f"tick {tick_id} frame file not found; crops skipped"]

    try:
        frame_path.resolve().relative_to(session.resolve())
    except (OSError, ValueError):
        return 0, [f"tick {tick_id} frame path escapes session; crops skipped"]

    crop_dir = temp_crops_dir / f"tick-{tick_id:08d}"
    published_crop_dir = Path("perception") / "crops" / f"tick-{tick_id:08d}"
    crop_dir.mkdir(parents=True, exist_ok=True)
    crop_count = 0

    try:
        with image_module.open(frame_path) as image:
            for region in visual_regions:
                box = crop_box(region.get("pixelBox"))

                if box is None:
                    warnings.append(f"tick {tick_id} region {region.get('name')} has no valid pixel box; crop skipped")
                    continue

                region_name = safe_name(str(region.get("name") or "region"))
                crop_path = crop_dir / f"{region_name}.jpg"
                image.crop(box).save(crop_path, "JPEG")
                region["cropPath"] = str(published_crop_dir / crop_path.name)
                region["exists"] = True
                crop_count += 1
    except OSError as error:
        return crop_count, [f"tick {tick_id} unable to crop frame: {error}"]

    return crop_count, warnings


def state_summary(state: dict) -> dict:
    position = state.get("position") if isinstance(state.get("position"), dict) else {}

    return {
        "gameState": state.get("gameState"),
        "position": {
            "worldX": position.get("worldX"),
            "worldY": position.get("worldY"),
            "plane": position.get("plane"),
        },
        "hp": state.get("hp") if isinstance(state.get("hp"), dict) else {},
        "prayer": state.get("prayer") if isinstance(state.get("prayer"), dict) else {},
        "runEnergyPercent": state.get("runEnergyPercent"),
        "interacting": state.get("interacting"),
        "inventoryCount": int_count(state.get("inventoryCount")),
        "equipmentCount": int_count(state.get("equipmentCount")),
        "npcCount": int_count(state.get("npcCount")),
        "playerCount": int_count(state.get("playerCount")),
    }


def signal_payload(derived: dict) -> dict:
    return {
        "hasCombatSignal": bool(derived.get("hasCombatSignal")),
        "hasInventorySignal": bool(derived.get("hasInventorySignal")),
        "hasUiSignal": bool(derived.get("hasUiSignal")),
        "hasVarSignal": bool(derived.get("hasVarSignal")),
        "hasFrameIssue": bool(derived.get("hasFrameIssue")),
        "hasCaptureError": bool(derived.get("hasCaptureError")),
    }


def prompt_context(bundle: dict, frame: dict, summary: dict, events: dict) -> str:
    tick_id = bundle.get("tickId")
    position = format_position(summary.get("position"))
    hp = format_pair(summary.get("hp"))
    prayer = format_pair(summary.get("prayer"))
    run = summary.get("runEnergyPercent")
    run_text = f"{run}%" if run is not None else "?%"
    inventory = summary.get("inventoryCount")
    event_text = event_preview(events)
    status = frame.get("frameIndexStatus") or frame.get("status") or "unknown"
    latency = frame.get("totalLatencyMs")
    latency_text = f"{latency:.0f}ms" if isinstance(latency, (int, float)) else "unknown"

    return (
        f"Tick {tick_id}, {summary.get('gameState') or 'UNKNOWN'} at {position}. "
        f"HP {hp}, prayer {prayer}, run {run_text}. "
        f"Inventory {inventory}/28. "
        f"Events: {event_text}. "
        f"Frame status {status}, latency {latency_text}."
    )


def recommended_review(signals: dict) -> dict:
    reasons = []

    if signals["hasFrameIssue"]:
        reasons.append("frameIssue")

    if signals["hasCaptureError"]:
        reasons.append("captureError")

    if signals["hasCombatSignal"]:
        reasons.append("combatSignal")

    if signals["hasInventorySignal"]:
        reasons.append("inventorySignal")

    if signals["hasUiSignal"]:
        reasons.append("uiSignal")

    if signals["hasVarSignal"]:
        reasons.append("varSignal")

    if signals["hasFrameIssue"] or signals["hasCaptureError"]:
        priority = "HIGH"
    elif signals["hasCombatSignal"] or signals["hasInventorySignal"] or signals["hasUiSignal"]:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    return {
        "priority": priority,
        "reasons": reasons,
    }


def build_visual_record(
    session: Path,
    bundle: dict,
    regions: dict,
    *,
    crop_enabled: bool = False,
    temp_crops_dir: Path | None = None,
    image_module=None,
) -> tuple[dict, list[str], int]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    state = bundle.get("state") if isinstance(bundle.get("state"), dict) else {}
    events = bundle.get("events") if isinstance(bundle.get("events"), dict) else {}
    derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}
    summary = state_summary(state)
    signals = signal_payload(derived)
    regions_payload, missing_pixel_boxes = region_records(regions, frame)
    warnings = []

    if missing_pixel_boxes:
        warnings.append(f"tick {bundle.get('tickId')} missing frame width/height for pixel boxes")

    crop_count = 0

    if crop_enabled and temp_crops_dir is not None and image_module is not None:
        crop_count, crop_warnings = generate_region_crops(
            session,
            frame,
            regions_payload,
            bundle.get("tickId"),
            temp_crops_dir,
            image_module,
        )
        warnings.extend(crop_warnings)

    return (
        {
            "schemaVersion": SCHEMA_VERSION_TICK_RECORD,
            "sessionId": bundle.get("sessionId"),
            "tickId": bundle.get("tickId"),
            "timestampUtc": bundle.get("timestampUtc"),
            "frame": {
                "path": frame.get("path"),
                "exists": frame.get("exists"),
                "width": frame.get("width"),
                "height": frame.get("height"),
                "status": frame.get("frameIndexStatus") or frame.get("captureStatus"),
                "captureSource": frame.get("captureSource"),
                "writeDelayMs": numeric_value(frame.get("writeDelayMs")),
                "totalLatencyMs": numeric_value(frame.get("totalLatencyMs")),
            },
            "stateSummary": summary,
            "signals": signals,
            "regions": regions_payload,
            "promptContext": prompt_context(bundle, frame, summary, events),
            "recommendedReview": recommended_review(signals),
        },
        warnings,
        crop_count,
    )


def write_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        file.write(json_dump_compact(data))
        file.write("\n")


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json_dump_compact(record))
            file.write("\n")


def atomic_publish(perception_dir: Path, temp_dir: Path, filenames: tuple[str, ...], *, publish_crops: bool = False) -> None:
    perception_dir.mkdir(parents=True, exist_ok=True)

    for filename in filenames:
        os.replace(temp_dir / filename, perception_dir / filename)

    crop_root = perception_dir / "crops"
    temp_crop_root = temp_dir / "crops"

    if crop_root.exists():
        shutil.rmtree(crop_root)

    if publish_crops and temp_crop_root.exists():
        shutil.move(str(temp_crop_root), str(crop_root))


def load_pillow_image():
    try:
        from PIL import Image
    except ImportError:
        return None

    return Image


def build_visual_perception(session: Path, args) -> dict:
    paths = perception_paths(session)
    perception_dir = paths["perceptionDir"]
    tick_bundle_path = paths["tickBundles"]
    screen_region_path = paths["screenRegions"]
    warnings = []

    if not tick_bundle_path.exists() or not screen_region_path.exists():
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)

    screen_regions = safe_read_json(screen_region_path)

    if not isinstance(screen_regions, dict):
        raise ValueError(f"Unable to read screen regions: {screen_region_path}")

    regions = screen_regions.get("regions")

    if not isinstance(regions, dict):
        raise ValueError(f"screen_regions.json does not contain a regions object: {screen_region_path}")

    filters = signal_filters(args)
    records = []
    signal_counts = Counter()
    review_priority_counts = Counter()
    selected_tick_ids = []
    missing_pixel_box_ticks = 0
    crop_count = 0
    limit = None if args.tick is not None else args.limit
    image_module = None
    crop_generation_reason = CROP_GENERATION_REASON

    if args.generate_crops:
        image_module = load_pillow_image()

        if image_module is None:
            crop_generation_reason = PILLOW_UNAVAILABLE_REASON
            warnings.append(PILLOW_UNAVAILABLE_REASON)

    temp_dir = perception_dir / f".tmp-visual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"
    temp_crops_dir = temp_dir / "crops"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    if limit is None or limit > 0:
        for bundle in iter_jsonl_records(tick_bundle_path):
            if not selected_by_tick_args(bundle, args):
                continue

            if not selected_by_signal_filters(bundle, filters):
                continue

            visual_record, record_warnings, record_crop_count = build_visual_record(
                session,
                bundle,
                regions,
                crop_enabled=bool(args.generate_crops and image_module is not None),
                temp_crops_dir=temp_crops_dir,
                image_module=image_module,
            )
            records.append(visual_record)
            warnings.extend(record_warnings)
            selected_tick_ids.append(visual_record.get("tickId"))
            crop_count += record_crop_count

            if any("missing frame width/height" in warning for warning in record_warnings):
                missing_pixel_box_ticks += 1

            signals = visual_record["signals"]

            for key, value in signals.items():
                if value:
                    signal_counts[key] += 1

            review_priority_counts[visual_record["recommendedReview"]["priority"]] += 1

            if limit is not None and len(records) >= limit:
                break

            if args.tick is not None:
                break

    if missing_pixel_box_ticks:
        warnings.append(
            f"pixel boxes unavailable for {missing_pixel_box_ticks} selected ticks because frame width/height was missing"
        )

    if args.generate_crops and image_module is not None:
        if crop_count:
            crop_generation_reason = None
        else:
            crop_generation_reason = NO_CROPS_CREATED_REASON

    valid_tick_ids = [tick_id for tick_id in selected_tick_ids if isinstance(tick_id, int)]
    output_paths = {
        "visualPerceptionIndex": "perception/visual_perception_index.json",
        "visualTickRecords": "perception/visual_tick_records.jsonl",
        "crops": "perception/crops",
    }
    filters_used = {
        "tick": args.tick,
        "range": list(args.tick_range) if args.tick_range else None,
        "limit": args.limit,
        "signals": filters,
        "signalCombination": "OR",
    }
    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "sessionPath": str(session),
        "sessionId": records[0].get("sessionId") if records else None,
        "generatedAtUtc": utc_now(),
        "selectedTickCount": len(records),
        "firstTickId": valid_tick_ids[0] if valid_tick_ids else None,
        "lastTickId": valid_tick_ids[-1] if valid_tick_ids else None,
        "cropsGenerated": bool(crop_count),
        "cropGenerationReason": crop_generation_reason,
        "cropCount": crop_count,
        "regionNames": list(regions.keys()),
        "signalCounts": {
            key: signal_counts.get(key, 0)
            for key in (
                "hasCombatSignal",
                "hasInventorySignal",
                "hasUiSignal",
                "hasVarSignal",
                "hasFrameIssue",
                "hasCaptureError",
            )
        },
        "reviewPriorityCounts": {
            key: review_priority_counts.get(key, 0)
            for key in ("LOW", "MEDIUM", "HIGH")
        },
        "frameIssueCount": signal_counts.get("hasFrameIssue", 0),
        "captureErrorCount": signal_counts.get("hasCaptureError", 0),
        "filters": filters_used,
        "paths": output_paths,
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    filenames = ("visual_perception_index.json", "visual_tick_records.jsonl")

    try:
        write_json(temp_dir / "visual_perception_index.json", index)
        write_jsonl(temp_dir / "visual_tick_records.jsonl", records)
        atomic_publish(perception_dir, temp_dir, filenames, publish_crops=bool(crop_count))
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prepare visual perception records from derived OSRS telemetry bundles.",
        epilog=(
            "Signal filters such as --combat, --ui, and --frame-issues combine with OR. "
            "--tick or --range first constrain the tick set, then signal filters apply."
        ),
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum selected visual records to write after filters.")
    parser.add_argument("--tick", type=parse_tick_id, help="Select one tick.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument("--combat", action="store_true", help="Select ticks with hasCombatSignal.")
    parser.add_argument("--inventory", action="store_true", help="Select ticks with hasInventorySignal.")
    parser.add_argument("--ui", action="store_true", help="Select ticks with hasUiSignal.")
    parser.add_argument("--frame-issues", action="store_true", help="Select ticks with hasFrameIssue.")
    parser.add_argument("--generate-crops", action="store_true", help="Attempt to generate region crops if Pillow is already available.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    try:
        index = build_visual_perception(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(error)
        return 1
    except (OSError, ValueError) as error:
        print(f"Unable to prepare visual perception records: {error}")
        return 1

    print(f"Prepared visual perception metadata: {index['sessionPath']}")
    print(f"  perception/visual_perception_index.json")
    print(f"  perception/visual_tick_records.jsonl ({index['selectedTickCount']} rows)")
    print(f"  cropsGenerated: {index['cropsGenerated']}")
    print(f"  cropGenerationReason: {index['cropGenerationReason']}")
    print(f"  frameIssueCount: {index['frameIssueCount']}")
    print(f"  captureErrorCount: {index['captureErrorCount']}")
    print(f"  warningCount: {index['warningCount']}")

    if index["warnings"]:
        print("  warnings:")

        for warning in index["warnings"][:10]:
            print(f"    - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
