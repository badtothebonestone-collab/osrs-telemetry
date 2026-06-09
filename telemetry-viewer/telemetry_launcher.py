import os
import queue
import subprocess
import sys
import threading
import json
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BooleanVar, Label, StringVar, Tk, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


sys.path.insert(0, str(Path(__file__).resolve().parent))

from telemetry_paths import (  # noqa: E402
    DEFAULT_SESSIONS_DIR,
    SESSIONS_DIR_ENV,
    directory_size,
    find_newest_session,
    frame_index_stats,
    list_event_files,
    list_tick_files,
    load_frame_index_summaries,
    resolve_frame_path,
    safe_read_json,
    session_mtime,
    session_size_mb,
    tick_age_seconds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_URL = "http://127.0.0.1:8765/"
CALIBRATION_URL = "http://127.0.0.1:8770/"
TRAINING_INSPECTOR_URL = "http://127.0.0.1:8790/"
TARGET_GEOMETRY_INSPECTOR_URL = "http://127.0.0.1:8800/"
CALIBRATION_PROFILE_DIR = Path(__file__).resolve().parent / "calibration_profiles"
TAB_LABELS_PATH = Path(__file__).resolve().parent / "tab_labels.json"
REPLAY_LABELING_HELP_PATH = PROJECT_ROOT / "docs" / "analysis_examples.md"
STOP_GRACE_MS = 2500
MAX_LOG_LINES = 5000
HEALTH_REFRESH_MS = 5000
COLLECTION_POLL_MS = 2000
FRESH_TICK_SECONDS = 10
SESSION_AWARE_PROCESS_KEYS = {
    "replay",
    "calibration",
    "perception",
    "visual_perception",
    "test_crops",
    "training",
    "training_focused_ui",
    "training_rebuild",
    "dataset_status",
    "training_inspector",
    "curated_export",
    "curated_export_splits",
    "world_geometry",
    "target_candidates",
    "target_coverage",
    "target_override_suggestions",
    "target_geometry_inspector",
    "live_setup_check",
    "compact_packet_inspector",
    "live_processor",
}


@dataclass(frozen=True)
class ProcessSpec:
    key: str
    name: str
    command: list[str]
    long_running: bool


PROCESS_SPECS = {
    # Legacy dev launcher. Do not use this as the canonical Start Game resolver.
    "runelite": ProcessSpec(
        "runelite",
        "RuneLite Dev Client",
        ["cmd", "/c", ".\\gradlew.bat", "--no-daemon", "run"],
        True,
    ),
    "viewer": ProcessSpec(
        "viewer",
        "Text Viewer",
        ["python", "telemetry-viewer\\viewer.py"],
        True,
    ),
    "latest": ProcessSpec(
        "latest",
        "Latest State Watcher",
        ["python", "telemetry-viewer\\latest_state.py"],
        True,
    ),
    "replay": ProcessSpec(
        "replay",
        "Replay Viewer",
        ["python", "telemetry-viewer\\replay_viewer.py"],
        True,
    ),
    "calibration": ProcessSpec(
        "calibration",
        "Screen Calibration UI",
        [
            "python",
            "telemetry-viewer\\calibrate_screen_regions.py",
            "--interactive",
            "--latest-existing-frame",
            "--port",
            "8770",
        ],
        True,
    ),
    "validate": ProcessSpec(
        "validate",
        "Validate Session",
        ["python", "telemetry-viewer\\validate_session.py"],
        False,
    ),
    "export": ProcessSpec(
        "export",
        "Export Session",
        ["python", "telemetry-viewer\\export_session.py"],
        False,
    ),
    "perception": ProcessSpec(
        "perception",
        "Build Perception Dataset",
        ["python", "telemetry-viewer\\build_perception_dataset.py"],
        False,
    ),
    "visual_perception": ProcessSpec(
        "visual_perception",
        "Prepare Visual Perception",
        ["python", "telemetry-viewer\\prepare_visual_perception.py"],
        False,
    ),
    "test_crops": ProcessSpec(
        "test_crops",
        "Generate Test Crops",
        [
            "python",
            "telemetry-viewer\\prepare_visual_perception.py",
            "--generate-crops",
            "--generate-grid-slots",
            "--latest",
            "25",
            "--only-existing-frames",
            "--active-tab",
            "inventory",
        ],
        False,
    ),
    "training": ProcessSpec(
        "training",
        "Build Training Dataset Review Preset",
        [
            "python",
            "telemetry-viewer\\build_training_dataset.py",
            "--preset",
            "review",
            "--latest",
            "500",
            "--generate-grid-slots",
        ],
        False,
    ),
    "training_focused_ui": ProcessSpec(
        "training_focused_ui",
        "Build Training Dataset Focused UI",
        [
            "python",
            "telemetry-viewer\\build_training_dataset.py",
            "--preset",
            "focused-ui",
            "--latest",
            "500",
            "--generate-grid-slots",
        ],
        False,
    ),
    "training_rebuild": ProcessSpec(
        "training_rebuild",
        "Build Training Dataset Rebuild",
        [
            "python",
            "telemetry-viewer\\build_training_dataset.py",
            "--preset",
            "review",
            "--latest",
            "500",
            "--generate-grid-slots",
            "--rebuild",
        ],
        False,
    ),
    "dataset_status": ProcessSpec(
        "dataset_status",
        "Dataset Status",
        ["python", "telemetry-viewer\\dataset_status.py"],
        False,
    ),
    "training_inspector": ProcessSpec(
        "training_inspector",
        "Training Dataset Inspector",
        ["python", "telemetry-viewer\\training_dataset_inspector.py", "--port", "8790"],
        True,
    ),
    "curated_export": ProcessSpec(
        "curated_export",
        "Export Curated Dataset",
        ["python", "telemetry-viewer\\export_curated_training_dataset.py"],
        False,
    ),
    "curated_export_splits": ProcessSpec(
        "curated_export_splits",
        "Export Curated Dataset With Splits",
        [
            "python",
            "telemetry-viewer\\export_curated_training_dataset.py",
            "--split",
            "train,val,test",
            "--seed",
            "123",
        ],
        False,
    ),
    "tests": ProcessSpec(
        "tests",
        "Path Regression Tests",
        ["python", "telemetry-viewer\\tests\\test_telemetry_paths.py"],
        False,
    ),
    "world_geometry": ProcessSpec(
        "world_geometry",
        "Build World Target Geometry",
        ["python", "telemetry-viewer\\build_world_target_geometry.py", "--target-type", "all"],
        False,
    ),
    "target_candidates": ProcessSpec(
        "target_candidates",
        "Select Target Candidates",
        ["python", "telemetry-viewer\\select_target_candidates.py", "--target-type", "all", "--limit", "500", "--summary"],
        False,
    ),
    "target_coverage": ProcessSpec(
        "target_coverage",
        "Target Coverage Diagnostic",
        [
            "python",
            "telemetry-viewer\\diagnose_target_coverage.py",
            "--latest",
            "25",
            "--project-root",
            str(PROJECT_ROOT),
        ],
        False,
    ),
    "target_override_suggestions": ProcessSpec(
        "target_override_suggestions",
        "Suggest Target Overrides",
        ["python", "telemetry-viewer\\suggest_target_overrides.py", "--limit", "25"],
        False,
    ),
    "target_geometry_inspector": ProcessSpec(
        "target_geometry_inspector",
        "Target Geometry Inspector",
        ["python", "telemetry-viewer\\target_geometry_inspector.py", "--port", "8800"],
        True,
    ),
    "live_setup_check": ProcessSpec(
        "live_setup_check",
        "Check Live Setup",
        ["python", "telemetry-viewer\\check_live_setup.py"],
        False,
    ),
    "compact_packet_inspector": ProcessSpec(
        "compact_packet_inspector",
        "Legacy Packet Cleanup Report",
        ["python", "telemetry-viewer\\maintenance.py", "--live-packets-report"],
        False,
    ),
    "live_processor": ProcessSpec(
        "live_processor",
        "Live Target Processor",
        [
            "python",
            "telemetry-viewer\\live_target_processor.py",
            "--input-source",
            "plugin-snapshot",
            "--profile",
            "woodcutting",
            "--follow",
            "--latency-mode",
            "realtime",
            "--liveness-mode",
            "delta",
            "--liveness-budget-ms",
            "20",
            "--no-startup-backfill",
            "--max-new-ticks-per-update",
            "1",
            "--candidate-output-window",
            "latest",
            "--window-ticks",
            "10",
            "--limit",
            "100",
            "--no-ui-targets",
            "--emit-world-targets",
            "candidates",
            "--drain-backlog-on-overrun",
            "--summary",
            "--benchmark",
        ],
        True,
    ),
}


def format_bool(value) -> str:
    if value is True:
        return "true"

    if value is False:
        return "false"

    return "unknown"


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"

    return f"{seconds:.1f}s"


def format_mb(value: float | None) -> str:
    if value is None:
        return "-"

    return f"{value:.2f}"


def format_ms(value: float | int | None) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"

    return f"{value:.0f} ms"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    return parsed.astimezone(timezone.utc)


def latest_numeric_summary_value(summaries: list[dict], key: str) -> float | int | None:
    for summary in reversed(summaries):
        value = summary.get(key)

        if isinstance(value, (int, float)):
            return value

    return None


def read_latest_jsonl_record_with_source(files: list[Path]) -> tuple[Path | None, dict | None]:
    for file_path in reversed(files):
        record = read_last_json_line(file_path)

        if record is not None:
            return file_path, record

    return None, None


def read_latest_jsonl_record(files: list[Path]) -> dict | None:
    return read_latest_jsonl_record_with_source(files)[1]


def read_last_json_line(file_path: Path) -> dict | None:
    try:
        if not file_path.exists() or file_path.stat().st_size == 0:
            return None

        with file_path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            position = file.tell()
            buffer = b""

            while position > 0:
                chunk_size = min(8192, position)
                position -= chunk_size
                file.seek(position)
                buffer = file.read(chunk_size) + buffer
                lines = [line.strip() for line in buffer.splitlines() if line.strip()]

                for line in reversed(lines):
                    try:
                        record = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue

                    if isinstance(record, dict):
                        return record

                if len(lines) > 1:
                    break
    except OSError:
        return None

    return None


def count_frame_files(session: Path) -> int:
    frames = session / "frames"

    if not frames.exists():
        return 0

    try:
        return sum(1 for path in frames.iterdir() if path.is_file())
    except OSError:
        return 0


def newest_file_in_dir(directory: Path) -> Path | None:
    if not directory.exists():
        return None

    try:
        files = [path for path in directory.iterdir() if path.is_file()]
    except OSError:
        return None

    if not files:
        return None

    return max(files, key=lambda path: path.stat().st_mtime)


def newest_directory_in_dir(directory: Path) -> Path | None:
    if not directory.exists():
        return None

    try:
        directories = [path for path in directory.iterdir() if path.is_dir()]
    except OSError:
        return None

    if not directories:
        return None

    try:
        return max(directories, key=lambda path: path.stat().st_mtime)
    except OSError:
        return None


def list_session_directories(sessions_dir: Path) -> list[Path]:
    if not sessions_dir.exists():
        return []

    try:
        return [path.resolve() for path in sessions_dir.iterdir() if path.is_dir()]
    except OSError:
        return []


def path_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def newest_mtime_utc(paths: list[Path | None]) -> datetime | None:
    mtimes = [path_mtime_utc(path) for path in paths if path is not None and path.exists()]
    mtimes = [mtime for mtime in mtimes if mtime is not None]

    if not mtimes:
        return None

    return max(mtimes)


def count_jsonl_records(file_path: Path) -> int:
    if not file_path.exists():
        return 0

    count = 0

    try:
        with file_path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    count += 1
    except OSError:
        return 0

    return count


def first_numeric_index_value(index: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = index.get(key)

        if isinstance(value, (int, float)):
            return int(value)

    return None


def latest_existing_frame_file(session: Path) -> Path | None:
    frames_dir = session / "frames"

    if not frames_dir.exists():
        return None

    try:
        frame_files = [path for path in frames_dir.glob("frame-tick-*.jpg") if path.is_file()]
    except OSError:
        return None

    if not frame_files:
        return None

    try:
        return max(frame_files, key=lambda path: path.stat().st_mtime)
    except OSError:
        return None


def tick_id_from_frame_file(path: Path | None) -> int | None:
    if path is None:
        return None

    prefix = "frame-tick-"

    if not path.stem.startswith(prefix):
        return None

    try:
        return int(path.stem[len(prefix):])
    except ValueError:
        return None


def compute_warning_count(values: dict) -> int:
    count = 0

    if values.get("calibration_profile_exists") == "no":
        count += 1

    if values.get("label_file_exists") == "no":
        count += 1

    if values.get("perception_bundle_count") in {"not built", "unknown"}:
        count += 1

    if values.get("training_example_count") in {"not built", "0", "unknown"}:
        count += 1

    if values.get("curated_example_count") in {"not built", "0", "unknown"}:
        count += 1

    missing_crop_count = str(values.get("missing_crop_count") or "")

    if missing_crop_count not in {"", "not built", "0 manifest, 0 skipped"}:
        count += 1

    capture_errors = str(values.get("capture_errors") or "")

    if capture_errors not in {"", "-", "0"}:
        count += 1

    return count


def collect_health(
    sessions_dir: Path,
    validation_result: str,
    active_session: Path | None = None,
) -> dict:
    values = {
        "active_session_locked": "yes" if active_session is not None else "no",
        "active_session_path": str(active_session) if active_session is not None else "not locked",
        "newest_session": "-",
        "active": "unknown",
        "tick_id": "-",
        "tick_age": "-",
        "game_state": "-",
        "position": "-",
        "resources": "-",
        "tick_files": "0",
        "event_files": "0",
        "frame_files": "0",
        "frames_size": "-",
        "frame_write_delay": "unavailable",
        "frame_total_latency": "unavailable",
        "frame_index_status": "unavailable",
        "frame_written_count": "0",
        "frame_dropped_count": "0",
        "frame_deleted_count": "0",
        "perception_exists": "no",
        "perception_bundle_count": "not built",
        "visual_perception_record_count": "not built",
        "label_file_exists": "no",
        "calibration_profile_exists": "no",
        "session_screen_regions": "unknown",
        "latest_existing_frame_tick": "unavailable",
        "visual_crop_count": "not built",
        "training_data_exists": "no",
        "training_example_count": "not built",
        "curated_manifest_exists": "no",
        "curated_example_count": "not built",
        "missing_crop_count": "not built",
        "calibration_server_running": "no",
        "inspector_server_running": "no",
        "session_size": "-",
        "capture_errors": "-",
        "validation": validation_result,
    }
    paths = {
        "session": None,
        "latest_frame": None,
        "latest_status": None,
        "manifest": None,
        "newest_tick_segment": None,
        "newest_event_segment": None,
        "perception": None,
        "training_data": None,
        "curated": None,
        "calibration_profiles": str(CALIBRATION_PROFILE_DIR),
    }

    values["label_file_exists"] = "yes" if TAB_LABELS_PATH.exists() else "no"
    values["calibration_profile_exists"] = "yes" if (
        CALIBRATION_PROFILE_DIR / "default_screen_regions.json"
    ).exists() else "no"

    if not sessions_dir.exists():
        return health_result("stale", f"Sessions dir missing: {sessions_dir}", values, paths)

    if active_session is not None:
        session = active_session.expanduser()

        if not session.exists():
            return health_result("stale", f"Active session missing: {session}", values, paths)
    else:
        session = find_newest_session(sessions_dir)

    if session is None:
        return health_result("stale", f"No sessions found in {sessions_dir}", values, paths)

    manifest = safe_read_json(session / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else None
    active = manifest.get("active") if manifest else None
    tick_files = list_tick_files(session)
    event_files = list_event_files(session)
    frame_index_summaries = load_frame_index_summaries(session)
    latest_tick = read_latest_jsonl_record(tick_files)
    latest_frame = None
    latest_existing_frame = latest_existing_frame_file(session)
    latest_existing_frame_tick = tick_id_from_frame_file(latest_existing_frame)

    if latest_existing_frame_tick is not None:
        values["latest_existing_frame_tick"] = str(latest_existing_frame_tick)

    if frame_index_summaries:
        frame_stats = frame_index_stats(frame_index_summaries)
        latest_frame_index = frame_stats.get("latestEvent") or {}
        frame_index_status = latest_frame_index.get("status") or latest_frame_index.get("eventType")
        values["frame_write_delay"] = format_ms(
            latest_numeric_summary_value(frame_index_summaries, "frameWriteDelayMs")
        )
        values["frame_total_latency"] = format_ms(
            latest_numeric_summary_value(frame_index_summaries, "frameTotalLatencyMs")
        )
        values["frame_index_status"] = str(frame_index_status or "unknown")
        values["frame_written_count"] = str(frame_stats["FrameWritten"])
        values["frame_dropped_count"] = str(frame_stats["FrameDropped"])
        values["frame_deleted_count"] = str(frame_stats["FrameDeleted"])

    if latest_tick:
        latest_frame = resolve_frame_path(session, latest_tick.get("framePath"))

        if latest_frame is not None and not latest_frame.exists():
            latest_frame = None

    if latest_frame is None:
        latest_frame = latest_existing_frame or newest_file_in_dir(session / "frames")

    values["newest_session"] = str(session)
    values["active"] = format_bool(active)
    values["recording_mode"] = str((manifest or {}).get("recordingMode") or "unknown")
    values["raw_tick_recording"] = format_bool((manifest or {}).get("rawTickRecordingEnabled"))
    values["frame_recording"] = format_bool((manifest or {}).get("frameRecordingEnabled"))
    values["compact_packets"] = "yes" if packet_index else "no"
    values["compact_latest_tick"] = str((packet_index or {}).get("latestTick", "-"))
    values["tick_files"] = str(len(tick_files))
    values["event_files"] = str(len(event_files))
    values["frame_files"] = str(count_frame_files(session))
    values["frames_size"] = format_mb(directory_size(session / "frames") / (1024 * 1024))
    values["session_size"] = format_mb(session_size_mb(session))
    values["session_screen_regions"] = "yes" if (
        session / "perception" / "screen_regions.json"
    ).exists() else "no"

    perception_dir = session / "perception"
    values["perception_exists"] = "yes" if (perception_dir / "tick_bundles.jsonl").exists() else "no"

    perception_index = safe_read_json(perception_dir / "perception_index.json")

    if isinstance(perception_index, dict):
        bundle_count = perception_index.get("tickBundleCount")
        values["perception_bundle_count"] = (
            str(bundle_count) if bundle_count is not None else "unknown"
        )

    visual_perception_index = safe_read_json(perception_dir / "visual_perception_index.json")

    if isinstance(visual_perception_index, dict):
        record_count = (
            visual_perception_index.get("selectedTickCount")
            or visual_perception_index.get("visualRecordCount")
        )
        values["visual_perception_record_count"] = (
            str(record_count) if record_count is not None else "unknown"
        )
        crop_count = visual_perception_index.get("cropCount")
        values["visual_crop_count"] = str(crop_count) if crop_count is not None else "unknown"

    training_dir = session / "training_data"
    values["training_data_exists"] = "yes" if training_dir.exists() else "no"
    training_index = safe_read_json(training_dir / "training_index.json")
    training_manifest = training_dir / "training_manifest.jsonl"

    if isinstance(training_index, dict):
        training_examples = first_numeric_index_value(
            training_index,
            ("manifestExampleCount", "exampleCount", "selectedTickCount"),
        )
        crop_missing = first_numeric_index_value(
            training_index,
            ("cropMissingExampleCount",),
        )
        skipped_missing = first_numeric_index_value(
            training_index,
            ("skippedMissingCropCount",),
        )

        values["training_example_count"] = (
            str(training_examples) if training_examples is not None else "unknown"
        )

        if crop_missing is not None or skipped_missing is not None:
            values["missing_crop_count"] = (
                f"{crop_missing or 0} manifest, {skipped_missing or 0} skipped"
            )
    elif training_manifest.exists():
        values["training_example_count"] = str(count_jsonl_records(training_manifest))

    curated_dir = training_dir / "curated"
    curated_index = safe_read_json(curated_dir / "curated_index.json")
    curated_manifest = curated_dir / "curated_manifest.jsonl"
    values["curated_manifest_exists"] = "yes" if curated_manifest.exists() else "no"

    if isinstance(curated_index, dict):
        curated_examples = first_numeric_index_value(
            curated_index,
            ("selectedCuratedCount", "curatedExampleCount", "exampleCount"),
        )
        values["curated_example_count"] = (
            str(curated_examples) if curated_examples is not None else "unknown"
        )
    elif curated_manifest.exists():
        values["curated_example_count"] = str(count_jsonl_records(curated_manifest))

    paths["session"] = str(session)
    paths["latest_frame"] = str(latest_frame) if latest_frame else None
    paths["latest_status"] = str(session / "latest" / "latest_status.json")
    paths["manifest"] = str(session / "manifest.json")
    paths["legacy_live_packet_index"] = str(packet_index_path)
    paths["newest_tick_segment"] = str(tick_files[-1]) if tick_files else None
    paths["newest_event_segment"] = str(event_files[-1]) if event_files else None
    paths["perception"] = str(session / "perception")
    paths["training_data"] = str(training_dir)
    paths["curated"] = str(curated_dir)

    if latest_tick is None:
        packet_mtime = path_mtime_utc(packet_index_path)
        if packet_index and packet_mtime is not None:
            age = (utc_now() - packet_mtime).total_seconds()
            values["legacy_live_packet_age"] = format_age(age)
            if age < FRESH_TICK_SECONDS and active is True:
                return health_result("warning", "Legacy live packet files are present but ignored by current runtime", values, paths)
            if age < FRESH_TICK_SECONDS * 3:
                return health_result("warning", "Legacy live packet files are present but not runtime truth", values, paths)
        return health_result("stale", "No raw ticks found. In normal live mode use PluginSnapshotEndpoint/WorldModel, not packet files.", values, paths)

    local_player = latest_tick.get("localPlayer") or {}
    status = latest_tick.get("status") or {}
    tick_age = tick_age_seconds(latest_tick)
    capture_errors = latest_tick.get("captureErrors") or []
    hp = value_pair(status.get("hitpointsBoosted"), status.get("hitpointsReal"))
    prayer = value_pair(status.get("prayerBoosted"), status.get("prayerReal"))
    run = status.get("runEnergyPercent")

    values["tick_id"] = str(latest_tick.get("tickId", "-"))
    values["tick_age"] = format_age(tick_age)
    values["game_state"] = str(latest_tick.get("gameState", "-"))
    values["position"] = ",".join(
        str(value if value is not None else "?")
        for value in (
            local_player.get("worldX"),
            local_player.get("worldY"),
            local_player.get("plane"),
        )
    )
    values["resources"] = f"hp={hp} prayer={prayer} run={run if run is not None else '?'}"
    values["capture_errors"] = str(len(capture_errors))

    if tick_age is None:
        return health_result("warning", "Latest tick timestamp unavailable", values, paths)

    if tick_age < 10 and active is True:
        return health_result("ok", "Active session is fresh", values, paths)

    if tick_age <= 60:
        return health_result("warning", "Latest tick is not fresh", values, paths)

    return health_result("stale", "Latest tick is stale", values, paths)


def value_pair(current, maximum) -> str:
    return f"{current if current is not None else '?'}/{maximum if maximum is not None else '?'}"


def health_result(status: str, message: str, values: dict, paths: dict) -> dict:
    values["warning_count"] = str(compute_warning_count(values))
    colors = {
        "ok": ("#e8f5e9", "#1b5e20"),
        "warning": ("#fff8e1", "#5d4300"),
        "stale": ("#ffebee", "#7f1d1d"),
    }
    background, foreground = colors.get(status, ("#eeeeee", "#202124"))

    return {
        "status": status,
        "message": message,
        "values": values,
        "paths": paths,
        "background": background,
        "foreground": foreground,
    }


class ManagedProcess:
    def __init__(self, spec: ProcessSpec):
        self.spec = spec
        self.process: subprocess.Popen | None = None
        self.started_command: list[str] | None = None
        self.started_at: datetime | None = None
        self.reader_thread: threading.Thread | None = None
        self.stop_requested = False
        self.starting = False
        self.exit_code: int | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def pid(self) -> str:
        if self.process is not None and self.is_running():
            return str(self.process.pid)
        return ""

    def started_display(self) -> str:
        if self.started_at is None:
            return ""
        return self.started_at.strftime("%H:%M:%S")

    def status(self) -> str:
        if self.is_running():
            return "starting" if self.starting else "running"

        if self.process is not None:
            code = self.process.poll()

            if code is not None:
                self.exit_code = code
                return f"exited {code}"

        if self.exit_code is not None:
            return f"exited {self.exit_code}"

        return "stopped"

    def status_tag(self) -> str:
        status = self.status()

        if status == "running":
            return "running"

        if status == "starting":
            return "starting"

        if status.startswith("exited") and not status.endswith(" 0"):
            return "exited_error"

        return "stopped"


class LauncherApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("OSRS Telemetry Control Center")
        self.geometry("1120x760")
        self.minsize(920, 620)

        configured_sessions_dir = os.environ.get(SESSIONS_DIR_ENV)
        self.sessions_dir_var = StringVar(value=configured_sessions_dir or str(DEFAULT_SESSIONS_DIR))
        self.active_session_path: Path | None = None
        self.active_session_path_var = StringVar(value="not locked")
        self.collection_status_var = StringVar(value="Idle")
        self.collection_start_utc: datetime | None = None
        self.collection_known_sessions: set[Path] = set()
        self.collection_waiting = False
        self.show_advanced_var = BooleanVar(value=False)
        self.auto_open_replay_var = BooleanVar(value=False)
        self.auto_start_latest_var = BooleanVar(value=True)
        self.auto_open_calibration_var = BooleanVar(value=False)
        self.auto_scroll_var = BooleanVar(value=True)
        self.health_log_var = BooleanVar(value=False)
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.control_queue: queue.Queue[tuple[str, str, int]] = queue.Queue()
        self.health_queue: queue.Queue[dict] = queue.Queue()
        self.managed = {key: ManagedProcess(spec) for key, spec in PROCESS_SPECS.items()}
        self.start_buttons: dict[str, ttk.Button] = {}
        self.health_values: dict[str, StringVar] = {}
        self.health_paths: dict[str, str | None] = {}
        self.health_refresh_running = False
        self.last_validation_result = "unknown"
        self.log_line_count = 0
        self.pipeline_name: str | None = None
        self.pipeline_queue: list[str] = []
        self.pipeline_current_key: str | None = None
        self.pipeline_session: Path | None = None

        self._build_ui()
        self._refresh_status_table()
        self.after(100, self._drain_output_queue)
        self.after(150, self._drain_control_queue)
        self.after(250, self._drain_health_queue)
        self.after(500, self._poll_processes)
        self.after(500, self.refresh_health)
        self.after(HEALTH_REFRESH_MS, self._auto_refresh_health)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

        config_frame = ttk.LabelFrame(self, text="Configuration")
        config_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="Sessions dir").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(config_frame, textvariable=self.sessions_dir_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ttk.Label(config_frame, text="Active session").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(config_frame, textvariable=self.active_session_path_var).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=8,
            pady=(0, 4),
        )
        ttk.Label(config_frame, text="Collection status").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(config_frame, textvariable=self.collection_status_var).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=8,
            pady=(0, 4),
        )
        options_frame = ttk.Frame(config_frame)
        options_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 4))
        ttk.Checkbutton(
            options_frame,
            text="Auto-open Replay after fresh session locked",
            variable=self.auto_open_replay_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            options_frame,
            text="Auto-start Latest State Watcher after fresh session locked",
            variable=self.auto_start_latest_var,
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            options_frame,
            text="Auto-open Calibration after fresh session locked",
            variable=self.auto_open_calibration_var,
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(
            config_frame,
            text=(
                "Current Happy Path: 1. Start Collection  2. Calibrate if needed, then Save Default Profile  "
                "3. Replay / label tick ranges  4. Build Dataset  5. Inspect Dataset  "
                "6. Export Curated  7. Run Doctor / Status\n"
                "Target Geometry QA: collect raw session, then build world target geometry, select target candidates, "
                "run target coverage diagnostic, and open the target geometry inspector.\n"
                "Live QA default input: PluginSnapshotEndpoint/WorldModel. Legacy packet archives are cleanup-only."
            ),
            justify="left",
            wraplength=980,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        button_frame = ttk.Frame(self)
        button_frame.grid(row=1, column=0, rowspan=3, sticky="ns", padx=(10, 6), pady=6)
        button_frame.columnconfigure(0, weight=1)

        self._build_simple_controls(button_frame)
        ttk.Checkbutton(
            button_frame,
            text="Show Advanced",
            variable=self.show_advanced_var,
            command=self._toggle_advanced,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        self._build_advanced_controls(button_frame)
        self.advanced_frame.grid_remove()

        self._build_health_panel()
        self._build_status_panel()
        self._build_log_panel()

    def _build_simple_controls(self, parent):
        simple_frame = ttk.LabelFrame(parent, text="Simple Workflow")
        simple_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        simple_frame.columnconfigure(0, weight=1)

        simple_actions = [
            (
                "Start Collection",
                "Launch RuneLite and wait for a fresh active session.",
                self.start_collection,
            ),
            (
                "Stop Collection",
                "Stop launcher-started collection processes.",
                self.stop_collection,
            ),
            (
                "Open Replay / Label",
                "Review ticks, frames, events, and label tab ranges.",
                self.open_replay_label_workflow,
            ),
            (
                "Open Calibration",
                "Edit screen regions and tab profiles.",
                self.open_calibration_workflow,
            ),
            (
                "Build Dataset",
                "Build perception + training data using current labels/calibration.",
                self.build_dataset_pipeline,
            ),
            (
                "Inspect Dataset",
                "Review generated crops and mark good/bad examples.",
                self.inspect_dataset_workflow,
            ),
            (
                "Export Curated",
                "Create clean curated_manifest.jsonl with train/val/test split.",
                self.export_curated_pipeline,
            ),
            (
                "Run Doctor / Status",
                "Check profiles, labels, sessions, training data, and curated output.",
                self.run_doctor_status,
            ),
        ]

        for row, (label, description, command) in enumerate(simple_actions):
            self._add_workflow_button(simple_frame, row, label, description, command)

    def _add_workflow_button(self, parent, row: int, label: str, description: str, command):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        frame.columnconfigure(1, weight=1)
        ttk.Button(frame, text=label, command=command, width=28).grid(row=0, column=0, sticky="ew")
        ttk.Label(frame, text=description, wraplength=240, justify="left").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )

    def _build_advanced_controls(self, parent):
        self.advanced_frame = ttk.LabelFrame(parent, text="Advanced")
        self.advanced_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.advanced_frame.columnconfigure(0, weight=1)
        self.advanced_frame.columnconfigure(1, weight=1)
        self._button_group_index = 0

        self._add_button_group(
            self.advanced_frame,
            "Collection / Live Tools",
            [
                ("Start RuneLite Dev Client", lambda: self.start_process("runelite"), "runelite"),
                ("Start Latest State Watcher", lambda: self.start_process("latest"), "latest"),
                ("Start Text Viewer", lambda: self.start_process("viewer"), "viewer"),
                ("Check Live Setup", self.check_live_setup, "live_setup_check"),
                ("Inspect Compact Packets", self.inspect_compact_packets, "compact_packet_inspector"),
                ("Start Live Processor", self.start_live_processor, "live_processor"),
                ("Unlock Active Session / Use Newest Session", self.unlock_active_session, None),
                ("Set Active Session To Newest Fresh Session", self.set_active_session_to_newest_fresh_session, None),
            ],
        )
        self._add_button_group(
            self.advanced_frame,
            "Replay / Calibration",
            [
                ("Start Replay Viewer", lambda: self.start_process("replay"), "replay"),
                ("Open Replay Viewer", self.open_replay_viewer, None),
                ("Open Labels File", self.open_labels_file, None),
                ("Start Calibration Mode", self.open_calibration_workflow, None),
                ("Generate Test Crops", lambda: self.start_process("test_crops"), "test_crops"),
                ("Open Latest Test Crops", self.open_latest_test_crops, None),
            ],
        )
        self._add_button_group(
            self.advanced_frame,
            "Dataset Commands",
            [
                ("Build Perception", lambda: self.start_process("perception"), "perception"),
                ("Build Training Dataset Review Preset", lambda: self.start_process("training"), "training"),
                (
                    "Build Training Dataset Focused UI",
                    lambda: self.start_process("training_focused_ui"),
                    "training_focused_ui",
                ),
                (
                    "Build Training Dataset Rebuild",
                    lambda: self.start_process("training_rebuild"),
                    "training_rebuild",
                ),
                ("Export Raw Session", lambda: self.start_process("export"), "export"),
            ],
        )
        self._add_button_group(
            self.advanced_frame,
            "Export / Inspect / Doctor",
            [
                ("Start Training Dataset Inspector", lambda: self.start_process("training_inspector"), "training_inspector"),
                ("Open Training Dataset Inspector", self.open_training_dataset_inspector, None),
                ("Export Curated Dataset", lambda: self.start_process("curated_export"), "curated_export"),
                (
                    "Export Curated Dataset With Splits",
                    lambda: self.start_process("curated_export_splits"),
                    "curated_export_splits",
                ),
                ("Run Validate Session", lambda: self.start_process("validate"), "validate"),
                ("Run Path Tests", lambda: self.start_process("tests"), "tests"),
            ],
        )
        self._add_button_group(
            self.advanced_frame,
            "Target Geometry QA",
            [
                ("Build World Target Geometry", self.build_world_target_geometry, "world_geometry"),
                ("Select Target Candidates", self.select_target_candidates, "target_candidates"),
                ("Run Target Coverage Diagnostic", self.run_target_coverage_diagnostic, "target_coverage"),
                ("Suggest Target Overrides", self.suggest_target_overrides, "target_override_suggestions"),
                ("Start Target Geometry Inspector", self.open_target_geometry_inspector_workflow, "target_geometry_inspector"),
                ("Open Target Geometry Inspector", self.open_target_geometry_inspector, None),
            ],
        )
        self._add_button_group(
            self.advanced_frame,
            "Folders / Process Controls",
            [
                ("Open Sessions Folder", self.open_sessions_folder, None),
                ("Open Newest Session Folder", self.open_newest_session_folder, None),
                ("Open Current Session Perception Folder", self.open_current_session_perception_folder, None),
                ("Open Default Profile Folder", self.open_calibration_profile_folder, None),
                ("Open Training Data Folder", self.open_training_data_folder, None),
                ("Open Curated Folder", self.open_curated_folder, None),
                ("Stop All Started Processes", self.stop_all_processes, None),
                ("Stop Selected Process", self.stop_selected_process, None),
                ("Clear Log", self.clear_log, None),
            ],
        )

    def _toggle_advanced(self):
        if self.show_advanced_var.get():
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def _build_status_panel(self):
        status_frame = ttk.LabelFrame(self, text="Managed Processes")
        status_frame.grid(row=2, column=1, sticky="nsew", padx=(6, 10), pady=6)
        status_frame.columnconfigure(0, weight=1)

        self.status_table = ttk.Treeview(
            status_frame,
            columns=("status", "pid", "started", "command"),
            show="tree headings",
            height=8,
            selectmode="browse",
        )
        self.status_table.heading("#0", text="Name")
        self.status_table.heading("status", text="Status")
        self.status_table.heading("pid", text="PID")
        self.status_table.heading("started", text="Start Time")
        self.status_table.heading("command", text="Command")
        self.status_table.column("#0", width=220, anchor="w")
        self.status_table.column("status", width=100, anchor="center")
        self.status_table.column("pid", width=90, anchor="center")
        self.status_table.column("started", width=100, anchor="center")
        self.status_table.column("command", width=360, anchor="w")
        self.status_table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.status_table.tag_configure("running", background="#e8f5e9")
        self.status_table.tag_configure("starting", background="#fff8e1")
        self.status_table.tag_configure("stopped", foreground="#6b7280")
        self.status_table.tag_configure("exited_error", background="#ffebee")

    def _build_log_panel(self):
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=3, column=1, sticky="nsew", padx=(6, 10), pady=(6, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = ScrolledText(log_frame, wrap="word", height=20)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self.log_text.configure(state="disabled")

        ttk.Checkbutton(log_frame, text="Auto-scroll", variable=self.auto_scroll_var).grid(
            row=1,
            column=0,
            sticky="w",
            padx=8,
            pady=(0, 2),
        )
        ttk.Checkbutton(
            log_frame,
            text="Show health refresh logs",
            variable=self.health_log_var,
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=8,
            pady=(0, 8),
        )

    def _build_health_panel(self):
        health_frame = ttk.LabelFrame(self, text="Telemetry Health")
        health_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 10), pady=6)
        health_frame.columnconfigure(1, weight=1)
        health_frame.columnconfigure(3, weight=1)

        self.health_status_label = Label(
            health_frame,
            text="unknown",
            anchor="w",
            background="#eeeeee",
            foreground="#202124",
            padx=8,
            pady=4,
        )
        self.health_status_label.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 4))

        fields = [
            ("active_session_locked", "Active session locked"),
            ("active_session_path", "Active session path"),
            ("tick_age", "Latest tick age"),
            ("calibration_profile_exists", "Default profile exists"),
            ("label_file_exists", "Labels file exists"),
            ("perception_exists", "Perception exists"),
            ("perception_bundle_count", "Perception bundle count"),
            ("training_data_exists", "Training data exists"),
            ("training_example_count", "Training examples"),
            ("curated_manifest_exists", "Curated manifest exists"),
            ("curated_example_count", "Curated examples"),
            ("missing_crop_count", "Missing crops"),
            ("calibration_server_running", "Calibration server running"),
            ("inspector_server_running", "Inspector server running"),
            ("warning_count", "Warning count"),
            ("validation", "Last status/check result"),
        ]

        split_index = (len(fields) + 1) // 2

        for index, (key, label) in enumerate(fields, start=1):
            column_offset = 0 if index <= split_index else 2
            row = index if index <= split_index else index - split_index
            self.health_values[key] = StringVar(value="-")
            ttk.Label(health_frame, text=label).grid(
                row=row,
                column=column_offset,
                sticky="w",
                padx=(8, 4),
                pady=2,
            )
            ttk.Label(health_frame, textvariable=self.health_values[key]).grid(
                row=row,
                column=column_offset + 1,
                sticky="ew",
                padx=(4, 8),
                pady=2,
            )

        button_row = split_index + 1
        ttk.Button(health_frame, text="Refresh Health", command=self.refresh_health).grid(
            row=button_row,
            column=0,
            sticky="w",
            padx=8,
            pady=(6, 8),
        )
        quick_actions = ttk.Frame(health_frame)
        quick_actions.grid(row=button_row, column=1, columnspan=3, sticky="ew", padx=8, pady=(6, 8))

        for index, (label, key) in enumerate((
            ("Open latest frame file", "latest_frame"),
            ("Open latest_status.json", "latest_status"),
            ("Open manifest.json", "manifest"),
            ("Open newest tick segment", "newest_tick_segment"),
            ("Open newest event segment", "newest_event_segment"),
            ("Open active/newest session folder", "session"),
        )):
            ttk.Button(
                quick_actions,
                text=label,
                command=lambda target_key=key: self.open_health_target(target_key),
            ).grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=2)
            quick_actions.columnconfigure(index % 3, weight=1)

    def _add_button_group(self, parent, title: str, controls: list[tuple[str, object, str | None]]):
        group_index = getattr(self, "_button_group_index", 0)
        row = group_index // 2
        column = group_index % 2
        self._button_group_index = group_index + 1
        group = ttk.LabelFrame(parent, text=title)
        group.grid(
            row=row,
            column=column,
            sticky="new",
            padx=(0, 6) if column == 0 else (6, 0),
            pady=(0, 8),
        )
        group.columnconfigure(0, weight=1)

        for index, (label, command, process_key) in enumerate(controls):
            button = ttk.Button(group, text=label, command=command)
            button.grid(row=index, column=0, sticky="ew", padx=8, pady=3)

            if process_key:
                self.start_buttons[process_key] = button

    def _env_for_subprocess(self) -> dict[str, str]:
        env = os.environ.copy()
        env[SESSIONS_DIR_ENV] = str(self.sessions_dir())
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def sessions_dir(self) -> Path:
        return Path(self.sessions_dir_var.get()).expanduser()

    def selected_session(self) -> Path | None:
        if self.active_session_path is not None and self.active_session_path.exists():
            return self.active_session_path

        return find_newest_session(self.sessions_dir())

    def newest_session_with_training_manifest(self) -> Path | None:
        candidates = []

        for session in list_session_directories(self.sessions_dir()):
            manifest_path = session / "training_data" / "training_manifest.jsonl"

            if manifest_path.exists():
                candidates.append(session)

        if not candidates:
            return None

        return max(candidates, key=session_mtime)

    def session_for_tool(self, button_name: str, *, prefer_training_data: bool = False) -> Path | None:
        if self.active_session_path is not None and self.active_session_path.exists():
            self.log(button_name, f"Using locked active session: {self.active_session_path}")
            return self.active_session_path

        fallback = self.newest_session_with_training_manifest() if prefer_training_data else None

        if fallback is None:
            fallback = find_newest_session(self.sessions_dir())

        if fallback is None:
            self.log(button_name, f"No telemetry session found in {self.sessions_dir()}.")
            return None

        self.log(button_name, f"No active session locked; using fallback session: {fallback}")
        return fallback

    def command_for_process(self, key: str, session_override: Path | None = None) -> list[str]:
        managed = self.managed[key]
        command = list(managed.spec.command)
        session = session_override or self.active_session_path

        if (
            key in SESSION_AWARE_PROCESS_KEYS
            and session is not None
            and "--session" not in command
        ):
            command.extend(["--session", str(session)])

        return command

    def start_process(
        self,
        key: str,
        *,
        session_override: Path | None = None,
        restart_if_command_changed: bool = False,
    ) -> bool:
        managed = self.managed[key]
        command = self.command_for_process(key, session_override=session_override)

        if managed.is_running():
            if (
                restart_if_command_changed
                and managed.started_command is not None
                and managed.started_command != command
            ):
                self.log(
                    managed.spec.name,
                    "Already running for a different command/session; restarting launcher-started process.",
                )
                self.log(managed.spec.name, f"Existing command: {' '.join(managed.started_command)}")
                self.log(managed.spec.name, f"Requested command: {' '.join(command)}")
                self.stop_process(key)
                self.after(
                    STOP_GRACE_MS + 800,
                    lambda: self.start_process(
                        key,
                        session_override=session_override,
                        restart_if_command_changed=False,
                    ),
                )
                return True

            self.log(managed.spec.name, f"Already running with PID {managed.pid()}.")
            self.log(managed.spec.name, f"Reusing command: {' '.join(managed.started_command or command)}")
            return False

        creationflags = 0

        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=self._env_for_subprocess(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
                creationflags=creationflags,
            )
        except OSError as error:
            self.log(managed.spec.name, f"Failed to start: {error}")
            return False

        managed.process = process
        managed.started_command = command
        managed.started_at = datetime.now()
        managed.stop_requested = False
        managed.starting = True
        managed.exit_code = None
        managed.reader_thread = threading.Thread(
            target=self._read_process_output,
            args=(managed,),
            daemon=True,
        )
        managed.reader_thread.start()
        self.log(managed.spec.name, f"Started PID {process.pid}: {' '.join(command)}")
        self._refresh_status_table()
        self.after(1000, lambda: self._mark_started(key))
        return True

    def _mark_started(self, key: str):
        managed = self.managed[key]

        if managed.is_running():
            managed.starting = False
            self._refresh_status_table()

    def start_collection(self):
        self.collection_start_utc = utc_now()
        self.collection_known_sessions = set(list_session_directories(self.sessions_dir()))
        self.collection_waiting = True
        self.active_session_path = None
        self.active_session_path_var.set("not locked")
        self.collection_status_var.set("Waiting for fresh telemetry session. Log into RuneLite.")
        self.log("Collection", "Waiting for fresh telemetry session. Log into RuneLite.")

        if not self.managed["runelite"].is_running():
            self.start_process("runelite")
        else:
            self.log("Collection", "RuneLite Dev Client is already running.")

        self.refresh_health()
        self._poll_collection_lock()

    def stop_collection(self):
        self.collection_waiting = False
        self.collection_status_var.set("Collection stopped; active session lock is unchanged.")

        for key in ("runelite", "latest"):
            self.stop_process(key)

    def unlock_active_session(self):
        self.active_session_path = None
        self.active_session_path_var.set("not locked")
        self.collection_waiting = False
        self.collection_status_var.set("Unlocked; tools will use the newest session.")
        self.log("Collection", "Active session unlocked. Tools will use the newest session.")
        self.refresh_health()

    def set_active_session_to_newest_fresh_session(self):
        candidate, reason = self.find_fresh_session(require_collection_start=False)

        if candidate is None:
            self.log("Collection", reason or "No fresh session found.")
            self.collection_status_var.set(reason or "No fresh session found.")
            return

        self.lock_active_session(candidate)

    def find_fresh_session(self, require_collection_start: bool) -> tuple[Path | None, str | None]:
        sessions = list_session_directories(self.sessions_dir())

        if not sessions:
            return None, f"No sessions found in {self.sessions_dir()}"

        newest_session = find_newest_session(self.sessions_dir())
        old_newest_ignored = False
        candidates = []

        for session in sessions:
            manifest = safe_read_json(session / "manifest.json")
            manifest = manifest if isinstance(manifest, dict) else {}

            if "active" in manifest and manifest.get("active") is not True:
                continue

            tick_files = list_tick_files(session)
            tick_file, latest_tick = read_latest_jsonl_record_with_source(tick_files)
            if latest_tick is None:
                continue
            else:
                tick_age = tick_age_seconds(latest_tick)

            if tick_age is None or tick_age >= FRESH_TICK_SECONDS:
                continue

            if require_collection_start and self.collection_start_utc is not None:
                started_at = parse_utc(
                    manifest.get("startedAtUtc")
                    or manifest.get("startedUtc")
                    or manifest.get("createdAtUtc")
                    or manifest.get("timestampUtc")
                )
                newest_mtime = newest_mtime_utc([
                    session,
                    session / "manifest.json",
                    session / "ticks",
                    tick_file,
                    session / "frames",
                    latest_existing_frame_file(session),
                ])
                started_after = started_at is not None and started_at >= self.collection_start_utc
                modified_after = newest_mtime is not None and newest_mtime >= self.collection_start_utc
                new_session = session.resolve() not in self.collection_known_sessions

                if not (started_after or modified_after or new_session):
                    if newest_session is not None and session.resolve() == newest_session.resolve():
                        old_newest_ignored = True
                    continue

            candidates.append((tick_age, session))

        if not candidates:
            if old_newest_ignored:
                return None, "Old session ignored. Waiting for fresh session."

            if require_collection_start and newest_session is not None and self.collection_start_utc is not None:
                tick_files = list_tick_files(newest_session)
                tick_file = tick_files[-1] if tick_files else None
                newest_mtime = newest_mtime_utc([
                    newest_session,
                    newest_session / "manifest.json",
                    newest_session / "ticks",
                    tick_file,
                    newest_session / "frames",
                    latest_existing_frame_file(newest_session),
                ])

                if newest_mtime is not None and newest_mtime < self.collection_start_utc:
                    return None, "Old session ignored. Waiting for fresh session."

            if self.managed["runelite"].is_running():
                return None, "RuneLite running; waiting for login / new ticks."

            return None, "No fresh telemetry session found."

        _tick_age, session = min(candidates, key=lambda item: item[0])
        return session.resolve(), None

    def _poll_collection_lock(self):
        if not self.collection_waiting:
            return

        session, reason = self.find_fresh_session(require_collection_start=True)

        if session is not None:
            self.lock_active_session(session)
            return

        message = reason or "RuneLite running; waiting for login / new ticks."
        self.collection_status_var.set(message)
        self.refresh_health()
        self.after(COLLECTION_POLL_MS, self._poll_collection_lock)

    def lock_active_session(self, session: Path):
        self.active_session_path = session.resolve()
        self.active_session_path_var.set(str(self.active_session_path))
        self.collection_waiting = False
        self.collection_status_var.set(f"Active session locked: {self.active_session_path}")
        self.log("Collection", f"Active session locked: {self.active_session_path}")
        self.refresh_health()
        self._start_configured_live_tools_after_lock()

    def _start_configured_live_tools_after_lock(self):
        if self.auto_start_latest_var.get() and not self.managed["latest"].is_running():
            self.start_process("latest")

        if self.auto_open_replay_var.get():
            self.open_replay_label_workflow()

        if self.auto_open_calibration_var.get():
            self.open_calibration_workflow()

    def open_replay_label_workflow(self):
        session = self.session_for_tool("Open Replay / Label")

        if session is None:
            return

        self.log("Open Replay / Label", f"Opening local URL: {REPLAY_URL}")
        command = self.command_for_process("replay", session_override=session)
        needs_restart = (
            self.managed["replay"].is_running()
            and self.managed["replay"].started_command is not None
            and self.managed["replay"].started_command != command
        )
        self.start_process(
            "replay",
            session_override=session,
            restart_if_command_changed=True,
        )

        self.after(STOP_GRACE_MS + 1800 if needs_restart else 800, self.open_replay_viewer)

    def open_calibration_workflow(self):
        session = self.session_for_tool("Open Calibration")

        if session is None:
            self.log("Calibration", "No session found. Start Collection and wait for fresh ticks first.")
            return

        calibration = self.managed["calibration"]
        command = self.command_for_process("calibration", session_override=session)

        if calibration.is_running() and calibration.started_command == command:
            self.log("Calibration", "Calibration server is already running; reusing it.")
            self.open_calibration_ui()
            return

        if calibration.is_running():
            self.log(
                "Calibration",
                "Calibration server is running for another command/session; restarting launcher-started server.",
            )
            self.start_process(
                "calibration",
                session_override=session,
                restart_if_command_changed=True,
            )
            self.after(STOP_GRACE_MS + 1800, self.open_calibration_ui)
            return

        latest_frame = latest_existing_frame_file(session)

        if latest_frame is None:
            self.log(
                "Calibration",
                "No retained frame found yet. Log in and wait a few ticks, then refresh calibration.",
            )
            return

        perception_dir = session / "perception"

        if not (perception_dir / "tick_bundles.jsonl").exists() or not (
            perception_dir / "screen_regions.json"
        ).exists():
            self.log(
                "Calibration",
                "Perception files are not built yet. Run Build Dataset, then open calibration again.",
            )
            return

        self.log("Calibration", f"Using retained frame: {latest_frame}")
        self.log("Open Calibration", f"Command: {' '.join(command)}")
        self.start_process("calibration", session_override=session)
        self.after(1000, self.open_calibration_ui)

    def build_dataset_pipeline(self):
        session = self.session_for_tool("Build Dataset")

        if session is None:
            return

        self.start_pipeline("Build Dataset", ["perception", "training", "dataset_status"], session)

    def export_curated_pipeline(self):
        session = self.session_for_tool("Export Curated", prefer_training_data=True)

        if session is None:
            return

        self.start_pipeline("Export Curated", ["curated_export_splits", "dataset_status"], session)

    def run_doctor_status(self):
        session = self.session_for_tool("Run Doctor / Status", prefer_training_data=True)

        if session is None:
            return

        self.log(
            "Run Doctor / Status",
            f"Command: {' '.join(self.command_for_process('dataset_status', session_override=session))}",
        )
        self.start_process("dataset_status", session_override=session)

    def check_live_setup(self):
        session = self.session_for_tool("Check Live Setup")

        if session is None:
            return

        self.log("Check Live Setup", "Default live input is PluginSnapshotEndpoint/WorldModel; legacy packet archives are cleanup-only.")
        self.log("Check Live Setup", f"Resolved session: {session}")
        self.start_process("live_setup_check", session_override=session)

    def inspect_compact_packets(self):
        session = self.session_for_tool("Inspect Compact Packets")

        if session is None:
            return

        self.log("Inspect Compact Packets", "Retired path: runs the legacy live-packet cleanup report instead of reading packets as truth.")
        self.log("Inspect Compact Packets", f"Resolved session: {session}")
        self.start_process("compact_packet_inspector", session_override=session)

    def start_live_processor(self):
        session = self.session_for_tool("Start Live Processor")

        if session is None:
            return

        self.log("Live Target Processor", "Default live input: PluginSnapshotEndpoint/WorldModel; packet-file input is retired.")
        self.log("Live Target Processor", f"Resolved session: {session}")
        self.start_process("live_processor", session_override=session, restart_if_command_changed=True)

    def build_world_target_geometry(self):
        session = self.session_for_tool("Build World Target Geometry")

        if session is None:
            return

        self.log("Build World Target Geometry", "Builds broad world_targets.jsonl from raw read-only scene/NPC/player/object geometry.")
        self.log("Build World Target Geometry", f"Resolved session: {session}")
        self.start_process("world_geometry", session_override=session)

    def select_target_candidates(self):
        session = self.session_for_tool("Select Target Candidates")

        if session is None:
            return

        self.log("Select Target Candidates", "Ranks/filter-selects existing world/UI targets; sparse candidates do not mean sparse raw/world capture.")
        self.log("Select Target Candidates", f"Resolved session: {session}")
        self.start_process("target_candidates", session_override=session)

    def run_target_coverage_diagnostic(self):
        session = self.session_for_tool("Run Target Coverage Diagnostic")

        if session is None:
            return

        self.log("Target Coverage Diagnostic", "Reports raw -> world_targets -> target_candidates coverage and scene capture cap pressure.")
        self.log("Target Coverage Diagnostic", f"Resolved session: {session}")
        self.start_process("target_coverage", session_override=session)

    def suggest_target_overrides(self):
        session = self.session_for_tool("Suggest Target Overrides")

        if session is None:
            return

        self.log("Suggest Target Overrides", "Prints read-only skeletons for fallback/unclassified scene object labels; it does not edit override files.")
        self.log("Suggest Target Overrides", f"Resolved session: {session}")
        self.start_process("target_override_suggestions", session_override=session)

    def open_target_geometry_inspector_workflow(self):
        session = self.session_for_tool("Open Target Geometry Inspector")

        if session is None:
            return

        self.log("Target Geometry Inspector", "Inspect world_targets first for broad QA; use candidates for task-specific QA.")
        self.log("Target Geometry Inspector", f"Opening local URL: {TARGET_GEOMETRY_INSPECTOR_URL}")
        self.start_process("target_geometry_inspector", session_override=session, restart_if_command_changed=True)
        self.after(800, self.open_target_geometry_inspector)

    def inspect_dataset_workflow(self):
        session = self.session_for_tool("Inspect Dataset", prefer_training_data=True)

        if session is None:
            return

        self.log("Inspect Dataset", f"Opening local URL: {TRAINING_INSPECTOR_URL}")
        inspector = self.managed["training_inspector"]

        if inspector.is_running():
            self.log(
                "Inspect Dataset",
                "Restarting launcher-started inspector so it reloads the current training manifest.",
            )
            self.stop_process("training_inspector")
            self.after(
                STOP_GRACE_MS + 800,
                lambda: self.start_process("training_inspector", session_override=session),
            )
            self.after(STOP_GRACE_MS + 1800, self.open_training_dataset_inspector)
            return

        self.start_process("training_inspector", session_override=session)

        self.after(800, self.open_training_dataset_inspector)

    def start_pipeline(self, name: str, keys: list[str], session: Path):
        if self.pipeline_current_key is not None:
            self.log("Pipeline", f"{self.pipeline_name or 'Pipeline'} is already running.")
            return

        running = [self.managed[key].spec.name for key in keys if self.managed[key].is_running()]

        if running:
            self.log("Pipeline", f"Cannot start {name}; already running: {', '.join(running)}")
            return

        self.pipeline_name = name
        self.pipeline_queue = list(keys)
        self.pipeline_current_key = None
        self.pipeline_session = session
        self.log("Pipeline", f"Starting {name}.")
        self.log("Pipeline", f"Resolved session: {session}")
        self._start_next_pipeline_step()

    def _start_next_pipeline_step(self):
        if not self.pipeline_queue:
            self.log("Pipeline", f"{self.pipeline_name or 'Pipeline'} complete.")
            self.pipeline_name = None
            self.pipeline_current_key = None
            self.pipeline_session = None
            self.refresh_health()
            return

        key = self.pipeline_queue.pop(0)
        self.pipeline_current_key = key
        self.log("Pipeline", f"Running {self.managed[key].spec.name}.")
        self.log(
            "Pipeline",
            f"Command: {' '.join(self.command_for_process(key, session_override=self.pipeline_session))}",
        )

        if not self.start_process(key, session_override=self.pipeline_session):
            self.log("Pipeline", f"Unable to start {self.managed[key].spec.name}; pipeline stopped.")
            self.pipeline_name = None
            self.pipeline_queue = []
            self.pipeline_current_key = None
            self.pipeline_session = None

    def _read_process_output(self, managed: ManagedProcess):
        process = managed.process

        if process is None or process.stdout is None:
            return

        try:
            for line in process.stdout:
                self.output_queue.put((managed.spec.name, line.rstrip("\n")))
        finally:
            return_code = process.wait()
            managed.exit_code = return_code
            managed.starting = False
            self.output_queue.put((managed.spec.name, f"Exited with code {return_code}."))
            self.control_queue.put(("process_exit", managed.spec.key, return_code))

            if managed.spec.key in {"validate", "dataset_status"}:
                result = "pass" if return_code == 0 else "fail"
                self.last_validation_result = (
                    f"{managed.spec.name} {result} at {datetime.now().strftime('%H:%M:%S')}"
                )

            if managed.spec.key == "runelite":
                self.output_queue.put((
                    managed.spec.name,
                    "Root process exited; child process may have detached. Stop may not affect detached children.",
                ))

    def _drain_output_queue(self):
        drained = 0

        while drained < 200:
            try:
                name, line = self.output_queue.get_nowait()
            except queue.Empty:
                break

            self.log(name, line)
            drained += 1

        self.after(100, self._drain_output_queue)

    def _drain_control_queue(self):
        try:
            while True:
                event_type, key, return_code = self.control_queue.get_nowait()

                if event_type == "process_exit":
                    self._handle_process_exit(key, return_code)
        except queue.Empty:
            pass

        self.after(150, self._drain_control_queue)

    def _handle_process_exit(self, key: str, return_code: int):
        if self.pipeline_current_key != key:
            return

        if return_code != 0:
            self.log(
                "Pipeline",
                f"{self.managed[key].spec.name} failed with exit code {return_code}; pipeline stopped.",
            )
            self.pipeline_name = None
            self.pipeline_queue = []
            self.pipeline_current_key = None
            self.pipeline_session = None
            self.refresh_health()
            return

        self.pipeline_current_key = None
        self._start_next_pipeline_step()

    def refresh_health(self):
        if self.health_refresh_running:
            return

        self.health_refresh_running = True
        sessions_dir = self.sessions_dir()
        active_session = self.active_session_path
        threading.Thread(
            target=self._collect_health,
            args=(sessions_dir, active_session),
            daemon=True,
        ).start()

    def _auto_refresh_health(self):
        self.refresh_health()
        self.after(HEALTH_REFRESH_MS, self._auto_refresh_health)

    def _collect_health(self, sessions_dir: Path, active_session: Path | None):
        try:
            health = collect_health(sessions_dir, self.last_validation_result, active_session)
            self.health_queue.put({"ok": True, "health": health})
        except Exception as error:
            self.health_queue.put({"ok": False, "error": str(error)})

    def _drain_health_queue(self):
        try:
            while True:
                payload = self.health_queue.get_nowait()
                self.health_refresh_running = False

                if payload.get("ok"):
                    self._apply_health(payload["health"])
                else:
                    self.log("Health", f"Refresh failed: {payload.get('error')}")
        except queue.Empty:
            pass

        self.after(250, self._drain_health_queue)

    def _apply_health(self, health: dict):
        values = dict(health.get("values", {}))
        values["calibration_server_running"] = (
            "yes" if self.managed["calibration"].is_running() else "no"
        )
        values["inspector_server_running"] = (
            "yes" if self.managed["training_inspector"].is_running() else "no"
        )
        self.health_paths = health.get("paths", {})

        for key, variable in self.health_values.items():
            variable.set(values.get(key, "-"))

        status = health.get("status", "unknown")
        message = health.get("message", "Health unknown")
        hint = ""

        if status == "stale":
            if self.collection_waiting and self.active_session_path is None:
                message = self.collection_status_var.get() or "Waiting for fresh telemetry session."
            elif self.managed["runelite"].is_running():
                message = "RuneLite running, waiting for new ticks."
            else:
                message = "Stale: no live collection running."

        if status == "stale" and not self.managed["runelite"].is_running():
            hint = " Start Collection to resume live telemetry."

        self.health_status_label.configure(
            text=f"{status.upper()}: {message}{hint}",
            background=health.get("background", "#eeeeee"),
            foreground=health.get("foreground", "#202124"),
        )

        if self.health_log_var.get():
            self.log(
                "Health",
                f"{status}: tick={values.get('tick_id', '-')} age={values.get('tick_age', '-')} active={values.get('active', '-')}",
            )

    def _poll_processes(self):
        self._refresh_status_table()
        self.after(500, self._poll_processes)

    def log(self, name: str, line: str):
        prefix = f"[{name}] "
        self.log_text.configure(state="normal")
        self.log_text.insert("end", prefix + line + "\n")
        self.log_line_count += 1

        if self.log_line_count > MAX_LOG_LINES:
            self.log_text.delete("1.0", "200.0")
            self.log_line_count = max(0, self.log_line_count - 199)

        if self.auto_scroll_var.get():
            self.log_text.see("end")

        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_line_count = 0

    def _refresh_status_table(self):
        selected = self.status_table.selection()
        selected_key = selected[0] if selected else None

        for item in self.status_table.get_children():
            self.status_table.delete(item)

        for key, managed in self.managed.items():
            status = managed.status()
            command = managed.started_command if managed.is_running() and managed.started_command else self.command_for_process(key)
            self.status_table.insert(
                "",
                "end",
                iid=key,
                text=managed.spec.name,
                values=(
                    status,
                    managed.pid(),
                    managed.started_display(),
                    " ".join(command),
                ),
                tags=(managed.status_tag(),),
            )

            button = self.start_buttons.get(key)

            if button is not None:
                button.configure(state="disabled" if managed.is_running() else "normal")

        if selected_key in self.managed:
            self.status_table.selection_set(selected_key)

    def selected_process_key(self) -> str | None:
        selected = self.status_table.selection()
        return selected[0] if selected else None

    def stop_selected_process(self):
        key = self.selected_process_key()

        if key is None:
            self.log("Launcher", "Select a process first.")
            return

        self.stop_process(key)

    def stop_all_processes(self):
        for key in self.managed:
            self.stop_process(key)

    def stop_process(self, key: str):
        managed = self.managed[key]

        if managed.process is None:
            self.log(managed.spec.name, "Not running.")
            return

        if not managed.is_running():
            managed.exit_code = managed.process.poll()
            self.log(managed.spec.name, f"Already stopped with exit code {managed.exit_code}.")
            return

        managed.stop_requested = True
        self.log(managed.spec.name, f"Stopping PID {managed.process.pid}.")

        if os.name == "nt":
            self._taskkill_process_tree(managed)
            return

        try:
            managed.process.terminate()
        except OSError as error:
            self.log(managed.spec.name, f"Terminate failed: {error}")
            return

        self.after(STOP_GRACE_MS, lambda: self._kill_if_still_running(key))

    def _taskkill_process_tree(self, managed: ManagedProcess):
        if managed.process is None:
            return

        command = ["taskkill", "/PID", str(managed.process.pid), "/T", "/F"]
        self.log(managed.spec.name, f"Running: {' '.join(command)}")

        threading.Thread(
            target=self._run_taskkill,
            args=(managed.spec.name, command),
            daemon=True,
        ).start()

    def _run_taskkill(self, process_name: str, command: list[str]):
        def enqueue(line: str):
            self.output_queue.put((process_name, line))

        try:
            result = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                shell=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            enqueue(f"taskkill failed: {error}")
            return

        output = (result.stdout or "").strip()
        error_output = (result.stderr or "").strip()

        if output:
            for line in output.splitlines():
                enqueue(f"taskkill: {line}")

        if error_output:
            for line in error_output.splitlines():
                enqueue(f"taskkill error: {line}")

        if result.returncode == 0:
            enqueue("taskkill completed.")
        else:
            enqueue(f"taskkill exited with code {result.returncode}.")

    def _kill_if_still_running(self, key: str):
        managed = self.managed[key]

        if not managed.is_running() or managed.process is None:
            return

        self.log(managed.spec.name, f"Still running after grace period; killing PID {managed.process.pid}.")

        try:
            managed.process.kill()
        except OSError as error:
            self.log(managed.spec.name, f"Kill failed: {error}")

    def open_replay_viewer(self):
        webbrowser.open(REPLAY_URL)
        self.log("Launcher", f"Opened {REPLAY_URL}")

    def open_calibration_ui(self):
        webbrowser.open(CALIBRATION_URL)
        self.log("Launcher", f"Opened {CALIBRATION_URL}")

    def open_training_dataset_inspector(self):
        webbrowser.open(TRAINING_INSPECTOR_URL)
        self.log("Launcher", f"Opened {TRAINING_INSPECTOR_URL}")

    def open_target_geometry_inspector(self):
        webbrowser.open(TARGET_GEOMETRY_INSPECTOR_URL)
        self.log("Launcher", f"Opened {TARGET_GEOMETRY_INSPECTOR_URL}")

    def open_labels_file(self):
        if not TAB_LABELS_PATH.exists():
            self.log(
                "Launcher",
                f"No labels file found at {TAB_LABELS_PATH}. Create tab_labels.json or use the replay labeling workflow first.",
            )
            return

        self.open_path(TAB_LABELS_PATH, "Labels File")

    def open_replay_labeling_help(self):
        if not REPLAY_LABELING_HELP_PATH.exists():
            self.log("Launcher", f"Replay labeling help not found: {REPLAY_LABELING_HELP_PATH}")
            return

        self.open_path(REPLAY_LABELING_HELP_PATH, "Replay Labeling Help")

    def open_sessions_folder(self):
        self.open_folder(self.sessions_dir(), "Sessions Folder")

    def open_newest_session_folder(self):
        newest = find_newest_session(self.sessions_dir())

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        self.open_folder(newest, "Newest Session Folder")

    def open_calibration_profile_folder(self):
        self.open_folder(CALIBRATION_PROFILE_DIR, "Calibration Profile Folder")

    def open_current_session_perception_folder(self):
        newest = self.selected_session()

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        self.open_folder(newest / "perception", "Current Session Perception Folder")

    def open_latest_test_crops(self):
        newest = self.selected_session()

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        test_crops_root = newest / "perception" / "test_crops"
        latest_test_crops = newest_directory_in_dir(test_crops_root)

        if latest_test_crops is None:
            self.log(
                "Launcher",
                f"No test crop runs found yet in {test_crops_root}. Generate Test Crops first.",
            )
            return

        self.open_folder(latest_test_crops, "Latest Test Crops")

    def open_training_data_folder(self):
        newest = self.selected_session()

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        training_data = newest / "training_data"

        if not training_data.exists():
            self.log(
                "Launcher",
                f"No training data folder found yet at {training_data}. Build Training Dataset first.",
            )
            return

        self.open_folder(training_data, "Training Data Folder")

    def open_curated_folder(self):
        newest = self.selected_session()

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        curated = newest / "training_data" / "curated"

        if not curated.exists():
            self.log(
                "Launcher",
                f"No curated folder found yet at {curated}. Export Curated Dataset first.",
            )
            return

        self.open_folder(curated, "Curated Folder")

    def open_health_target(self, key: str):
        labels = {
            "latest_frame": "latest frame file",
            "latest_status": "latest_status.json",
            "manifest": "manifest.json",
            "newest_tick_segment": "newest tick segment",
            "newest_event_segment": "newest event segment",
            "session": "newest session folder",
        }
        label = labels.get(key, key)
        raw_path = self.health_paths.get(key)

        if not raw_path:
            message = f"No {label} is available from the current health snapshot."
            self.log("Launcher", message)
            messagebox.showinfo("Missing target", message)
            return

        self.open_path(Path(raw_path), label)

    def open_folder(self, path: Path, label: str):
        self.open_path(path, label)

    def open_path(self, path: Path, label: str):
        path = path.expanduser()

        if not path.exists():
            message = f"{label} does not exist: {path}"
            self.log("Launcher", message)
            messagebox.showinfo("Missing target", message)
            return

        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))
            else:
                webbrowser.open(path.resolve().as_uri())
            self.log("Launcher", f"Opened {label}: {path}")
        except OSError as error:
            self.log("Launcher", f"Unable to open {label}: {error}")

    def _on_close(self):
        running = [managed for managed in self.managed.values() if managed.is_running()]

        if running:
            should_close = messagebox.askyesno(
                "Stop started processes?",
                "Stop all processes started by this launcher and close?",
            )

            if not should_close:
                return

            self.stop_all_processes()

        self.destroy()


def main():
    app = LauncherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
