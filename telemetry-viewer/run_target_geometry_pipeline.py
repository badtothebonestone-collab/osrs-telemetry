import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, list_tick_files, raw_recording_unavailable_message


VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = VIEWER_DIR.parent


@dataclass
class Step:
    name: str
    command: list[str]
    skipped: bool = False
    exit_code: int | None = None
    optional_failure: bool = False
    continued_after_failure: bool = False


def quote_command(command: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def script_path(name: str) -> str:
    return str(VIEWER_DIR / name)


def ui_targets_path(session: Path) -> Path:
    return session / "interaction_geometry" / "ui_targets.jsonl"


def ui_input_status(session: Path) -> dict[str, bool]:
    perception_dir = session / "perception"
    return {
        "tickBundles": (perception_dir / "tick_bundles.jsonl").exists(),
        "screenRegions": (perception_dir / "screen_regions.json").exists(),
    }


def print_ui_recovery_commands(session: Path) -> None:
    print("UI geometry can be built later with:")
    print("  python telemetry-viewer\\build_perception_dataset.py")
    print("  python telemetry-viewer\\calibrate_screen_regions.py --interactive --latest-existing-frame --port 8770")
    print(
        f'  python telemetry-viewer\\build_ui_target_geometry.py --session "{session}" '
        "--latest-with-frames 25 --include-base-regions --include-all-tab-profiles"
    )


def warn_ui_blocking_unavailable(session: Path) -> None:
    if ui_targets_path(session).exists():
        return

    print(
        "warning: UI-blocked filtering requested but ui_targets.jsonl is unavailable; "
        "either build UI targets or omit --exclude-ui-blocked."
    )


def resolve_session(args) -> Path:
    if args.session:
        session = Path(args.session).expanduser()
        if not session.exists():
            raise RuntimeError(f"Session does not exist: {session}")
        return session.resolve()

    if not args.latest_session:
        raise RuntimeError("Pass --session explicitly, or pass --latest-session to use the newest session.")

    session = find_newest_session(get_sessions_dir(args.sessions_dir))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")

    return session.resolve()


def selection_for_world_or_ui(args, *, ui: bool = False) -> list[str]:
    if args.tick is not None:
        return ["--range", str(args.tick), str(args.tick)]

    if args.tick_range is not None:
        start, end = args.tick_range
        return ["--range", str(start), str(end)]

    if args.latest_with_frames is not None:
        return ["--latest-with-frames", str(args.latest_with_frames)]

    if args.latest is not None:
        if ui:
            return ["--latest-with-frames", str(args.latest)]
        return ["--latest", str(args.latest)]

    return []


def selection_for_report(args) -> list[str]:
    if args.tick is not None:
        return ["--tick", str(args.tick)]

    if args.tick_range is not None:
        start, end = args.tick_range
        return ["--range", str(start), str(end)]

    if args.latest_with_frames is not None:
        return ["--latest", str(args.latest_with_frames)]

    if args.latest is not None:
        return ["--latest", str(args.latest)]

    return []


def build_steps(args, session: Path) -> list[Step]:
    session_args = ["--session", str(session)]
    steps: list[Step] = []

    if args.skip_world:
        steps.append(Step("Build world target geometry", [], skipped=True))
    else:
        steps.append(
            Step(
                "Build world target geometry",
                [
                    sys.executable,
                    script_path("build_world_target_geometry.py"),
                    *session_args,
                    *selection_for_world_or_ui(args),
                    "--target-type",
                    args.target_type,
                ],
            )
        )

    if args.ui_mode == "skip":
        steps.append(Step("Build UI target geometry", [], skipped=True))
    else:
        steps.append(
            Step(
                "Build UI target geometry",
                [
                    sys.executable,
                    script_path("build_ui_target_geometry.py"),
                    *session_args,
                    *selection_for_world_or_ui(args, ui=True),
                    "--include-base-regions",
                    "--include-all-tab-profiles",
                ],
                optional_failure=args.ui_mode == "optional",
            )
        )

    if args.skip_candidates:
        steps.append(Step("Select target candidates", [], skipped=True))
    else:
        command = [
            sys.executable,
            script_path("select_target_candidates.py"),
            *session_args,
            *selection_for_report(args),
            "--target-type",
            args.target_type,
            "--profile",
            args.profile,
            "--limit",
            str(args.limit),
            "--summary",
        ]

        if args.exclude_ui_blocked:
            command.append("--exclude-ui-blocked")

        steps.append(Step("Select target candidates", command))

    if args.skip_diagnostic:
        steps.append(Step("Run target coverage diagnostic", [], skipped=True))
    else:
        steps.append(
            Step(
                "Run target coverage diagnostic",
                [
                    sys.executable,
                    script_path("diagnose_target_coverage.py"),
                    *session_args,
                    *selection_for_report(args),
                    "--project-root",
                    str(PROJECT_ROOT),
                ],
            )
        )

    if args.skip_quality_summary:
        steps.append(Step("Summarize candidate quality", [], skipped=True))
    else:
        steps.append(
            Step(
                "Summarize candidate quality",
                [
                    sys.executable,
                    script_path("summarize_candidate_quality.py"),
                    *session_args,
                    *selection_for_report(args),
                    "--profile",
                    args.profile,
                    "--limit",
                    str(args.limit),
                ],
            )
        )

    if args.open_inspector:
        steps.append(
            Step(
                "Open target geometry inspector",
                [
                    sys.executable,
                    script_path("target_geometry_inspector.py"),
                    *session_args,
                ],
            )
        )

    return steps


def print_step_plan(steps: list[Step], dry_run: bool) -> None:
    print("pipeline steps:")

    for index, step in enumerate(steps, start=1):
        if step.skipped:
            print(f"  {index}. {step.name}: skipped")
            continue

        prefix = "dry-run" if dry_run else "run"
        suffix = " (optional)" if step.optional_failure else ""
        print(f"  {index}. {step.name}{suffix}:")
        print(f"     {prefix}: {quote_command(step.command)}")


def run_steps(steps: list[Step], dry_run: bool, session: Path, args) -> int:
    if dry_run:
        print("dry run: no commands executed")
        return 0

    for step in steps:
        if step.skipped:
            continue

        if step.name == "Select target candidates" and args.exclude_ui_blocked:
            warn_ui_blocking_unavailable(session)

        print()
        print(f"== {step.name} ==")
        print(quote_command(step.command))
        completed = subprocess.run(step.command)
        step.exit_code = completed.returncode

        if completed.returncode != 0:
            if step.optional_failure:
                step.continued_after_failure = True
                print(f"warning: optional step failed: {step.name} exit={completed.returncode}")

                status = ui_input_status(session)
                if not status["tickBundles"] or not status["screenRegions"]:
                    print("warning: UI perception/calibration inputs appear missing or incomplete.")
                else:
                    print("warning: UI target geometry was not refreshed.")

                print_ui_recovery_commands(session)
                continue

            print(f"step failed: {step.name} exit={completed.returncode}")
            return completed.returncode

    return 0


def print_summary(steps: list[Step], exit_code: int, dry_run: bool) -> None:
    print()
    print("pipeline summary:")

    for step in steps:
        if step.skipped:
            status = "skipped"
        elif dry_run:
            status = "dry-run"
        elif step.continued_after_failure:
            status = f"optional warning exit={step.exit_code}"
        else:
            status = f"exit={step.exit_code if step.exit_code is not None else 'not-run'}"

        print(f"  {step.name}: {status}")

    print(f"overall: {'success' if exit_code == 0 else 'failed'}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the local read-only target geometry QA pipeline in order. "
            "This orchestrates existing Python tools and does not interact with RuneLite."
        )
    )
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session when --session is omitted.")
    parser.add_argument("--tick", type=int, help="Process one tick.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--latest", type=int, metavar="N", help="Use the latest N ticks.")
    parser.add_argument("--latest-with-frames", type=int, metavar="N", help="Use the latest N ticks with retained frame files for geometry builders.")
    parser.add_argument("--target-type", default="all", help="Target type passed to world/candidate builders. Default: all.")
    parser.add_argument(
        "--profile",
        default="broad_qa",
        choices=["broad_qa", "woodcutting", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa"],
        help="Candidate profile. Default: broad_qa.",
    )
    parser.add_argument("--limit", type=int, default=500, help="Candidate limit. Default: 500.")
    parser.add_argument("--exclude-ui-blocked", action="store_true", help="Exclude candidate aim points that land inside known UI regions.")
    parser.add_argument(
        "--ui-mode",
        choices=["required", "optional", "skip"],
        default="optional",
        help="UI target build behavior: required fails the pipeline, optional warns and continues, skip omits the UI step. Default: optional.",
    )
    parser.add_argument("--skip-world", action="store_true", help="Skip build_world_target_geometry.py.")
    parser.add_argument("--skip-ui", action="store_true", help="Alias for --ui-mode skip.")
    parser.add_argument("--skip-candidates", action="store_true", help="Skip select_target_candidates.py.")
    parser.add_argument("--skip-diagnostic", action="store_true", help="Skip diagnose_target_coverage.py.")
    parser.add_argument("--skip-quality-summary", action="store_true", help="Skip summarize_candidate_quality.py.")
    parser.add_argument("--open-inspector", action="store_true", help="Run target_geometry_inspector.py as the final step.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    args = parser.parse_args()

    selectors = [
        args.tick is not None,
        args.tick_range is not None,
        args.latest is not None,
        args.latest_with_frames is not None,
    ]

    if sum(1 for selected in selectors if selected) > 1:
        parser.error("--tick, --range, --latest, and --latest-with-frames are mutually exclusive")

    if args.tick_range is not None:
        start, end = args.tick_range
        if end < start:
            args.tick_range = (end, start)

    if args.latest is not None and args.latest < 1:
        parser.error("--latest must be positive")

    if args.latest_with_frames is not None and args.latest_with_frames < 1:
        parser.error("--latest-with-frames must be positive")

    if args.limit < 0:
        parser.error("--limit must be zero or positive")

    if args.skip_ui:
        args.ui_mode = "skip"

    return args


def main() -> int:
    args = parse_args()

    try:
        session = resolve_session(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f"selected session: {session}")

    if args.latest_with_frames is not None:
        print(f"tick selection: latest-with-frames {args.latest_with_frames}")
    elif args.latest is not None:
        print(f"tick selection: latest {args.latest}")
    elif args.tick is not None:
        print(f"tick selection: tick {args.tick}")
    elif args.tick_range is not None:
        print(f"tick selection: range {args.tick_range[0]}..{args.tick_range[1]}")
    else:
        print("tick selection: tool defaults")

    print(f"UI mode: {args.ui_mode}")
    if not list_tick_files(session):
        print(f"warning: {raw_recording_unavailable_message(session)}")

    steps = build_steps(args, session)
    print_step_plan(steps, args.dry_run)
    exit_code = run_steps(steps, args.dry_run, session, args)
    print_summary(steps, exit_code, args.dry_run)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
