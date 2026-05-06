import argparse
import json
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()
    return find_newest_session(get_sessions_dir(args.sessions_dir))


def live_paths(session: Path) -> dict[str, Path]:
    live_dir = session / "interaction_geometry" / "live"
    return {
        "baseline": live_dir / "live_baseline_state.json",
        "context": live_dir / "live_context_index.json",
        "candidates": live_dir / "live_candidates.jsonl",
        "status": live_dir / "live_status.json",
    }


def read_jsonl(path: Path) -> list[dict]:
    records = []
    if not path.exists():
        return records
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        pass
    return records


def distance_for(candidate: dict) -> float:
    for key in ("targetDistanceChebyshev", "distanceTiles"):
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 999999.0


def nearest_candidate(candidates: list[dict], class_id: str, max_distance: float | None) -> dict | None:
    matches = [
        candidate
        for candidate in candidates
        if str(candidate.get("classId") or candidate.get("targetClass") or "").lower() == class_id.lower()
    ]
    if max_distance is not None:
        matches = [candidate for candidate in matches if distance_for(candidate) <= max_distance]
    return min(matches, key=distance_for) if matches else None


def summary_payload(session: Path, paths: dict[str, Path]) -> dict:
    baseline = safe_read_json(paths["baseline"])
    context = safe_read_json(paths["context"])
    status = safe_read_json(paths["status"])
    candidates = read_jsonl(paths["candidates"])
    return {
        "sessionPath": str(session),
        "liveStatusExists": paths["status"].exists(),
        "baselineExists": paths["baseline"].exists(),
        "contextIndexExists": paths["context"].exists(),
        "candidateCount": len(candidates),
        "latestTick": (status or {}).get("latestTick") if isinstance(status, dict) else None,
        "profile": (status or {}).get("profile") if isinstance(status, dict) else None,
        "candidateCountsByClassId": (context or {}).get("candidateCountsByClassId") if isinstance(context, dict) else None,
        "bestCandidateSummary": (baseline or {}).get("candidates", {}).get("bestCandidateSummary") if isinstance(baseline, dict) else None,
    }


def print_human(data) -> None:
    print("Live Context Query")
    for key, value in data.items():
        print(f"{key}: {value}")


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only helper for querying rolling live target context files.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --session is omitted.")
    parser.add_argument("--summary", action="store_true", help="Print a compact live context summary.")
    parser.add_argument("--nearest", help="Return nearest candidate for a class id, such as tree or npc.")
    parser.add_argument("--max-distance", type=float, help="Maximum candidate Chebyshev tile distance for --nearest.")
    parser.add_argument("--baseline", action="store_true", help="Return live_baseline_state.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    if session is None:
        print("No telemetry session found.")
        return 1

    paths = live_paths(session)
    payload = None

    if args.baseline:
        payload = safe_read_json(paths["baseline"]) or {}
    elif args.nearest:
        payload = {
            "sessionPath": str(session),
            "nearest": args.nearest,
            "candidate": nearest_candidate(read_jsonl(paths["candidates"]), args.nearest, args.max_distance),
        }
    else:
        payload = summary_payload(session, paths)

    if args.json:
        print(json_dump_compact(payload))
    else:
        print_human(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
