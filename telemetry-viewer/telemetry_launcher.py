import os
import queue
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


sys.path.insert(0, str(Path(__file__).resolve().parent))

from telemetry_paths import DEFAULT_SESSIONS_DIR, SESSIONS_DIR_ENV, find_newest_session  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_URL = "http://127.0.0.1:8765/"
STOP_GRACE_MS = 2500
MAX_LOG_LINES = 5000


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
    "tests": ProcessSpec(
        "tests",
        "Path Regression Tests",
        ["python", "telemetry-viewer\\test_telemetry_paths.py"],
        False,
    ),
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
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.managed = {key: ManagedProcess(spec) for key, spec in PROCESS_SPECS.items()}
        self.start_buttons: dict[str, ttk.Button] = {}
        self.log_line_count = 0

        self._build_ui()
        self._refresh_status_table()
        self.after(100, self._drain_output_queue)
        self.after(500, self._poll_processes)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        config_frame = ttk.LabelFrame(self, text="Configuration")
        config_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="Sessions dir").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(config_frame, textvariable=self.sessions_dir_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)

        button_frame = ttk.Frame(self)
        button_frame.grid(row=1, column=0, rowspan=2, sticky="ns", padx=(10, 6), pady=6)

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

        status_frame = ttk.LabelFrame(self, text="Managed Processes")
        status_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 10), pady=6)
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

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=2, column=1, sticky="nsew", padx=(6, 10), pady=(6, 10))
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
            pady=(0, 8),
        )

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

    def open_folder(self, path: Path, label: str):
        path = path.expanduser()

        if not path.exists():
            self.log("Launcher", f"{label} does not exist: {path}")
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
