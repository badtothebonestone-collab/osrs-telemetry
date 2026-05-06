import argparse
import json
import re
from pathlib import Path

from label_ranges import load_label_ranges
from tab_profile_names import canonical_tab_profile_key
from telemetry_paths import find_newest_session, get_sessions_dir, list_frame_index_files, safe_read_json


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "calibration_profiles" / "default_screen_regions.json"
DEFAULT_LABELS_PATH = Path(__file__).resolve().with_name("tab_labels.json")
TARGET_OVERRIDES_PATH = Path(__file__).resolve().with_name("target_name_overrides.json")
FRAME_TICK_RE = re.compile(r"frame-tick-(\d+)\.[^.]+$", re.IGNORECASE)
FRAME_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0

    count = 0

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    count += 1
    except OSError:
        return 0

    return count


def count_files(path: Path) -> int:
    if not path.exists():
        return 0

    try:
        return sum(1 for child in path.rglob("*") if child.is_file())
    except OSError:
        return 0


def target_override_counts() -> dict:
    document = safe_read_json(TARGET_OVERRIDES_PATH)

    if not isinstance(document, dict):
        return {"sceneObjects": 0, "groundItems": 0, "npcs": 0}

    counts = {}

    for group in ("sceneObjects", "groundItems", "npcs"):
        value = document.get(group)
        counts[group] = len(value) if isinstance(value, dict) else 0

    return counts


def tick_range_from_jsonl(path: Path) -> tuple[int | None, int | None]:
    first = None
    last = None

    if not path.exists():
        return None, None

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()

                if not text:
                    continue

                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue

                tick_id = record.get("tickId") if isinstance(record, dict) else None

                if not isinstance(tick_id, int):
                    continue

                first = tick_id if first is None else min(first, tick_id)
                last = tick_id if last is None else max(last, tick_id)
    except OSError:
        return None, None

    return first, last


def tick_range_from_index_or_jsonl(index: dict, path: Path) -> tuple[int | None, int | None]:
    value = index.get("selectedTickRange") if isinstance(index, dict) else None

    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        return value[0], value[1]

    return tick_range_from_jsonl(path)


def ranges_overlap(first_a, last_a, first_b, last_b) -> bool | None:
    if not all(isinstance(value, int) for value in (first_a, last_a, first_b, last_b)):
        return None

    return last_a >= first_b and last_b >= first_a


def frame_tick_from_path(path: Path) -> int | None:
    match = FRAME_TICK_RE.search(path.name)

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def frame_file_summary(session: Path) -> dict:
    frames_dir = session / "frames"

    if not frames_dir.exists():
        return {
            "frameFileCount": 0,
            "firstFrameTick": None,
            "lastFrameTick": None,
            "latestFrameFilePath": None,
        }

    try:
        files = sorted(
            path
            for path in frames_dir.iterdir()
            if path.is_file() and path.suffix.lower() in FRAME_IMAGE_SUFFIXES
        )
    except OSError:
        files = []

    ticks = [tick for tick in (frame_tick_from_path(path) for path in files) if tick is not None]
    latest = max(files, key=lambda path: path.stat().st_mtime, default=None)
    return {
        "frameFileCount": len(files),
        "firstFrameTick": min(ticks) if ticks else None,
        "lastFrameTick": max(ticks) if ticks else None,
        "latestFrameFilePath": str(latest) if latest else None,
    }


def latest_directory(path: Path) -> Path | None:
    if not path.exists():
        return None

    try:
        directories = [child for child in path.iterdir() if child.is_dir()]
    except OSError:
        return None

    if not directories:
        return None

    return max(directories, key=lambda child: child.stat().st_mtime)


def region_doc_model(doc) -> tuple[dict, dict]:
    if not isinstance(doc, dict):
        return {}, {}

    if isinstance(doc.get("baseRegions"), dict) or isinstance(doc.get("tabProfiles"), dict):
        base_regions = doc.get("baseRegions") if isinstance(doc.get("baseRegions"), dict) else {}
        tab_profiles = doc.get("tabProfiles") if isinstance(doc.get("tabProfiles"), dict) else {}
        return base_regions, tab_profiles

    regions = doc.get("regions") if isinstance(doc.get("regions"), dict) else {}
    return regions, {}


def active_tab_counts(perception_index: dict | None) -> dict:
    if not isinstance(perception_index, dict):
        return {}

    value = perception_index.get("activeTabCounts")
    return value if isinstance(value, dict) else {}


def label_active_tabs(labels_doc: dict) -> set[str]:
    labels = labels_doc.get("labels") if isinstance(labels_doc, dict) else []
    return {
        canonical_tab_profile_key(label.get("activeTab"))
        for label in labels
        if isinstance(label, dict) and label.get("activeTab")
    }


def profile_names(tab_profiles: dict) -> set[str]:
    return {canonical_tab_profile_key(name) for name in tab_profiles.keys()}


def status_for_session(session: Path | None) -> dict:
    labels_doc = load_label_ranges()
    default_profile = safe_read_json(DEFAULT_PROFILE_PATH)
    default_base_regions, default_tab_profiles = region_doc_model(default_profile)
    override_counts = target_override_counts()
    status = {
        "sessionPath": str(session) if session else None,
        "defaultProfilePath": str(DEFAULT_PROFILE_PATH),
        "defaultProfileExists": DEFAULT_PROFILE_PATH.exists(),
        "sessionProfileExists": False,
        "sessionProfilePath": None,
        "labelsPath": str(DEFAULT_LABELS_PATH),
        "labelsFileExists": DEFAULT_LABELS_PATH.exists(),
        "labelCount": len(labels_doc.get("labels", [])),
        "labelWarnings": labels_doc.get("warnings", []),
        "targetOverridesPath": str(TARGET_OVERRIDES_PATH),
        "targetOverridesExist": TARGET_OVERRIDES_PATH.exists(),
        "targetOverrideSceneObjectCount": override_counts["sceneObjects"],
        "targetOverrideGroundItemCount": override_counts["groundItems"],
        "targetOverrideNpcCount": override_counts["npcs"],
        "activeTabCounts": {},
        "frameFileCount": 0,
        "firstFrameFileTick": None,
        "lastFrameFileTick": None,
        "latestFrameFilePath": None,
        "frameIndexRecordCount": 0,
        "manifestFrameCount": None,
        "manifestDeletedFrameCount": None,
        "manifestDroppedFrameCount": None,
        "manifestScreenshotEveryTicks": None,
        "manifestFrameCaptureMode": None,
        "manifestMaxFrameStorageMb": None,
        "latestTestCropRun": None,
        "trainingManifestExampleCount": 0,
        "trainingCropCount": 0,
        "uiGeometryExists": False,
        "uiTargetRecordCount": 0,
        "uiGeometryGeneratedAtUtc": None,
        "uiGeometryFirstTick": None,
        "uiGeometryLastTick": None,
        "uiGeometryFrameOverlap": None,
        "worldGeometryExists": False,
        "worldTargetRecordCount": 0,
        "worldGeometryGeneratedAtUtc": None,
        "worldGeometryFirstTick": None,
        "worldGeometryLastTick": None,
        "worldGeometryFrameOverlap": None,
        "worldGeometrySourceSchema": None,
        "worldObjectKeySupport": False,
        "worldStaticIndexRecordCount": 0,
        "sceneStaticIndexExists": False,
        "sceneStaticIndexRecordCount": 0,
        "worldTargetRoleCounts": {},
        "worldTargetCategoryCounts": {},
        "worldTopTargetTags": {},
        "worldUnclassifiedSceneObjectCount": 0,
        "worldFallbackSceneObjectCount": 0,
        "targetGeometryInspectorCommand": "python telemetry-viewer\\target_geometry_inspector.py",
        "targetOverrideSuggestionCommand": "python telemetry-viewer\\suggest_target_overrides.py --limit 25",
        "targetCandidatesExist": False,
        "targetCandidateCount": 0,
        "targetCandidatesGeneratedAtUtc": None,
        "targetHandoffExists": False,
        "targetHandoffCandidateCount": 0,
        "targetHandoffGeneratedAtUtc": None,
        "scenarioDatasetExists": False,
        "scenarioDatasetType": None,
        "scenarioDatasetRecordCount": 0,
        "scenarioSelectedCandidateCount": 0,
        "scenarioContextTargetCount": 0,
        "scenarioGeneratedAtUtc": None,
        "scenarioInspectorCommand": "python telemetry-viewer\\scenario_inspector.py --scenario bank_area",
        "curatedManifestExists": False,
        "curatedManifestExampleCount": 0,
        "curatedGeneratedAtUtc": None,
        "curatedSplitCounts": {},
        "defaultTabProfileCount": len(default_tab_profiles),
        "sessionTabProfileCount": 0,
        "warnings": [],
    }

    if session is None:
        status["warnings"].append("no telemetry session found")
        return status

    perception_dir = session / "perception"
    training_dir = session / "training_data"
    manifest = safe_read_json(session / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    frame_summary = frame_file_summary(session)
    frame_index_record_count = sum(count_jsonl(path) for path in list_frame_index_files(session))
    session_profile_path = perception_dir / "screen_regions.json"
    session_profile = safe_read_json(session_profile_path)
    _session_base_regions, session_tab_profiles = region_doc_model(session_profile)
    perception_index = safe_read_json(perception_dir / "perception_index.json")
    latest_test_crop_run = latest_directory(perception_dir / "test_crops")
    training_manifest_path = training_dir / "training_manifest.jsonl"
    training_crops_path = training_dir / "crops"
    ui_geometry_dir = session / "interaction_geometry"
    ui_targets_path = ui_geometry_dir / "ui_targets.jsonl"
    ui_geometry_index_path = ui_geometry_dir / "ui_geometry_index.json"
    ui_geometry_index = safe_read_json(ui_geometry_index_path)
    ui_geometry_index = ui_geometry_index if isinstance(ui_geometry_index, dict) else {}
    world_targets_path = ui_geometry_dir / "world_targets.jsonl"
    world_geometry_index_path = ui_geometry_dir / "world_geometry_index.json"
    world_geometry_index = safe_read_json(world_geometry_index_path)
    world_geometry_index = world_geometry_index if isinstance(world_geometry_index, dict) else {}
    scene_static_index_path = ui_geometry_dir / "scene_static_index.jsonl"
    target_candidates_path = ui_geometry_dir / "target_candidates.jsonl"
    target_candidates_index_path = ui_geometry_dir / "target_candidates_index.json"
    target_candidates_index = safe_read_json(target_candidates_index_path)
    target_candidates_index = target_candidates_index if isinstance(target_candidates_index, dict) else {}
    target_handoff_dir = ui_geometry_dir / "handoff"
    target_handoff_jsonl_path = target_handoff_dir / "latest_candidates.jsonl"
    target_handoff_index_path = target_handoff_dir / "handoff_index.json"
    target_handoff_index = safe_read_json(target_handoff_index_path)
    target_handoff_index = target_handoff_index if isinstance(target_handoff_index, dict) else {}
    scenario_dir = session / "scenario_datasets"
    scenario_index_path = scenario_dir / "scenario_index.json"
    scenario_index = safe_read_json(scenario_index_path)
    scenario_index = scenario_index if isinstance(scenario_index, dict) else {}
    scenario_type = scenario_index.get("scenarioType") if isinstance(scenario_index.get("scenarioType"), str) else None
    scenario_dataset_path = scenario_dir / f"{scenario_type}.jsonl" if scenario_type else None
    ui_first_tick, ui_last_tick = tick_range_from_index_or_jsonl(ui_geometry_index, ui_targets_path)
    world_first_tick, world_last_tick = tick_range_from_index_or_jsonl(world_geometry_index, world_targets_path)
    curated_manifest_path = training_dir / "curated" / "curated_manifest.jsonl"
    curated_index_path = training_dir / "curated" / "curated_index.json"
    curated_index = safe_read_json(curated_index_path)
    curated_index = curated_index if isinstance(curated_index, dict) else {}

    status.update(
        {
            "sessionProfileExists": session_profile_path.exists(),
            "sessionProfilePath": str(session_profile_path),
            "activeTabCounts": active_tab_counts(perception_index),
            "frameFileCount": frame_summary["frameFileCount"],
            "firstFrameFileTick": frame_summary["firstFrameTick"],
            "lastFrameFileTick": frame_summary["lastFrameTick"],
            "latestFrameFilePath": frame_summary["latestFrameFilePath"],
            "frameIndexRecordCount": frame_index_record_count,
            "manifestFrameCount": manifest.get("frameCount"),
            "manifestDeletedFrameCount": manifest.get("deletedFrameCount"),
            "manifestDroppedFrameCount": manifest.get("droppedFrameCount"),
            "manifestScreenshotEveryTicks": manifest.get("screenshotEveryTicks"),
            "manifestFrameCaptureMode": manifest.get("frameCaptureMode"),
            "manifestMaxFrameStorageMb": manifest.get("maxFrameStorageMb"),
            "latestTestCropRun": str(latest_test_crop_run) if latest_test_crop_run else None,
            "trainingManifestExampleCount": count_jsonl(training_manifest_path),
            "trainingCropCount": count_files(training_crops_path),
            "uiGeometryExists": ui_targets_path.exists() and ui_geometry_index_path.exists(),
            "uiTargetRecordCount": (
                ui_geometry_index.get("targetRecordCount")
                if isinstance(ui_geometry_index.get("targetRecordCount"), int)
                else count_jsonl(ui_targets_path)
            ),
            "uiGeometryGeneratedAtUtc": ui_geometry_index.get("generatedAtUtc"),
            "uiGeometryFirstTick": ui_first_tick,
            "uiGeometryLastTick": ui_last_tick,
            "uiGeometryFrameOverlap": ranges_overlap(
                ui_first_tick,
                ui_last_tick,
                frame_summary["firstFrameTick"],
                frame_summary["lastFrameTick"],
            ),
            "worldGeometryExists": world_targets_path.exists() and world_geometry_index_path.exists(),
            "worldTargetRecordCount": (
                world_geometry_index.get("targetRecordCount")
                if isinstance(world_geometry_index.get("targetRecordCount"), int)
                else count_jsonl(world_targets_path)
            ),
            "worldGeometryGeneratedAtUtc": world_geometry_index.get("generatedAtUtc"),
            "worldGeometryFirstTick": world_first_tick,
            "worldGeometryLastTick": world_last_tick,
            "worldGeometryFrameOverlap": ranges_overlap(
                world_first_tick,
                world_last_tick,
                frame_summary["firstFrameTick"],
                frame_summary["lastFrameTick"],
            ),
            "worldGeometrySourceSchema": world_geometry_index.get("sourceSchema"),
            "worldObjectKeySupport": bool(world_geometry_index.get("objectKeySupport")),
            "worldStaticIndexRecordCount": (
                world_geometry_index.get("staticIndexRecordCount")
                if isinstance(world_geometry_index.get("staticIndexRecordCount"), int)
                else count_jsonl(scene_static_index_path)
            ),
            "sceneStaticIndexExists": scene_static_index_path.exists(),
            "sceneStaticIndexRecordCount": count_jsonl(scene_static_index_path),
            "worldTargetRoleCounts": (
                world_geometry_index.get("countsByTargetRole")
                if isinstance(world_geometry_index.get("countsByTargetRole"), dict)
                else {}
            ),
            "worldTargetCategoryCounts": (
                world_geometry_index.get("countsByTargetCategory")
                if isinstance(world_geometry_index.get("countsByTargetCategory"), dict)
                else {}
            ),
            "worldTopTargetTags": (
                world_geometry_index.get("topTargetTags")
                if isinstance(world_geometry_index.get("topTargetTags"), dict)
                else {}
            ),
            "worldUnclassifiedSceneObjectCount": (
                world_geometry_index.get("nameDiagnostics", {}).get("unclassifiedSceneObjectCount", 0)
                if isinstance(world_geometry_index.get("nameDiagnostics"), dict)
                else 0
            ),
            "worldFallbackSceneObjectCount": (
                world_geometry_index.get("nameDiagnostics", {}).get("fallbackSceneObjectCount", 0)
                if isinstance(world_geometry_index.get("nameDiagnostics"), dict)
                else 0
            ),
            "targetCandidatesExist": target_candidates_path.exists() and target_candidates_index_path.exists(),
            "targetCandidateCount": (
                target_candidates_index.get("candidateCount")
                if isinstance(target_candidates_index.get("candidateCount"), int)
                else count_jsonl(target_candidates_path)
            ),
            "targetCandidatesGeneratedAtUtc": target_candidates_index.get("generatedAtUtc"),
            "targetHandoffExists": target_handoff_jsonl_path.exists() and target_handoff_index_path.exists(),
            "targetHandoffCandidateCount": (
                target_handoff_index.get("selectedCandidateCount")
                if isinstance(target_handoff_index.get("selectedCandidateCount"), int)
                else count_jsonl(target_handoff_jsonl_path)
            ),
            "targetHandoffGeneratedAtUtc": target_handoff_index.get("generatedAtUtc"),
            "scenarioDatasetExists": bool(
                scenario_index_path.exists()
                and scenario_dataset_path is not None
                and scenario_dataset_path.exists()
            ),
            "scenarioDatasetType": scenario_type,
            "scenarioDatasetRecordCount": (
                scenario_index.get("scenarioRecordCount")
                if isinstance(scenario_index.get("scenarioRecordCount"), int)
                else count_jsonl(scenario_dataset_path) if scenario_dataset_path is not None else 0
            ),
            "scenarioSelectedCandidateCount": (
                scenario_index.get("selectedCandidateCount")
                if isinstance(scenario_index.get("selectedCandidateCount"), int)
                else 0
            ),
            "scenarioContextTargetCount": (
                scenario_index.get("contextTargetCount")
                if isinstance(scenario_index.get("contextTargetCount"), int)
                else 0
            ),
            "scenarioGeneratedAtUtc": scenario_index.get("generatedAtUtc"),
            "curatedManifestExists": curated_manifest_path.exists(),
            "curatedManifestExampleCount": count_jsonl(curated_manifest_path),
            "curatedGeneratedAtUtc": curated_index.get("generatedAtUtc"),
            "curatedSplitCounts": curated_index.get("splitCounts") if isinstance(curated_index.get("splitCounts"), dict) else {},
            "sessionTabProfileCount": len(session_tab_profiles),
        }
    )
    tab_profiles = session_tab_profiles if session_tab_profiles else default_tab_profiles
    tab_profile_names = profile_names(tab_profiles)
    labeled_tabs = label_active_tabs(labels_doc)
    missing_profiles = sorted(tab for tab in labeled_tabs if tab not in tab_profile_names)

    if not status["defaultProfileExists"]:
        status["warnings"].append("missing default profile")

    if not status["sessionProfileExists"]:
        status["warnings"].append("missing session profile")

    if status["labelCount"] == 0:
        status["warnings"].append("no tab label ranges loaded")

    counts = status["activeTabCounts"]

    if counts and all(canonical_tab_profile_key(tab) == "unknown" for tab in counts.keys()):
        status["warnings"].append("all activeTab counts are unknown")

    if status["trainingManifestExampleCount"] == 0:
        status["warnings"].append("no training data yet")

    if status["manifestFrameCount"] and status["frameFileCount"] == 0:
        status["warnings"].append("manifest reports captured frames, but no retained frame files are on disk")
    elif (
        isinstance(status["manifestFrameCount"], int)
        and status["manifestFrameCount"] > status["frameFileCount"]
        and status["frameFileCount"] > 0
    ):
        status["warnings"].append("only a subset of captured frames is currently retained on disk")

    if not status["uiGeometryExists"]:
        status["warnings"].append("no UI target geometry yet")
    elif status["frameFileCount"] and status["uiGeometryFrameOverlap"] is False:
        status["warnings"].append(
            "UI target geometry has no overlap with retained frames; rebuild with: "
            "python telemetry-viewer\\build_ui_target_geometry.py --latest-with-frames 100 --include-base-regions"
        )

    if not status["worldGeometryExists"]:
        status["warnings"].append("no world target geometry yet")
    elif status["frameFileCount"] and status["worldGeometryFrameOverlap"] is False:
        status["warnings"].append(
            "world target geometry has no overlap with retained frames; rebuild with: "
            "python telemetry-viewer\\build_world_target_geometry.py --latest-with-frames 100"
        )

    if status["worldUnclassifiedSceneObjectCount"]:
        status["warnings"].append(
            f"{status['worldUnclassifiedSceneObjectCount']} world scene object records are unclassified"
        )

    if status["targetCandidateCount"] and not status["scenarioDatasetExists"]:
        status["warnings"].append("no scenario dataset yet")

    if status["trainingManifestExampleCount"] and not status["curatedManifestExists"]:
        status["warnings"].append("no curated training manifest yet")

    for profile_name in missing_profiles:
        status["warnings"].append(f"label activeTab has no matching tab profile: {profile_name}")

    return status


def print_human(status: dict) -> None:
    print("Dataset Status")
    print(f"  session: {status['sessionPath'] or 'none'}")
    print(f"  default profile exists: {'yes' if status['defaultProfileExists'] else 'no'}")
    print(f"  session profile exists: {'yes' if status['sessionProfileExists'] else 'no'}")
    print(f"  labels file exists: {'yes' if status['labelsFileExists'] else 'no'}")
    print(f"  label count: {status['labelCount']}")
    print(f"  target overrides exist: {'yes' if status['targetOverridesExist'] else 'no'}")
    print(f"  target override scene objects: {status['targetOverrideSceneObjectCount']}")
    print(f"  target override ground items: {status['targetOverrideGroundItemCount']}")
    print(f"  target override NPCs: {status['targetOverrideNpcCount']}")
    print(f"  default tab profiles: {status['defaultTabProfileCount']}")
    print(f"  session tab profiles: {status['sessionTabProfileCount']}")
    print("  activeTab counts:")

    if status["activeTabCounts"]:
        for active_tab, count in status["activeTabCounts"].items():
            print(f"    {active_tab}: {count}")
    else:
        print("    unavailable")

    print(f"  frame files on disk: {status['frameFileCount']}")
    print(f"  retained frame tick range: {status['firstFrameFileTick'] or 'none'}-{status['lastFrameFileTick'] or 'none'}")
    print(f"  latest frame file: {status['latestFrameFilePath'] or 'none'}")
    print(f"  frame index records: {status['frameIndexRecordCount']}")
    print(f"  manifest frameCount: {status['manifestFrameCount'] if status['manifestFrameCount'] is not None else 'none'}")
    print(f"  manifest deletedFrameCount: {status['manifestDeletedFrameCount'] if status['manifestDeletedFrameCount'] is not None else 'none'}")
    print(f"  manifest droppedFrameCount: {status['manifestDroppedFrameCount'] if status['manifestDroppedFrameCount'] is not None else 'none'}")
    print(f"  manifest screenshotEveryTicks: {status['manifestScreenshotEveryTicks'] if status['manifestScreenshotEveryTicks'] is not None else 'none'}")
    print(f"  manifest frameCaptureMode: {status['manifestFrameCaptureMode'] or 'none'}")
    print(f"  manifest maxFrameStorageMb: {status['manifestMaxFrameStorageMb'] if status['manifestMaxFrameStorageMb'] is not None else 'none'}")
    print(f"  latest test crop run: {status['latestTestCropRun'] or 'none'}")
    print(f"  training manifest examples: {status['trainingManifestExampleCount']}")
    print(f"  training crop files: {status['trainingCropCount']}")
    print(f"  UI target geometry exists: {'yes' if status['uiGeometryExists'] else 'no'}")
    print(f"  UI target records: {status['uiTargetRecordCount']}")
    print(f"  UI geometry tick range: {status['uiGeometryFirstTick'] or 'none'}-{status['uiGeometryLastTick'] or 'none'}")
    print(f"  UI/retained frame overlap: {status['uiGeometryFrameOverlap'] if status['uiGeometryFrameOverlap'] is not None else 'unknown'}")
    print(f"  UI geometry generatedAtUtc: {status['uiGeometryGeneratedAtUtc'] or 'none'}")
    print(f"  world target geometry exists: {'yes' if status['worldGeometryExists'] else 'no'}")
    print(f"  world target records: {status['worldTargetRecordCount']}")
    print(f"  world geometry tick range: {status['worldGeometryFirstTick'] or 'none'}-{status['worldGeometryLastTick'] or 'none'}")
    print(f"  world/retained frame overlap: {status['worldGeometryFrameOverlap'] if status['worldGeometryFrameOverlap'] is not None else 'unknown'}")
    print(f"  world geometry generatedAtUtc: {status['worldGeometryGeneratedAtUtc'] or 'none'}")
    print(f"  world geometry source schema: {status['worldGeometrySourceSchema'] or 'unknown'}")
    print(f"  world objectKey support: {'yes' if status['worldObjectKeySupport'] else 'no'}")
    print(f"  scene static index exists: {'yes' if status['sceneStaticIndexExists'] else 'no'}")
    print(f"  scene static index records: {status['sceneStaticIndexRecordCount']}")
    print(f"  world target roles: {json.dumps(status['worldTargetRoleCounts'], sort_keys=True)}")
    print(f"  world target categories: {json.dumps(status['worldTargetCategoryCounts'], sort_keys=True)}")
    print(f"  world top target tags: {json.dumps(status['worldTopTargetTags'], sort_keys=True)}")
    print(f"  world unclassified scene objects: {status['worldUnclassifiedSceneObjectCount']}")
    print(f"  world fallback scene objects: {status['worldFallbackSceneObjectCount']}")
    print(f"  target geometry inspector: {status['targetGeometryInspectorCommand']}")
    print(f"  target override suggestions: {status['targetOverrideSuggestionCommand']}")
    print(f"  target candidates exist: {'yes' if status['targetCandidatesExist'] else 'no'}")
    print(f"  target candidate count: {status['targetCandidateCount']}")
    print(f"  target candidates generatedAtUtc: {status['targetCandidatesGeneratedAtUtc'] or 'none'}")
    print(f"  target handoff exists: {'yes' if status['targetHandoffExists'] else 'no'}")
    print(f"  target handoff candidate count: {status['targetHandoffCandidateCount']}")
    print(f"  target handoff generatedAtUtc: {status['targetHandoffGeneratedAtUtc'] or 'none'}")
    print(f"  scenario dataset exists: {'yes' if status['scenarioDatasetExists'] else 'no'}")
    print(f"  scenario dataset type: {status['scenarioDatasetType'] or 'none'}")
    print(f"  scenario records: {status['scenarioDatasetRecordCount']}")
    print(f"  scenario selected candidates: {status['scenarioSelectedCandidateCount']}")
    print(f"  scenario context targets: {status['scenarioContextTargetCount']}")
    print(f"  scenario generatedAtUtc: {status['scenarioGeneratedAtUtc'] or 'none'}")
    print(f"  scenario inspector: {status['scenarioInspectorCommand']}")
    print(f"  curated manifest exists: {'yes' if status['curatedManifestExists'] else 'no'}")
    print(f"  curated examples: {status['curatedManifestExampleCount']}")
    print(f"  curated generatedAtUtc: {status['curatedGeneratedAtUtc'] or 'none'}")
    print("  curated split counts:")

    if status["curatedSplitCounts"]:
        for split_name, count in status["curatedSplitCounts"].items():
            print(f"    {split_name}: {count}")
    else:
        print("    unavailable")

    if status["warnings"] or status["labelWarnings"]:
        print("  warnings:")

        for warning in status["warnings"]:
            print(f"    - {warning}")

        for warning in status["labelWarnings"]:
            print(f"    - {warning}")
    else:
        print("  warnings: none")


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only status report for derived OSRS telemetry datasets.")
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    status = status_for_session(session)

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print_human(status)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
