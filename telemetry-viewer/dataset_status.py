import argparse
import json
from pathlib import Path

from label_ranges import load_label_ranges
from tab_profile_names import canonical_tab_profile_key
from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "calibration_profiles" / "default_screen_regions.json"
DEFAULT_LABELS_PATH = Path(__file__).resolve().with_name("tab_labels.json")


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
        "activeTabCounts": {},
        "latestTestCropRun": None,
        "trainingManifestExampleCount": 0,
        "trainingCropCount": 0,
        "defaultTabProfileCount": len(default_tab_profiles),
        "sessionTabProfileCount": 0,
        "warnings": [],
    }

    if session is None:
        status["warnings"].append("no telemetry session found")
        return status

    perception_dir = session / "perception"
    training_dir = session / "training_data"
    session_profile_path = perception_dir / "screen_regions.json"
    session_profile = safe_read_json(session_profile_path)
    _session_base_regions, session_tab_profiles = region_doc_model(session_profile)
    perception_index = safe_read_json(perception_dir / "perception_index.json")
    latest_test_crop_run = latest_directory(perception_dir / "test_crops")
    training_manifest_path = training_dir / "training_manifest.jsonl"
    training_crops_path = training_dir / "crops"

    status.update(
        {
            "sessionProfileExists": session_profile_path.exists(),
            "sessionProfilePath": str(session_profile_path),
            "activeTabCounts": active_tab_counts(perception_index),
            "latestTestCropRun": str(latest_test_crop_run) if latest_test_crop_run else None,
            "trainingManifestExampleCount": count_jsonl(training_manifest_path),
            "trainingCropCount": count_files(training_crops_path),
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
    print(f"  default tab profiles: {status['defaultTabProfileCount']}")
    print(f"  session tab profiles: {status['sessionTabProfileCount']}")
    print("  activeTab counts:")

    if status["activeTabCounts"]:
        for active_tab, count in status["activeTabCounts"].items():
            print(f"    {active_tab}: {count}")
    else:
        print("    unavailable")

    print(f"  latest test crop run: {status['latestTestCropRun'] or 'none'}")
    print(f"  training manifest examples: {status['trainingManifestExampleCount']}")
    print(f"  training crop files: {status['trainingCropCount']}")

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
