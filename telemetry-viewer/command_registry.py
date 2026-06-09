from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = "osrs_telemetry_command_registry.v1"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def py(*parts: str) -> str:
    return "python " + " ".join(parts)


def command_entries() -> list[dict[str, Any]]:
    return [
        {
            "id": "start_ui_simple_mode",
            "purpose": "Open the small Record Everything UI.",
            "command": py("telemetry-viewer\\telemetry_ui.py", "--simple"),
            "owner": "telemetry-viewer\\telemetry_ui.py",
        },
        {
            "id": "reset_ui_config",
            "purpose": "Reset the UI to recommended Simple Mode Record Everything defaults.",
            "command": py("telemetry-viewer\\telemetry_ui.py", "--reset-config"),
            "owner": "telemetry-viewer\\telemetry_ui.py",
        },
        {
            "id": "start_telemetry_ui_check",
            "purpose": "Run non-GUI UI/config/command checks.",
            "command": py("telemetry-viewer\\telemetry_ui.py", "--check"),
            "owner": "telemetry-viewer\\telemetry_ui.py",
        },
        {
            "id": "manual_recorder_basic",
            "purpose": "Start a broad manual Record Everything capture.",
            "command": py(
                "telemetry-viewer\\manual_recorder.py",
                "--label",
                "<label>",
                "--latest-session",
                "--prefer-active-session",
                "--summary",
                "--include-raw",
                "--telemetry-preflight",
                "--wait-for-fresh-telemetry",
                "--capture-input",
                "--input-backend",
                "polling",
                "--capture-mouse",
                "--capture-keyboard",
                "--capture-window-context",
                "--join-input-telemetry",
                "--preserve-bank-ui",
                "--preserve-combat-state",
            ),
            "owner": "telemetry-viewer\\manual_recorder.py",
        },
        {
            "id": "analyzer_broad",
            "purpose": "Analyze a Record Everything folder and refresh knowledge indexes.",
            "command": py(
                "telemetry-viewer\\analyze_manual_recording.py",
                "<recording>",
                "--summary",
                "--woodcutting-lifecycle",
                "--woodcutting-loop-lifecycle",
                "--banking-lifecycle",
                "--traversal-lifecycle",
                "--route-monitor",
                "--route-history",
                "--interruption-lifecycle",
                "--combat-damage-summary",
                "--human-click-profile",
                "--update-knowledge",
            ),
            "owner": "telemetry-viewer\\analyze_manual_recording.py",
        },
        {
            "id": "update_knowledge",
            "purpose": "Regenerate repo-owned knowledge docs and machine-readable indexes.",
            "command": py("telemetry-viewer\\update_project_knowledge.py", "--scan-recordings", "--write-docs", "--json"),
            "owner": "telemetry-viewer\\update_project_knowledge.py",
        },
        {
            "id": "validate_template",
            "purpose": "Resolve and validate a route template.",
            "command": py("telemetry-viewer\\route_monitor.py", "--validate-template", "<route-name-or-template>", "--json"),
            "owner": "telemetry-viewer\\route_monitor.py",
        },
        {
            "id": "route_monitor_live_follow",
            "purpose": "Follow live route progress and write route history artifacts.",
            "command": py("telemetry-viewer\\route_monitor.py", "--live", "--follow", "--latest-session", "--template", "<route-name-or-template>", "--json"),
            "owner": "telemetry-viewer\\route_monitor.py",
        },
        {
            "id": "bot_eval_replay",
            "purpose": "Replay/evaluate bot phase decisions against known artifacts.",
            "command": py("telemetry-viewer\\bot_eval_runner.py", "--task", "woodcutting_loop", "--json"),
            "owner": "telemetry-viewer\\bot_eval_runner.py",
        },
        {
            "id": "bot_eval_preflight",
            "purpose": "Check bot live-run wiring without running the bot.",
            "command": py("telemetry-viewer\\bot_eval_runner.py", "--task", "woodcutting_loop", "--preflight", "--json"),
            "owner": "telemetry-viewer\\bot_eval_runner.py",
        },
        {
            "id": "bot_eval_live_action",
            "purpose": "Run guarded real live actions through the executor after readiness passes.",
            "command": py(
                "telemetry-viewer\\bot_eval_runner.py",
                "--task",
                "woodcutting_loop",
                "--live",
                "--execute-actions",
                "--auto-recover-loaded-scene",
                "--record-everything",
                "--analyze-after",
                "--json",
            ),
            "owner": "telemetry-viewer\\bot_eval_runner.py",
        },
        {
            "id": "loaded_scene_recovery",
            "purpose": "Recover or verify a loaded scene through the canonical recovery path.",
            "command": py(
                "telemetry-viewer\\context_service.py",
                "--ensure-loaded-scene",
                "--arduino-port",
                "COM6",
                "--liveness-max-total-seconds",
                "180",
                "--liveness-max-attempts-per-state",
                "3",
            ),
            "owner": "telemetry-viewer\\liveness_recovery_core.py via telemetry-viewer\\context_service.py",
        },
        {
            "id": "mcp_list_tools",
            "purpose": "List read-only MCP tools exposed to Codex.",
            "command": py("telemetry-viewer\\mcp_server.py", "--list-tools"),
            "owner": "telemetry-viewer\\mcp_server.py",
        },
    ]


def check_registry(root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    errors: list[str] = []
    warnings: list[str] = []
    entries = command_entries()
    for entry in entries:
        owner = str(entry.get("owner") or "")
        owner_path = owner.split(" via ", 1)[0]
        if "\\" in owner_path and not (base / owner_path).exists():
            errors.append(f"{entry['id']}: owner missing: {owner_path}")
        command = str(entry.get("command") or "")
        if "dry-run" in command and entry["id"] == "bot_eval_live_action":
            errors.append("bot_eval_live_action must not include dry-run flags")
        if entry["id"] == "loaded_scene_recovery" and "--ensure-loaded-scene" not in command:
            errors.append("loaded_scene_recovery must use context_service.py --ensure-loaded-scene")
        if entry["id"] == "bot_eval_preflight" and "--preflight" not in command:
            errors.append("bot_eval_preflight must use --preflight")
        if "<" in command and ">" in command:
            warnings.append(f"{entry['id']}: command contains placeholders")
    return {
        "schema": "osrs_telemetry_command_registry_check.v1",
        "status": "FAIL" if errors else "WARN" if warnings else "PASS",
        "repoRoot": str(base),
        "commandCount": len(entries),
        "commands": entries,
        "errors": errors,
        "warnings": warnings,
    }


def print_list(entries: list[dict[str, Any]]) -> None:
    print("ID | Purpose | Command")
    print("--- | --- | ---")
    for entry in entries:
        print(f"{entry['id']} | {entry['purpose']} | {entry['command']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List and validate important OSRS telemetry commands.")
    parser.add_argument("--list", action="store_true", help="List known commands.")
    parser.add_argument("--check", action="store_true", help="Validate registry owners and guardrails.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        result = check_registry()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['status']}: commands={result['commandCount']}")
            for error in result.get("errors") or []:
                print(f"ERROR: {error}")
            for warning in result.get("warnings") or []:
                print(f"WARN: {warning}")
        return 0 if result["status"] in {"PASS", "WARN"} else 1
    entries = command_entries()
    if args.json:
        print(json.dumps({"schema": SCHEMA, "commands": entries}, indent=2))
    else:
        print_list(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
