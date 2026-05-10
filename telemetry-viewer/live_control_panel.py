from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_DIR = PROJECT_ROOT / "telemetry-viewer"
MAX_LOG_LINES = 1000
SAFETY_TEXT = "Read-only telemetry launcher. Starts local tools only. Does not click, type, invoke menus, or execute actions."
PROFILES = ("woodcutting", "broad_qa", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa")
INPUT_SOURCES = ("auto", "compact-packets", "raw-ticks")
LIVENESS_MODES = ("off", "basic", "delta", "full")
WORKFLOW_MODES = ("Normal Live", "Visual QA", "Debug Audit")
SESSION_STALE_SECONDS = 15 * 60
COMPACT_PACKET_STALE_SECONDS = 2 * 60


@dataclass
class LivePanelOptions:
    profile: str = "woodcutting"
    mode: str = "Normal Live"
    input_source: str = "auto"
    liveness_mode: str = "delta"
    window_ticks: int = 10
    limit: int = 100
    port: int = 8890
    interval: float = 1.0
    require_compact_packets: bool = False
    no_ui_targets: bool = True
    benchmark: bool = True
    summary: bool = True


def python_command(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def command_preview(command: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def script_supports_flag(script: Path, flag: str) -> bool:
    try:
        return flag in script.read_text(encoding="utf-8")
    except OSError:
        return False


def build_runelite_command() -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/c", "gradlew.bat", "run"]
    return ["./gradlew", "run"]


def build_check_live_setup_command(require_compact_packets: bool = False) -> list[str]:
    command = python_command("telemetry-viewer\\check_live_setup.py", "--latest-session")
    if require_compact_packets:
        command.append("--require-compact-packets")
    return command


def build_inspect_packets_command() -> list[str]:
    return python_command("telemetry-viewer\\inspect_live_packets.py", "--latest-session", "--summary")


def build_live_processor_command(options: LivePanelOptions, *, supports_liveness: bool = True) -> list[str]:
    input_source = "compact-packets" if options.require_compact_packets else options.input_source
    command = python_command(
        "telemetry-viewer\\live_target_processor.py",
        "--latest-session",
        "--input-source",
        input_source,
    )
    if options.require_compact_packets:
        command.append("--require-compact-packets")
    command.extend(
        [
            "--profile",
            options.profile,
            "--follow",
            "--latency-mode",
            "realtime",
        ]
    )
    if supports_liveness:
        command.extend(["--liveness-mode", options.liveness_mode, "--liveness-budget-ms", "20"])
    command.extend(
        [
            "--no-startup-backfill",
            "--max-new-ticks-per-update",
            "1",
            "--candidate-output-window",
            "latest",
            "--window-ticks",
            str(options.window_ticks),
            "--limit",
            str(options.limit),
        ]
    )
    if options.no_ui_targets:
        command.append("--no-ui-targets")
    command.extend(["--emit-world-targets", "candidates", "--drain-backlog-on-overrun"])
    if options.summary:
        command.append("--summary")
    if options.benchmark:
        command.append("--benchmark")
    return command


def build_context_service_command(port: int) -> list[str]:
    return python_command("telemetry-viewer\\context_service.py", "--latest-session", "--port", str(port))


def build_dashboard_command(interval: float) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--task",
        "woodcutting",
        "--watch-human",
        "--interval",
        str(interval),
        "--events",
        "5",
    )


def build_dashboard_events_command(interval: float) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--task",
        "woodcutting",
        "--watch-human",
        "--interval",
        str(interval),
        "--events",
        "10",
    )


def build_event_timeline_command(events: int = 20) -> list[str]:
    return python_command(
        "telemetry-viewer\\live_context_query.py",
        "--latest-session",
        "--events-only",
        "--events",
        str(events),
    )


def build_mock_brain_command(goal_count: int = 5, *, watch: bool = False, interval: float = 1.0) -> list[str]:
    command = python_command(
        "telemetry-viewer\\mock_brain_rehearsal.py",
        "--task",
        "woodcutting",
        "--goal-count",
        str(goal_count),
        "--human",
    )
    if watch:
        command.extend(["--watch", "--interval", str(interval)])
    return command


def build_debug_audit_command(profile: str = "broad_qa") -> list[str]:
    return python_command(
        "telemetry-viewer\\run_target_geometry_pipeline.py",
        "--latest-session",
        "--latest-with-frames",
        "25",
        "--profile",
        profile,
        "--limit",
        "2000",
        "--open-inspector",
    )


def build_inspector_command(session: Path | None) -> list[str]:
    command = python_command("telemetry-viewer\\target_geometry_inspector.py", "--live")
    if session is not None:
        command.extend(["--session", str(session)])
    return command


def build_context_once_command() -> list[str]:
    return python_command("telemetry-viewer\\live_context_query.py", "--latest-session", "--task", "woodcutting", "--human")


def build_context_request_body(max_candidates: int = 1) -> dict:
    return {
        "schema": "context_request.v1",
        "task": "woodcutting",
        "needs": [
            "baseline",
            "best:tree",
            "nearest:tree",
            "inventory",
            "activity",
            "liveness",
            "navigation_readiness",
            "events",
            "diagnostics",
        ],
        "maxCandidates": max_candidates,
        "maxEvents": 5,
        "responseMode": "compact",
    }


def normal_live_options(profile: str = "woodcutting") -> LivePanelOptions:
    return LivePanelOptions(
        profile=profile,
        mode="Normal Live",
        input_source="compact-packets",
        liveness_mode="delta",
        window_ticks=10,
        limit=100,
        port=8890,
        interval=1.0,
        require_compact_packets=True,
        no_ui_targets=True,
        benchmark=True,
        summary=True,
    )


def build_normal_live_stack_commands(options: LivePanelOptions, *, supports_liveness: bool = True) -> list[tuple[str, list[str], str]]:
    stack_options = LivePanelOptions(
        profile=options.profile,
        mode="Normal Live",
        input_source="compact-packets",
        liveness_mode=options.liveness_mode or "delta",
        window_ticks=options.window_ticks,
        limit=options.limit,
        port=options.port,
        interval=options.interval,
        require_compact_packets=True,
        no_ui_targets=options.no_ui_targets,
        benchmark=options.benchmark,
        summary=options.summary,
    )
    return [
        ("Check Live Setup", build_check_live_setup_command(require_compact_packets=True), "Setup/Packet tools"),
        ("Live Processor", build_live_processor_command(stack_options, supports_liveness=supports_liveness), "Live Processor"),
        ("Context Service", build_context_service_command(stack_options.port), "Context Service"),
        ("Human Dashboard", build_dashboard_command(stack_options.interval), "Dashboard"),
    ]


def safe_load_json(path: Path, previous: dict | None = None) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return previous or {}
        value = json.loads(text)
        return value if isinstance(value, dict) else previous or {}
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return previous or {}


def latest_session_path(sessions_dir: str | None = None) -> Path | None:
    return find_newest_session(get_sessions_dir(sessions_dir))


def _path_age_seconds(path: Path, now: float | None = None) -> float | None:
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - modified)


def compact_packet_status(session: Path | None, *, now: float | None = None, stale_seconds: int = COMPACT_PACKET_STALE_SECONDS) -> dict:
    if session is None:
        return {"available": False, "recent": False, "warning": "No latest session found."}
    packet_dir = session / "live_packets"
    index_path = packet_dir / "live_packet_index.json"
    pointer_path = packet_dir / "latest_segment.txt"
    index = safe_load_json(index_path)
    latest_segment = index.get("activeSegment") or index.get("latestSegment")
    if not latest_segment and pointer_path.exists():
        try:
            latest_segment = pointer_path.read_text(encoding="utf-8").strip()
        except OSError:
            latest_segment = None
    segment_path = packet_dir / Path(str(latest_segment)).name if latest_segment else None
    available = bool(packet_dir.exists() and index and latest_segment and segment_path and segment_path.exists())
    ages = [
        age
        for age in (
            _path_age_seconds(index_path, now),
            _path_age_seconds(pointer_path, now),
            _path_age_seconds(segment_path, now) if segment_path else None,
        )
        if age is not None
    ]
    age_seconds = min(ages) if ages else None
    recent = bool(available and age_seconds is not None and age_seconds <= stale_seconds)
    warning = ""
    if not available:
        warning = "Compact packets are not available yet."
    elif not recent:
        warning = f"Compact packets look stale ({int(age_seconds or 0)}s old)."
    return {
        "available": available,
        "recent": recent,
        "warning": warning,
        "indexPath": str(index_path),
        "latestSegment": str(segment_path) if segment_path else None,
        "latestTick": index.get("latestTick"),
        "latestSequence": index.get("latestSequence"),
        "ageSeconds": age_seconds,
    }


def stale_session_warning(
    session: Path | None,
    *,
    now: float | None = None,
    max_session_age_seconds: int = SESSION_STALE_SECONDS,
    max_packet_age_seconds: int = COMPACT_PACKET_STALE_SECONDS,
) -> str:
    if session is None:
        return "No latest session found. Start RuneLite dev and collect compact packets."
    packet = compact_packet_status(session, now=now, stale_seconds=max_packet_age_seconds)
    if not packet["available"]:
        return "Waiting for compact packets. Enable compact live packets in RuneLite config if this persists."
    if not packet["recent"]:
        return packet["warning"]
    activity_ages = [
        age
        for age in (
            _path_age_seconds(session, now),
            packet.get("ageSeconds"),
        )
        if isinstance(age, (int, float))
    ]
    session_age = min(activity_ages) if activity_ages else None
    if session_age is not None and session_age > max_session_age_seconds:
        minutes = int(session_age // 60)
        return f"Latest session folder is {minutes} minutes old; confirm this is the intended session."
    return ""


def status_snapshot(session: Path | None, previous: dict | None = None) -> dict:
    if session is None:
        return previous or {}
    previous = previous or {}
    live_dir = session / "interaction_geometry" / "live"
    status = safe_load_json(live_dir / "live_status.json", previous.get("status") if isinstance(previous.get("status"), dict) else None)
    performance = safe_load_json(
        live_dir / "live_performance_summary.json",
        previous.get("performance") if isinstance(previous.get("performance"), dict) else None,
    )
    context = safe_load_json(
        live_dir / "live_context_index.json",
        previous.get("context") if isinstance(previous.get("context"), dict) else None,
    )
    packet_index = safe_load_json(
        session / "live_packets" / "live_packet_index.json",
        previous.get("packetIndex") if isinstance(previous.get("packetIndex"), dict) else None,
    )
    overlay = safe_load_json(
        live_dir / "overlay_debug_state.json",
        previous.get("overlayDebug") if isinstance(previous.get("overlayDebug"), dict) else None,
    )
    manifest = safe_load_json(
        session / "manifest.json",
        previous.get("manifest") if isinstance(previous.get("manifest"), dict) else None,
    )
    return {
        "status": status,
        "performance": performance,
        "context": context,
        "packetIndex": packet_index,
        "overlayDebug": overlay,
        "manifest": manifest,
        "latestTick": status.get("latestTickProcessed") or status.get("latestTick") or context.get("latestTick") or packet_index.get("latestTick"),
        "inputSourceActive": status.get("inputSourceActive"),
        "candidateCount": status.get("candidateCount"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "compactPacketsAvailable": status.get("compactPacketsAvailable") or bool(packet_index),
        "latestSegment": status.get("compactPacketLatestSegment") or packet_index.get("activeSegment") or packet_index.get("latestSegment"),
        "recordingMode": status.get("recordingMode") or manifest.get("recordingMode"),
        "rawTickRecordingEnabled": status.get("rawTickRecordingEnabled") if status.get("rawTickRecordingEnabled") is not None else manifest.get("rawTickRecordingEnabled"),
        "frameRecordingEnabled": status.get("frameRecordingEnabled") if status.get("frameRecordingEnabled") is not None else manifest.get("frameRecordingEnabled"),
        "latestEventSummary": overlay.get("latestEventSummary"),
        "latestEventTick": overlay.get("latestEventTick"),
    }


class ManagedProcess:
    def __init__(self, name: str, command: list[str], log_name: str, process: subprocess.Popen):
        self.name = name
        self.command = command
        self.log_name = log_name
        self.process = process
        self.started_at = datetime.now()
        self.exit_code: int | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def running(self) -> bool:
        code = self.process.poll()
        if code is not None:
            self.exit_code = code
            return False
        return True


class LiveControlPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OSRS Telemetry Live Control Panel")
        self.root.geometry("1180x780")
        self.latest_session: Path | None = latest_session_path()
        self.previous_snapshot: dict = {}
        self.processes: dict[str, ManagedProcess] = {}
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.context_poll_inflight = False
        self.log_widgets: dict[str, tk.Text] = {}
        self.context_status_var = tk.StringVar(value="context: unknown")
        self.session_var = tk.StringVar(value=str(self.latest_session) if self.latest_session else "No session found")
        self.packet_status_var = tk.StringVar(value="compact packets: unknown")
        self.latest_tick_var = tk.StringVar(value="latest tick: unknown")
        self.mode_var = tk.StringVar(value="Normal Live")
        self.profile_var = tk.StringVar(value="woodcutting")
        self.input_source_var = tk.StringVar(value="auto")
        self.liveness_var = tk.StringVar(value="delta")
        self.window_ticks_var = tk.StringVar(value="10")
        self.limit_var = tk.StringVar(value="100")
        self.port_var = tk.StringVar(value="8890")
        self.interval_var = tk.StringVar(value="1")
        self.require_compact_var = tk.BooleanVar(value=False)
        self.no_ui_targets_var = tk.BooleanVar(value=True)
        self.benchmark_var = tk.BooleanVar(value=True)
        self.summary_var = tk.BooleanVar(value=True)
        self.open_inspector_var = tk.BooleanVar(value=False)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_latest_session(log=False)
        self.root.after(100, self.process_log_queue)
        self.root.after(1000, self.poll_status)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text=SAFETY_TEXT, foreground="#064e3b").pack(anchor=tk.W, pady=(0, 8))

        top = ttk.Frame(outer)
        top.pack(fill=tk.X)
        session_frame = ttk.LabelFrame(top, text="Session", padding=8)
        session_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(session_frame, textvariable=self.session_var, wraplength=760).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        ttk.Button(session_frame, text="Refresh latest session", command=self.refresh_latest_session).grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Button(session_frame, text="Open session folder", command=self.open_session_folder).grid(row=1, column=1, sticky=tk.W, pady=(6, 0), padx=(6, 0))
        ttk.Label(session_frame, textvariable=self.packet_status_var).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))
        ttk.Label(session_frame, textvariable=self.latest_tick_var).grid(row=3, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(session_frame, textvariable=self.context_status_var).grid(row=3, column=2, columnspan=2, sticky=tk.W)
        session_frame.columnconfigure(3, weight=1)

        options = ttk.LabelFrame(top, text="Options", padding=8)
        options.pack(side=tk.RIGHT, fill=tk.X)
        self._option_row(options, 0, "Mode", ttk.Combobox(options, textvariable=self.mode_var, values=WORKFLOW_MODES, width=18, state="readonly"))
        self._option_row(options, 1, "Profile", ttk.Combobox(options, textvariable=self.profile_var, values=PROFILES, width=18, state="readonly"))
        self._option_row(options, 2, "Input source", ttk.Combobox(options, textvariable=self.input_source_var, values=INPUT_SOURCES, width=18, state="readonly"))
        self._option_row(options, 3, "Liveness", ttk.Combobox(options, textvariable=self.liveness_var, values=LIVENESS_MODES, width=18, state="readonly"))
        self._entry_row(options, 4, "Window ticks", self.window_ticks_var)
        self._entry_row(options, 5, "Limit", self.limit_var)
        self._entry_row(options, 6, "Port", self.port_var)
        self._entry_row(options, 7, "Dashboard interval", self.interval_var)
        ttk.Checkbutton(options, text="Require compact packets", variable=self.require_compact_var).grid(row=8, column=0, columnspan=2, sticky=tk.W)
        ttk.Checkbutton(options, text="No UI targets", variable=self.no_ui_targets_var).grid(row=9, column=0, columnspan=2, sticky=tk.W)
        ttk.Checkbutton(options, text="Summary", variable=self.summary_var).grid(row=10, column=0, sticky=tk.W)
        ttk.Checkbutton(options, text="Benchmark", variable=self.benchmark_var).grid(row=10, column=1, sticky=tk.W)
        ttk.Checkbutton(options, text="Open inspector URL", variable=self.open_inspector_var).grid(row=11, column=0, columnspan=2, sticky=tk.W)

        button_frame = ttk.LabelFrame(outer, text="Start / Stop", padding=8)
        button_frame.pack(fill=tk.X, pady=8)
        buttons = [
            ("Start Normal Live Stack", self.start_normal_live_stack),
            ("Restart Live Stack", self.restart_live_stack),
            ("Start RuneLite Dev", self.start_runelite),
            ("Check Live Setup", self.check_live_setup),
            ("Inspect Compact Packets", self.inspect_packets),
            ("Start Live Processor", self.start_live_processor),
            ("Start Context Service", self.start_context_service),
            ("Start Human Dashboard", self.start_dashboard),
            ("Human Dashboard with Events", self.start_dashboard_events),
            ("Event Timeline", self.start_event_timeline),
            ("Start Mock Brain Rehearsal", self.start_mock_brain),
            ("Start Live Inspector", self.start_inspector),
            ("Debug Audit Tools", self.start_debug_audit_tools),
            ("Request Context Once", self.context_once),
            ("Health Check", self.health_check),
            ("Stop Selected", self.stop_selected),
            ("Stop All", self.stop_all),
            ("Clear Log", self.clear_current_log),
        ]
        for index, (label, command) in enumerate(buttons):
            ttk.Button(button_frame, text=label, command=command).grid(row=index // 6, column=index % 6, sticky=tk.EW, padx=3, pady=3)
        for column in range(6):
            button_frame.columnconfigure(column, weight=1)

        middle = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        middle.pack(fill=tk.BOTH, expand=True)

        process_frame = ttk.LabelFrame(middle, text="Processes", padding=6)
        self.process_tree = ttk.Treeview(process_frame, columns=("status", "pid", "started", "exit"), show="tree headings", height=7)
        self.process_tree.heading("#0", text="Name")
        self.process_tree.heading("status", text="Status")
        self.process_tree.heading("pid", text="PID")
        self.process_tree.heading("started", text="Start time")
        self.process_tree.heading("exit", text="Exit code")
        self.process_tree.pack(fill=tk.BOTH, expand=True)
        middle.add(process_frame, weight=1)

        log_frame = ttk.LabelFrame(middle, text="Logs", padding=6)
        self.notebook = ttk.Notebook(log_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        for name in ("Live Processor", "Context Service", "Dashboard", "Inspector", "Setup/Packet tools", "RuneLite"):
            self._add_log_tab(name)
        middle.add(log_frame, weight=4)

    def _option_row(self, parent: ttk.Frame, row: int, label: str, widget: ttk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=2)

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
        ttk.Entry(parent, textvariable=variable, width=20).grid(row=row, column=1, sticky=tk.EW, pady=2)

    def _add_log_tab(self, name: str) -> None:
        frame = ttk.Frame(self.notebook)
        text = tk.Text(frame, height=14, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.notebook.add(frame, text=name)
        self.log_widgets[name] = text

    def options(self) -> LivePanelOptions:
        return LivePanelOptions(
            profile=self.profile_var.get(),
            mode=self.mode_var.get(),
            input_source=self.input_source_var.get(),
            liveness_mode=self.liveness_var.get(),
            window_ticks=self._int_var(self.window_ticks_var, 10),
            limit=self._int_var(self.limit_var, 100),
            port=self._int_var(self.port_var, 8890),
            interval=self._float_var(self.interval_var, 1.0),
            require_compact_packets=bool(self.require_compact_var.get()),
            no_ui_targets=bool(self.no_ui_targets_var.get()),
            benchmark=bool(self.benchmark_var.get()),
            summary=bool(self.summary_var.get()),
        )

    def _int_var(self, variable: tk.StringVar, default: int) -> int:
        try:
            return max(0, int(variable.get()))
        except ValueError:
            return default

    def _float_var(self, variable: tk.StringVar, default: float) -> float:
        try:
            return max(0.1, float(variable.get()))
        except ValueError:
            return default

    def refresh_latest_session(self, log: bool = True) -> None:
        self.latest_session = latest_session_path()
        self.session_var.set(str(self.latest_session) if self.latest_session else "No session found")
        if log:
            self.log("Setup/Packet tools", f"Latest session: {self.session_var.get()}")
        self.poll_status()

    def open_session_folder(self) -> None:
        if not self.latest_session:
            self.log("Setup/Packet tools", "No session folder available.")
            return
        try:
            os.startfile(str(self.latest_session))  # type: ignore[attr-defined]
        except OSError as exc:
            self.log("Setup/Packet tools", f"Could not open session folder: {exc}")

    def start_runelite(self) -> None:
        self.start_process("RuneLite Dev", build_runelite_command(), "RuneLite")

    def apply_normal_live_defaults(self) -> None:
        options = normal_live_options(self.profile_var.get())
        self.mode_var.set(options.mode)
        self.input_source_var.set(options.input_source)
        self.liveness_var.set(options.liveness_mode)
        self.window_ticks_var.set(str(options.window_ticks))
        self.limit_var.set(str(options.limit))
        self.port_var.set(str(options.port))
        self.interval_var.set(str(options.interval))
        self.require_compact_var.set(options.require_compact_packets)
        self.no_ui_targets_var.set(options.no_ui_targets)
        self.summary_var.set(options.summary)
        self.benchmark_var.set(options.benchmark)

    def start_normal_live_stack(self) -> None:
        self.apply_normal_live_defaults()
        if stale_session_warning(self.latest_session) and not self.is_process_running("RuneLite Dev"):
            self.start_runelite()
        self.log("Setup/Packet tools", "Starting normal live stack. Waiting for recent compact packets before launching sidecars.")
        threading.Thread(target=self._normal_live_stack_worker, daemon=True).start()

    def _normal_live_stack_worker(self) -> None:
        deadline = time.time() + 90
        last_warning = ""
        while time.time() < deadline:
            session = latest_session_path()
            warning = stale_session_warning(session)
            if session is not None and not warning:
                self.log_queue.put(("Setup/Packet tools", f"Compact packet session ready: {session}"))
                self.root.after(0, self.refresh_latest_session)
                self.root.after(0, self._start_normal_live_sidecars)
                return
            if warning and warning != last_warning:
                last_warning = warning
                self.log_queue.put(("Setup/Packet tools", warning))
            time.sleep(2)
        self.log_queue.put(("Setup/Packet tools", "Compact packets did not become recent before timeout. Running setup check so the missing piece is visible."))
        self.root.after(0, self.check_live_setup)

    def _start_normal_live_sidecars(self) -> None:
        supports_liveness = script_supports_flag(VIEWER_DIR / "live_target_processor.py", "--liveness-mode")
        for name, command, log_name in build_normal_live_stack_commands(self.options(), supports_liveness=supports_liveness):
            self.start_process(name, command, log_name)

    def restart_live_stack(self) -> None:
        for name in ("Live Processor", "Context Service", "Human Dashboard", "Human Dashboard Events", "Event Timeline", "Mock Brain Rehearsal"):
            self.stop_process(name)
        self.root.after(1000, self.start_normal_live_stack)

    def check_live_setup(self) -> None:
        self.start_process("Check Live Setup", build_check_live_setup_command(self.require_compact_var.get()), "Setup/Packet tools")

    def inspect_packets(self) -> None:
        self.start_process("Inspect Compact Packets", build_inspect_packets_command(), "Setup/Packet tools")

    def start_live_processor(self) -> None:
        supports_liveness = script_supports_flag(VIEWER_DIR / "live_target_processor.py", "--liveness-mode")
        command = build_live_processor_command(self.options(), supports_liveness=supports_liveness)
        self.start_process("Live Processor", command, "Live Processor")

    def start_context_service(self) -> None:
        self.start_process("Context Service", build_context_service_command(self.options().port), "Context Service")

    def start_dashboard(self) -> None:
        self.start_process("Human Dashboard", build_dashboard_command(self.options().interval), "Dashboard")

    def start_dashboard_events(self) -> None:
        self.start_process("Human Dashboard Events", build_dashboard_events_command(self.options().interval), "Dashboard")

    def start_event_timeline(self) -> None:
        self.start_process("Event Timeline", build_event_timeline_command(20), "Dashboard")

    def start_mock_brain(self) -> None:
        self.start_process("Mock Brain Rehearsal", build_mock_brain_command(watch=True, interval=self.options().interval), "Dashboard")

    def start_debug_audit_tools(self) -> None:
        self.log("Setup/Packet tools", "Debug audit tools expect DEBUG_RECORDING sessions with raw ticks/frames. Normal live sessions intentionally omit those files.")
        self.start_process("Debug Audit Tools", build_debug_audit_command("broad_qa"), "Setup/Packet tools")

    def start_inspector(self) -> None:
        command = build_inspector_command(self.latest_session)
        self.start_process("Live Inspector", command, "Inspector")
        if self.open_inspector_var.get():
            self.root.after(1000, lambda: webbrowser.open("http://127.0.0.1:8800/"))

    def context_once(self) -> None:
        threading.Thread(target=self._context_once_worker, daemon=True).start()

    def _context_once_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/context"
        body = json.dumps(build_context_request_body()).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        self.log_queue.put(("Setup/Packet tools", f"POST {url}"))
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.log_queue.put(("Setup/Packet tools", format_context_human(payload, compact=True)))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Context service request failed: {exc}"))
            self.log_queue.put(("Setup/Packet tools", "Fallback command: " + command_preview(build_context_once_command())))

    def health_check(self) -> None:
        threading.Thread(target=self._health_check_worker, daemon=True).start()

    def _health_check_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/health"
        self.log_queue.put(("Setup/Packet tools", f"GET {url}"))
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                text = response.read().decode("utf-8")
            self.log_queue.put(("Setup/Packet tools", text))
        except (OSError, urllib.error.URLError) as exc:
            self.log_queue.put(("Setup/Packet tools", f"Health check failed: {exc}"))

    def start_process(self, name: str, command: list[str], log_name: str) -> None:
        existing = self.processes.get(name)
        if existing and existing.running():
            self.log(log_name, f"{name} is already running (PID {existing.pid}).")
            return
        self.log(log_name, "> " + command_preview(command))
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.log(log_name, f"Failed to start {name}: {exc}")
            return
        entry = ManagedProcess(name, command, log_name, process)
        self.processes[name] = entry
        self.update_process_tree()
        threading.Thread(target=self._read_process_output, args=(entry,), daemon=True).start()

    def _read_process_output(self, entry: ManagedProcess) -> None:
        if entry.process.stdout is not None:
            for line in entry.process.stdout:
                self.log_queue.put((entry.log_name, line.rstrip()))
        code = entry.process.wait()
        entry.exit_code = code
        self.log_queue.put((entry.log_name, f"{entry.name} exited with code {code}"))
        self.log_queue.put(("__process__", "update"))

    def stop_selected(self) -> None:
        selected = self.process_tree.selection()
        if not selected:
            return
        for item in selected:
            name = self.process_tree.item(item, "text")
            self.stop_process(name)

    def stop_all(self) -> None:
        for name in list(self.processes):
            self.stop_process(name)

    def stop_process(self, name: str) -> None:
        entry = self.processes.get(name)
        if not entry or not entry.running():
            return
        self.log(entry.log_name, f"Stopping {name} (PID {entry.pid})")
        if os.name == "nt" and entry.pid:
            try:
                subprocess.run(["taskkill", "/PID", str(entry.pid), "/T", "/F"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                entry.process.terminate()
        else:
            entry.process.terminate()
        self.update_process_tree()

    def clear_current_log(self) -> None:
        tab = self.notebook.select()
        for name, widget in self.log_widgets.items():
            if str(widget.master) == tab:
                widget.delete("1.0", tk.END)
                return

    def log(self, log_name: str, message: str) -> None:
        widget = self.log_widgets.get(log_name)
        if widget is None:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        widget.insert(tk.END, f"[{timestamp}] {message}\n")
        line_count = int(float(widget.index("end-1c")))
        if line_count > MAX_LOG_LINES:
            widget.delete("1.0", f"{line_count - MAX_LOG_LINES + 1}.0")
        widget.see(tk.END)

    def process_log_queue(self) -> None:
        while True:
            try:
                log_name, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if log_name == "__process__":
                self.update_process_tree()
            elif log_name == "__context_status__":
                self.context_status_var.set(message)
            else:
                self.log(log_name, message)
        self.root.after(100, self.process_log_queue)

    def update_process_tree(self) -> None:
        existing = set(self.process_tree.get_children())
        for name, entry in self.processes.items():
            running = entry.running()
            values = (
                "running" if running else "stopped",
                entry.pid or "",
                entry.started_at.strftime("%H:%M:%S"),
                "" if entry.exit_code is None else entry.exit_code,
            )
            if name in existing:
                self.process_tree.item(name, text=name, values=values)
            else:
                self.process_tree.insert("", tk.END, iid=name, text=name, values=values)
        for item in list(existing):
            if item not in self.processes:
                self.process_tree.delete(item)

    def poll_status(self) -> None:
        self.previous_snapshot = status_snapshot(self.latest_session, self.previous_snapshot)
        snapshot = self.previous_snapshot
        stale_warning = stale_session_warning(self.latest_session)
        self.latest_tick_var.set(f"latest tick: {snapshot.get('latestTick') or 'unknown'}")
        self.packet_status_var.set(
            "compact packets: "
            f"{'available' if snapshot.get('compactPacketsAvailable') else 'unknown'}; "
            f"input={snapshot.get('inputSourceActive') or 'unknown'}; "
            f"candidates={snapshot.get('candidateCount') if snapshot.get('candidateCount') is not None else 'unknown'}; "
            f"budgetExceeded={snapshot.get('budgetExceeded')}; "
            f"writeFailures={snapshot.get('writeFailures')}; "
            f"segment={Path(str(snapshot.get('latestSegment'))).name if snapshot.get('latestSegment') else 'unknown'}; "
            f"recording={snapshot.get('recordingMode') or 'unknown'}; "
            f"rawTicks={snapshot.get('rawTickRecordingEnabled')}; "
            f"frames={snapshot.get('frameRecordingEnabled')}; "
            f"event={snapshot.get('latestEventSummary') or 'none'}"
            + (f"; warning={stale_warning}" if stale_warning else "")
        )
        if self.is_process_running("Context Service"):
            if not self.context_poll_inflight:
                self.context_poll_inflight = True
                threading.Thread(target=self._context_status_worker, daemon=True).start()
        else:
            self.context_status_var.set("context: service stopped")
        self.update_process_tree()
        self.root.after(2000, self.poll_status)

    def is_process_running(self, name: str) -> bool:
        entry = self.processes.get(name)
        return bool(entry and entry.running())

    def _context_status_worker(self) -> None:
        port = self.options().port
        url = f"http://127.0.0.1:{port}/context"
        body = json.dumps(build_context_request_body()).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=0.75) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.log_queue.put(("__context_status__", f"context: {payload.get('status', 'unknown')} tick={payload.get('latestTick', 'unknown')}"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.log_queue.put(("__context_status__", f"context: unavailable ({exc})"))
        finally:
            self.context_poll_inflight = False

    def on_close(self) -> None:
        running = [name for name, entry in self.processes.items() if entry.running()]
        if running:
            answer = messagebox.askyesnocancel("Stop helper processes?", "Stop running helper processes?")
            if answer is None:
                return
            if answer:
                self.stop_all()
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OSRS telemetry live control panel.")
    parser.add_argument("--auto-start-normal-live", action="store_true", help="Open the panel and start the normal compact live stack.")
    parser.add_argument("--profile", default="woodcutting", choices=PROFILES, help="Default target profile.")
    parser.add_argument("--mode", default="normal-live", choices=("normal-live", "visual-qa", "debug-audit"), help="Initial workflow mode.")
    return parser.parse_args(argv)


def _mode_label(value: str) -> str:
    mapping = {
        "normal-live": "Normal Live",
        "visual-qa": "Visual QA",
        "debug-audit": "Debug Audit",
    }
    return mapping.get(value, "Normal Live")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = tk.Tk()
    panel = LiveControlPanel(root)
    panel.profile_var.set(args.profile)
    panel.mode_var.set(_mode_label(args.mode))
    if args.mode == "normal-live":
        panel.apply_normal_live_defaults()
        panel.profile_var.set(args.profile)
    if args.auto_start_normal_live:
        root.after(500, panel.start_normal_live_stack)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
