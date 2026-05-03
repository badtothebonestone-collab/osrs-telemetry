import os
import queue
import subprocess
import sys
import threading
import json
import webbrowser
from dataclasses import dataclass
from datetime import datetime
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
    session_size_mb,
    tick_age_seconds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_URL = "http://127.0.0.1:8765/"
STOP_GRACE_MS = 2500
MAX_LOG_LINES = 5000
HEALTH_REFRESH_MS = 5000


@dataclass(frozen=True)
class ProcessSpec:
    key: str
    name: str
    command: list[str]
    long_running: bool


PROCESS_SPECS = {
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
    "tests": ProcessSpec(
        "tests",
        "Path Regression Tests",
        ["python", "telemetry-viewer\\test_telemetry_paths.py"],
        False,
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


def collect_health(sessions_dir: Path, validation_result: str) -> dict:
    values = {
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
        "perception_bundle_count": "not built",
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
    }

    if not sessions_dir.exists():
        return health_result("stale", f"Sessions dir missing: {sessions_dir}", values, paths)

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
        latest_frame = newest_file_in_dir(session / "frames")

    values["newest_session"] = str(session)
    values["active"] = format_bool(active)
    values["tick_files"] = str(len(tick_files))
    values["event_files"] = str(len(event_files))
    values["frame_files"] = str(count_frame_files(session))
    values["frames_size"] = format_mb(directory_size(session / "frames") / (1024 * 1024))
    values["session_size"] = format_mb(session_size_mb(session))

    perception_index = safe_read_json(session / "perception" / "perception_index.json")

    if isinstance(perception_index, dict):
        bundle_count = perception_index.get("tickBundleCount")
        values["perception_bundle_count"] = (
            str(bundle_count) if bundle_count is not None else "unknown"
        )

    paths["session"] = str(session)
    paths["latest_frame"] = str(latest_frame) if latest_frame else None
    paths["latest_status"] = str(session / "latest" / "latest_status.json")
    paths["manifest"] = str(session / "manifest.json")
    paths["newest_tick_segment"] = str(tick_files[-1]) if tick_files else None
    paths["newest_event_segment"] = str(event_files[-1]) if event_files else None

    if latest_tick is None:
        return health_result("stale", "No ticks found", values, paths)

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
        self.auto_scroll_var = BooleanVar(value=True)
        self.health_log_var = BooleanVar(value=False)
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.health_queue: queue.Queue[dict] = queue.Queue()
        self.managed = {key: ManagedProcess(spec) for key, spec in PROCESS_SPECS.items()}
        self.start_buttons: dict[str, ttk.Button] = {}
        self.health_values: dict[str, StringVar] = {}
        self.health_paths: dict[str, str | None] = {}
        self.health_refresh_running = False
        self.last_validation_result = "unknown"
        self.log_line_count = 0

        self._build_ui()
        self._refresh_status_table()
        self.after(100, self._drain_output_queue)
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

        button_frame = ttk.Frame(self)
        button_frame.grid(row=1, column=0, rowspan=3, sticky="ns", padx=(10, 6), pady=6)

        self._add_button_group(
            button_frame,
            "Quick Start",
            [
                ("Start Core Stack", self.start_core_stack, None),
                ("Stop All Started Processes", self.stop_all_processes, None),
            ],
        )
        self._add_button_group(
            button_frame,
            "RuneLite / Live",
            [
                ("Start RuneLite Dev Client", lambda: self.start_process("runelite"), "runelite"),
                ("Start Latest State Watcher", lambda: self.start_process("latest"), "latest"),
                ("Start Text Viewer", lambda: self.start_process("viewer"), "viewer"),
            ],
        )
        self._add_button_group(
            button_frame,
            "Replay / Analysis",
            [
                ("Start Replay Viewer", lambda: self.start_process("replay"), "replay"),
                ("Open Replay Viewer in Browser", self.open_replay_viewer, None),
                ("Run Validate Session", lambda: self.start_process("validate"), None),
                ("Run Export Session", lambda: self.start_process("export"), None),
                ("Build Perception Dataset", lambda: self.start_process("perception"), None),
                ("Run Path Regression Tests", lambda: self.start_process("tests"), None),
            ],
        )
        self._add_button_group(
            button_frame,
            "Folders",
            [
                ("Open Sessions Folder", self.open_sessions_folder, None),
                ("Open Newest Session Folder", self.open_newest_session_folder, None),
            ],
        )
        self._add_button_group(
            button_frame,
            "Process Controls",
            [
                ("Stop Selected Process", self.stop_selected_process, None),
                ("Clear Log", self.clear_log, None),
            ],
        )

        self._build_health_panel()
        self._build_status_panel()
        self._build_log_panel()

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
            ("newest_session", "Newest session path"),
            ("active", "Session active"),
            ("tick_id", "Latest tickId"),
            ("tick_age", "Latest tick age"),
            ("game_state", "Latest gameState"),
            ("position", "Latest position"),
            ("resources", "HP / prayer / run"),
            ("tick_files", "Tick files"),
            ("event_files", "Event files"),
            ("frame_files", "Frame files"),
            ("frame_write_delay", "Latest frame write delay"),
            ("frame_total_latency", "Latest frame total latency"),
            ("frame_index_status", "Latest frame index status"),
            ("frame_written_count", "FrameWritten count"),
            ("frame_dropped_count", "FrameDropped count"),
            ("frame_deleted_count", "FrameDeleted count"),
            ("perception_bundle_count", "Perception bundle count"),
            ("frames_size", "Frames folder size MB"),
            ("session_size", "Session size MB"),
            ("capture_errors", "Capture errors"),
            ("validation", "Last validation result"),
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
            ("Open newest session folder", "session"),
        )):
            ttk.Button(
                quick_actions,
                text=label,
                command=lambda target_key=key: self.open_health_target(target_key),
            ).grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=2)
            quick_actions.columnconfigure(index % 3, weight=1)

    def _add_button_group(self, parent, title: str, controls: list[tuple[str, object, str | None]]):
        row = len(parent.grid_slaves())
        group = ttk.LabelFrame(parent, text=title)
        group.grid(row=row, column=0, sticky="ew", pady=(0, 8))
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

    def start_process(self, key: str):
        managed = self.managed[key]

        if managed.is_running():
            self.log(managed.spec.name, f"Already running with PID {managed.pid()}.")
            return

        command = list(managed.spec.command)
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
            return

        managed.process = process
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

    def _mark_started(self, key: str):
        managed = self.managed[key]

        if managed.is_running():
            managed.starting = False
            self._refresh_status_table()

    def start_core_stack(self):
        for key in ("runelite", "latest", "replay"):
            if not self.managed[key].is_running():
                self.start_process(key)

        self.open_replay_viewer()

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

            if managed.spec.key == "validate":
                result = "pass" if return_code == 0 else "fail"
                self.last_validation_result = f"{result} at {datetime.now().strftime('%H:%M:%S')}"

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

    def refresh_health(self):
        if self.health_refresh_running:
            return

        self.health_refresh_running = True
        sessions_dir = self.sessions_dir()
        threading.Thread(
            target=self._collect_health,
            args=(sessions_dir,),
            daemon=True,
        ).start()

    def _auto_refresh_health(self):
        self.refresh_health()
        self.after(HEALTH_REFRESH_MS, self._auto_refresh_health)

    def _collect_health(self, sessions_dir: Path):
        try:
            health = collect_health(sessions_dir, self.last_validation_result)
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
        values = health.get("values", {})
        self.health_paths = health.get("paths", {})

        for key, variable in self.health_values.items():
            variable.set(values.get(key, "-"))

        status = health.get("status", "unknown")
        message = health.get("message", "Health unknown")
        hint = ""

        if status == "stale" and not self.managed["runelite"].is_running():
            hint = " Start Core Stack to resume live telemetry."

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
            self.status_table.insert(
                "",
                "end",
                iid=key,
                text=managed.spec.name,
                values=(
                    status,
                    managed.pid(),
                    managed.started_display(),
                    " ".join(managed.spec.command),
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

    def open_sessions_folder(self):
        self.open_folder(self.sessions_dir(), "Sessions Folder")

    def open_newest_session_folder(self):
        newest = find_newest_session(self.sessions_dir())

        if newest is None:
            self.log("Launcher", f"No sessions found in {self.sessions_dir()}")
            return

        self.open_folder(newest, "Newest Session Folder")

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
