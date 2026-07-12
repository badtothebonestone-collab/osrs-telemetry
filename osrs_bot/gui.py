from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .application import EngineApplication, LifecycleState
from .gui_controller import (
    GuiController,
    GuiControllerBusyError,
    GuiControllerClosedError,
    GuiControllerError,
)


WINDOW_TITLE = "OSRS Automation Engine"
GUI_PRESENTATION_SCHEMA = "osrs_operator_gui.v1"
POLL_MILLISECONDS = 250
CONNECTION_REFRESH_MILLISECONDS = 3_000

_STATUS_COLORS = {
    "READY": ("#0f7d32", "#ffffff"),
    "RUNNING": ("#1769aa", "#ffffff"),
    "PAUSED": ("#8a5a00", "#ffffff"),
    "BLOCKED": ("#b00020", "#ffffff"),
    "COMPLETE": ("#0f7d32", "#ffffff"),
    "NOT READY": ("#b00020", "#ffffff"),
    "UNKNOWN": ("#5f6368", "#ffffff"),
    "REQUESTED": ("#8a5a00", "#ffffff"),
}

_BLOCKER_MESSAGES = {
    "endpoint": "RuneLite telemetry is unavailable.",
    "runelite_not_running": "RuneLite is not running.",
    "loaded_scene": "Log in and enter a loaded scene.",
    "foreground": "Bring RuneLite to the foreground.",
    "layout": "Current client layout is unsupported.",
    "arduino": "The configured Arduino COM port is unavailable.",
    "lease": "Another process owns the Arduino lease.",
    "bank_pin": "A bank PIN or ambiguous interface was detected.",
    "cursor": "The cursor could not be safely reacquired.",
    "safe_stop": "Safe Stop is still finishing the current action.",
    "cleanup": "Cleanup could not be authoritatively confirmed.",
}


class OperatorWindow:
    """Tk presentation only; all operations flow through GuiController."""

    def __init__(self, root: tk.Tk, controller: GuiController) -> None:
        self.root = root
        self.controller = controller
        self._closing = False
        self._last_event_sequence = 0
        self._last_connection_refresh = 0.0
        self._last_render_signature: tuple[object, ...] | None = None
        self._latest_proof_path: Path | None = None
        self._latest_demo_path: Path | None = None
        self._next_snapshot_refresh = 0.0

        self.root.title(WINDOW_TITLE)
        display_scale = _display_scale(self.root)
        self._display_scale = display_scale
        self.root.minsize(
            round(1_000 * display_scale),
            round(680 * display_scale),
        )
        settings = controller.settings
        self.root.geometry(
            settings.geometry
            or f"{round(1_180 * display_scale)}x{round(820 * display_scale)}"
        )
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
        self._configure_style()

        self.lifecycle_var = tk.StringVar(value="IDLE")
        self.header_detail_var = tk.StringVar(value="Starting operator view…")
        self.status_bar_var = tk.StringVar(value="Ready")
        self.mode_var = tk.StringVar(value="observe")
        self.profile_validation_var = tk.StringVar(value="Validation pending")
        self.arduino_var = tk.StringVar(value=settings.arduino_port)
        self.overlay_var = tk.BooleanVar(value=settings.overlay_enabled)

        self.connection_vars = {
            key: tk.StringVar(value="UNKNOWN")
            for key in (
                "process",
                "pid",
                "endpoint",
                "loaded",
                "game_state",
                "foreground",
                "layout",
                "cursor",
                "blocker",
            )
        }
        self.live_vars = {
            key: tk.StringVar(value="—")
            for key in (
                "task",
                "binding",
                "state",
                "route_step",
                "route_progress",
                "cycle_progress",
                "location",
                "inventory",
                "target",
                "action",
                "distance",
                "candidates",
                "pending_verification",
                "last_outcome",
                "execution",
                "receipt",
                "cleanup",
                "blocker",
            )
        }
        self.demo_status_var = tk.StringVar(value="Not recording")
        self.demo_name_var = tk.StringVar(value="manual-walk")
        self.demo_duration_var = tk.StringVar(value="30")
        self.demo_artifact_var = tk.StringVar(value="—")
        self.self_test_var = tk.StringVar(value="NOT RUN")
        self.replay_var = tk.StringVar(value="NOT RUN")

        self._build_header()
        self._build_notebook()
        self._build_status_bar()
        self._load_catalog()
        self._validate_profile()

        self._safe_request(self.controller.apply_startup_preferences)
        self._safe_request(self.controller.refresh_connection)
        if settings.arduino_port:
            self._safe_request(
                lambda: self.controller.request_arduino_readiness(
                    settings.arduino_port
                )
            )
        self._safe_request(self.controller.request_diagnostics)
        self.root.after(POLL_MILLISECONDS, self._poll)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Value.TLabel", font=("Segoe UI", 10))
        style.configure("Hint.TLabel", foreground="#5f6368")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7))
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7))
        style.configure(
            "Treeview",
            rowheight=self._px(28),
            font=("Segoe UI", 9),
        )
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text=WINDOW_TITLE, style="Header.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.lifecycle_badge = tk.Label(
            header,
            textvariable=self.lifecycle_var,
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            background="#5f6368",
            foreground="#ffffff",
        )
        self.lifecycle_badge.grid(row=0, column=2, sticky="e")
        ttk.Label(
            header,
            textvariable=self.header_detail_var,
            style="Hint.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 0))

    def _build_notebook(self) -> None:
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.run_tab = ttk.Frame(self.notebook, padding=10)
        self.live_tab = ttk.Frame(self.notebook, padding=10)
        self.demo_tab = ttk.Frame(self.notebook, padding=10)
        self.diagnostics_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.run_tab, text="Run")
        self.notebook.add(self.live_tab, text="Live Status")
        self.notebook.add(self.demo_tab, text="Demonstrations")
        self.notebook.add(self.diagnostics_tab, text="Diagnostics")
        self._build_run_tab()
        self._build_live_tab()
        self._build_demo_tab()
        self._build_diagnostics_tab()

    def _build_run_tab(self) -> None:
        self.run_tab.columnconfigure(0, weight=3)
        self.run_tab.columnconfigure(1, weight=2)
        self.run_tab.rowconfigure(0, weight=1)
        left = ttk.Frame(self.run_tab)
        right = ttk.Frame(self.run_tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        connection = ttk.LabelFrame(
            left, text="RuneLite Connection", style="Section.TLabelframe", padding=10
        )
        connection.grid(row=0, column=0, sticky="ew")
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)
        rows = (
            ("RuneLite", "process", "PID", "pid"),
            ("Endpoint 8893", "endpoint", "Game state", "game_state"),
            ("Loaded scene", "loaded", "Foreground", "foreground"),
            ("175% fixed layout", "layout", "Cursor", "cursor"),
        )
        for row, (left_name, left_key, right_name, right_key) in enumerate(rows):
            ttk.Label(connection, text=f"{left_name}:").grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=2
            )
            ttk.Label(connection, textvariable=self.connection_vars[left_key]).grid(
                row=row, column=1, sticky="w", pady=2
            )
            ttk.Label(connection, text=f"{right_name}:").grid(
                row=row, column=2, sticky="w", padx=(14, 6), pady=2
            )
            ttk.Label(connection, textvariable=self.connection_vars[right_key]).grid(
                row=row, column=3, sticky="w", pady=2
            )
        ttk.Label(connection, text="Current blocker:").grid(
            row=4, column=0, sticky="nw", pady=(5, 2)
        )
        ttk.Label(
            connection,
            textvariable=self.connection_vars["blocker"],
            wraplength=560,
        ).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(5, 2))
        connection_buttons = ttk.Frame(connection)
        connection_buttons.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        self.launch_button = ttk.Button(
            connection_buttons,
            text="Launch / Connect RuneLite",
            command=lambda: self._safe_request(self.controller.launch_or_connect_runelite),
        )
        self.launch_button.pack(side="left")
        self.refresh_button = ttk.Button(
            connection_buttons,
            text="Refresh Status",
            command=self._refresh_all,
        )
        self.refresh_button.pack(side="left", padx=6)
        self.login_button = ttk.Button(
            connection_buttons,
            text="Login / Recover Session",
            command=self._login_or_recover,
        )
        self.login_button.pack(side="left")

        profile = ttk.LabelFrame(
            left, text="Validated Profile", style="Section.TLabelframe", padding=10
        )
        profile.grid(row=1, column=0, sticky="ew", pady=10)
        profile.columnconfigure(1, weight=1)
        self.profile_vars = {
            key: tk.StringVar(value="—")
            for key in ("task", "definition", "resource", "area", "bank", "goal", "profile")
        }
        labels = (
            ("Task", "task"),
            ("Task / site definition", "definition"),
            ("Resource", "resource"),
            ("Area", "area"),
            ("Bank", "bank"),
            ("Goal", "goal"),
            ("Profile", "profile"),
        )
        for row, (label, key) in enumerate(labels):
            ttk.Label(profile, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=2
            )
            ttk.Label(profile, textvariable=self.profile_vars[key]).grid(
                row=row, column=1, sticky="w", pady=2
            )
        ttk.Label(
            profile,
            text="Only one validated option is currently available.",
            style="Hint.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 2))
        self.validation_label = ttk.Label(
            profile, textvariable=self.profile_validation_var, wraplength=600
        )
        self.validation_label.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        runtime = ttk.LabelFrame(
            left, text="Execution and Lifecycle", style="Section.TLabelframe", padding=10
        )
        runtime.grid(row=2, column=0, sticky="ew")
        runtime.columnconfigure(1, weight=1)
        modes = ttk.Frame(runtime)
        modes.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Radiobutton(
            modes,
            text="Observe Only — no gameplay input",
            value="observe",
            variable=self.mode_var,
        ).pack(side="left", padx=(0, 18))
        ttk.Radiobutton(
            modes,
            text="Start Live — Arduino production input",
            value="live",
            variable=self.mode_var,
        ).pack(side="left")
        ttk.Label(runtime, text="Arduino port:").grid(
            row=1, column=0, sticky="w", pady=(9, 4)
        )
        self.arduino_combo = ttk.Combobox(
            runtime,
            textvariable=self.arduino_var,
            values=tuple(filter(None, (self.arduino_var.get(), "COM6"))),
            width=14,
        )
        self.arduino_combo.grid(row=1, column=1, sticky="w", pady=(9, 4))
        self.arduino_combo.bind("<<ComboboxSelected>>", lambda _event: self._arduino_changed())
        self.arduino_combo.bind("<FocusOut>", lambda _event: self._arduino_changed())
        self.overlay_check = ttk.Checkbutton(
            runtime,
            text="Enable passive EngineFrame overlay",
            variable=self.overlay_var,
            command=self._toggle_overlay,
        )
        self.overlay_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        controls = ttk.Frame(runtime)
        controls.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(
            controls, text="Start", style="Primary.TButton", command=self._start
        )
        self.pause_button = ttk.Button(controls, text="Pause", command=self._pause)
        self.resume_button = ttk.Button(controls, text="Resume", command=self._resume)
        self.stop_button = ttk.Button(
            controls, text="Safe Stop", style="Danger.TButton", command=self._safe_stop
        )
        for button in (
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.stop_button,
        ):
            button.pack(side="left", padx=(0, 6))

        preflight = ttk.LabelFrame(
            right, text="Preflight", style="Section.TLabelframe", padding=8
        )
        preflight.grid(row=0, column=0, sticky="nsew")
        preflight.rowconfigure(0, weight=1)
        preflight.columnconfigure(0, weight=1)
        self.preflight_tree = ttk.Treeview(
            preflight,
            columns=("status", "detail"),
            show="tree headings",
            selectmode="none",
        )
        self.preflight_tree.heading("#0", text="Indicator")
        self.preflight_tree.heading("status", text="Status")
        self.preflight_tree.heading("detail", text="Details")
        self.preflight_tree.column("#0", width=self._px(165), stretch=False)
        self.preflight_tree.column(
            "status", width=self._px(82), stretch=False, anchor="center"
        )
        self.preflight_tree.column("detail", width=self._px(190), stretch=True)
        self.preflight_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(preflight, orient="vertical", command=self.preflight_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.preflight_tree.configure(yscrollcommand=scrollbar.set)
        for status, (background, foreground) in _STATUS_COLORS.items():
            self.preflight_tree.tag_configure(status, background=background, foreground=foreground)

    def _build_live_tab(self) -> None:
        self.live_tab.columnconfigure(0, weight=1)
        self.live_tab.rowconfigure(1, weight=1)
        summary = ttk.LabelFrame(
            self.live_tab, text="Latest EngineFrame", style="Section.TLabelframe", padding=10
        )
        summary.grid(row=0, column=0, sticky="ew")
        for column in (1, 3):
            summary.columnconfigure(column, weight=1)
        items = (
            ("Task", "task", "Profile / definition", "binding"),
            ("Task state", "state", "Route step", "route_step"),
            ("Route progress", "route_progress", "Cycle progress", "cycle_progress"),
            ("Player location", "location", "Inventory", "inventory"),
            ("Selected target", "target", "Selected action", "action"),
            ("Target distance", "distance", "Candidates", "candidates"),
            ("Pending verification", "pending_verification", "Last outcome", "last_outcome"),
            ("Execution", "execution", "Input receipt", "receipt"),
            ("Cleanup", "cleanup", "Blocker", "blocker"),
        )
        for row, (a_label, a_key, b_label, b_key) in enumerate(items):
            ttk.Label(summary, text=f"{a_label}:").grid(row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
            ttk.Label(summary, textvariable=self.live_vars[a_key], wraplength=420).grid(row=row, column=1, sticky="w", pady=2)
            ttk.Label(summary, text=f"{b_label}:").grid(row=row, column=2, sticky="nw", padx=(18, 6), pady=2)
            ttk.Label(summary, textvariable=self.live_vars[b_key], wraplength=420).grid(row=row, column=3, sticky="w", pady=2)

        panes = ttk.Panedwindow(self.live_tab, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", pady=10)
        safety_frame = ttk.LabelFrame(panes, text="Ordered Safety Checks", padding=8)
        event_frame = ttk.LabelFrame(panes, text="Important Events (bounded)", padding=8)
        panes.add(safety_frame, weight=2)
        panes.add(event_frame, weight=3)
        safety_frame.rowconfigure(0, weight=1)
        safety_frame.columnconfigure(0, weight=1)
        self.safety_text = ScrolledText(
            safety_frame, height=12, wrap="word", font=("Consolas", 9), state="disabled"
        )
        self.safety_text.grid(row=0, column=0, sticky="nsew")
        event_frame.rowconfigure(0, weight=1)
        event_frame.columnconfigure(0, weight=1)
        self.event_tree = ttk.Treeview(
            event_frame,
            columns=("time", "status", "message"),
            show="headings",
        )
        for key, label, width in (
            ("time", "Time", 105),
            ("status", "Status", 95),
            ("message", "Event", 520),
        ):
            self.event_tree.heading(key, text=label)
            self.event_tree.column(
                key,
                width=self._px(width),
                stretch=key == "message",
            )
        self.event_tree.grid(row=0, column=0, sticky="nsew")
        event_scroll = ttk.Scrollbar(event_frame, orient="vertical", command=self.event_tree.yview)
        event_scroll.grid(row=0, column=1, sticky="ns")
        self.event_tree.configure(yscrollcommand=event_scroll.set)
        actions = ttk.Frame(self.live_tab)
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Copy Current Status", command=self._copy_status).pack(side="left")
        ttk.Button(actions, text="Save Current Status", command=self._save_status).pack(side="left", padx=6)
        ttk.Button(actions, text="Open Latest Proof Folder", command=self._open_latest_proof).pack(side="left")

    def _build_demo_tab(self) -> None:
        self.demo_tab.columnconfigure(0, weight=1)
        self.demo_tab.rowconfigure(2, weight=1)
        controls = ttk.LabelFrame(
            self.demo_tab, text="Read-only Demonstration Capture", style="Section.TLabelframe", padding=10
        )
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(5, weight=1)
        ttk.Label(controls, text="Short name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.demo_name_var, width=28).grid(row=0, column=1, sticky="w", padx=(5, 12))
        ttk.Label(controls, text="Maximum seconds:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(controls, from_=5, to=300, textvariable=self.demo_duration_var, width=7).grid(row=0, column=3, sticky="w", padx=(5, 12))
        self.record_demo_button = ttk.Button(controls, text="Record Demonstration", command=self._record_demo)
        self.record_demo_button.grid(row=0, column=4, padx=(0, 6))
        self.stop_demo_button = ttk.Button(controls, text="Stop Recording", command=self._stop_demo)
        self.stop_demo_button.grid(row=0, column=5, sticky="w")
        ttk.Button(controls, text="Inspect Demonstration", command=self._inspect_demo).grid(row=1, column=4, pady=(8, 0), padx=(0, 6))
        ttk.Button(controls, text="Open Demonstration Folder", command=self._open_demo_folder).grid(row=1, column=5, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="Capture status:").grid(row=2, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(controls, textvariable=self.demo_status_var, wraplength=760).grid(row=2, column=1, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="Artifact:").grid(row=3, column=0, sticky="nw", pady=(4, 0))
        ttk.Label(controls, textvariable=self.demo_artifact_var, wraplength=850).grid(row=3, column=1, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(
            self.demo_tab,
            text="Recording observes the existing RuneLite endpoint and never injects or replays input. Manual Computer Use actions remain operator evidence.",
            style="Hint.TLabel",
            wraplength=1_050,
        ).grid(row=1, column=0, sticky="w", pady=(8, 5))
        inspection = ttk.LabelFrame(
            self.demo_tab, text="Trusted Inspector Result", style="Section.TLabelframe", padding=8
        )
        inspection.grid(row=2, column=0, sticky="nsew")
        inspection.rowconfigure(0, weight=1)
        inspection.columnconfigure(0, weight=1)
        self.demo_text = ScrolledText(
            inspection, wrap="word", font=("Consolas", 9), state="disabled"
        )
        self.demo_text.grid(row=0, column=0, sticky="nsew")

    def _build_diagnostics_tab(self) -> None:
        self.diagnostics_tab.columnconfigure(0, weight=1)
        self.diagnostics_tab.rowconfigure(1, weight=1)
        buttons = ttk.Frame(self.diagnostics_tab)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(buttons, text="Run Quick Self-Test", command=lambda: self._safe_request(self.controller.run_quick_self_test)).pack(side="left")
        ttk.Label(buttons, textvariable=self.self_test_var, width=10).pack(side="left", padx=(5, 16))
        ttk.Button(buttons, text="Run Golden Replay", command=lambda: self._safe_request(self.controller.run_golden_replay)).pack(side="left")
        ttk.Label(buttons, textvariable=self.replay_var, width=10).pack(side="left", padx=(5, 16))
        ttk.Button(buttons, text="Open Logs / Proof Folder", command=self._open_latest_proof).pack(side="left")
        ttk.Button(buttons, text="Copy Diagnostic Summary", command=self._copy_diagnostics).pack(side="left", padx=6)
        ttk.Button(buttons, text="Refresh", command=lambda: self._safe_request(self.controller.request_diagnostics)).pack(side="left")
        frame = ttk.LabelFrame(
            self.diagnostics_tab, text="Diagnostic Summary", style="Section.TLabelframe", padding=8
        )
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.diagnostics_text = ScrolledText(
            frame, wrap="word", font=("Consolas", 9), state="disabled"
        )
        self.diagnostics_text.grid(row=0, column=0, sticky="nsew")

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status_bar_var, style="Hint.TLabel").grid(row=0, column=0, sticky="w")

    def _load_catalog(self) -> None:
        catalog = self.controller.catalog
        tasks = catalog.get("tasks") if isinstance(catalog, Mapping) else None
        definitions = catalog.get("definitions") if isinstance(catalog, Mapping) else None
        task = tasks[0] if isinstance(tasks, list) and tasks else {}
        definition = definitions[0] if isinstance(definitions, list) and definitions else {}
        profile = catalog.get("profile") if isinstance(catalog, Mapping) else {}
        defaults: dict[str, object] = {}
        if isinstance(profile, Mapping) and isinstance(profile.get("fields"), list):
            for field in profile["fields"]:
                if isinstance(field, Mapping) and isinstance(field.get("name"), str):
                    defaults[field["name"]] = field.get("default")
        resource = definition.get("resource") if isinstance(definition, Mapping) else {}
        bank = definition.get("bank") if isinstance(definition, Mapping) else {}
        self.profile_vars["task"].set(
            f"{task.get('displayName', '—')} ({task.get('taskId', '—')})"
            if isinstance(task, Mapping)
            else "—"
        )
        self.profile_vars["definition"].set(
            f"{definition.get('displayName', '—')} ({definition.get('definitionId', '—')})"
            if isinstance(definition, Mapping)
            else "—"
        )
        self.profile_vars["resource"].set(
            str(resource.get("name") or "—") if isinstance(resource, Mapping) else "—"
        )
        self.profile_vars["area"].set(str(definition.get("displayName") or "—") if isinstance(definition, Mapping) else "—")
        self.profile_vars["bank"].set(str(bank.get("name") or "—") if isinstance(bank, Mapping) else "—")
        self.profile_vars["goal"].set(f"{defaults.get('cycleGoal', 1)} complete bank cycle")
        self.profile_vars["profile"].set(str(defaults.get("profileId") or self.controller.settings.profile_id))

    def _validate_profile(self) -> bool:
        try:
            valid, detail = self.controller.validate_profile(self.controller.profile_values())
        except AttributeError:
            valid, detail = True, "Authoritative profile validation occurs again at Start."
        self.profile_validation_var.set(("VALID — " if valid else "INVALID — ") + detail)
        return valid

    def _start(self) -> None:
        if not self._validate_profile():
            messagebox.showerror(WINDOW_TITLE, self.profile_validation_var.get(), parent=self.root)
            return
        mode = self.mode_var.get()
        if mode == "live":
            port = self.arduino_var.get().strip().upper()
            if not port:
                messagebox.showerror(WINDOW_TITLE, "Start Live requires an Arduino COM port.", parent=self.root)
                return
            limits = _to_mapping(self.controller.runtime_configuration()) if hasattr(self.controller, "runtime_configuration") else {}
            connection = _to_mapping(self.controller.snapshot().result("connection"))
            blocker = _first(connection, "blocker", "currentBlocker") or self.connection_vars["blocker"].get()
            confirmation = (
                "Start production automation?\n\n"
                f"Profile: {self.profile_vars['profile'].get()}\n"
                f"Task/site: {self.profile_vars['definition'].get()}\n"
                f"Arduino: {port}\n"
                f"Limits: {self._limits_text(limits)}\n"
                f"Loaded scene: {self.connection_vars['loaded'].get()}\n"
                f"Current blocker: {blocker or 'none'}\n\n"
                "Gameplay actions will use InputCoordinator and the Arduino path."
            )
            if not messagebox.askyesno(WINDOW_TITLE, confirmation, parent=self.root):
                return
            self._safe_request(lambda: self.controller.start_live(port, self.controller.profile_values()))
        else:
            self._safe_request(lambda: self.controller.start_observe(self.controller.profile_values()))

    @staticmethod
    def _limits_text(values: Mapping[str, object]) -> str:
        return (
            f"{_first(values, 'max_actions', 'maxActions', default='?')} actions, "
            f"{_first(values, 'max_runtime_seconds', 'maxRuntimeSeconds', default='?')} s runtime"
        )

    def _pause(self) -> None:
        self._safe_request(self.controller.request_pause)

    def _resume(self) -> None:
        self._safe_request(self.controller.resume)

    def _safe_stop(self) -> None:
        self._safe_request(self.controller.request_safe_stop)

    def _login_or_recover(self) -> None:
        port = self.arduino_var.get().strip().upper()
        if not port:
            messagebox.showerror(WINDOW_TITLE, "Login recovery requires an Arduino COM port.", parent=self.root)
            return
        try:
            self.controller.save_preferences(arduino_port=port)
        except Exception:
            return
        self._safe_request(lambda: self.controller.request_arduino_readiness(port))
        self._safe_request(self.controller.login_or_recover)

    def _arduino_changed(self) -> None:
        port = self.arduino_var.get().strip().upper()
        if port:
            self.arduino_var.set(port)
            try:
                self.controller.save_preferences(arduino_port=port)
            except Exception:
                pass
            self._safe_request(lambda: self.controller.request_arduino_readiness(port))

    def _toggle_overlay(self) -> None:
        enabled = bool(self.overlay_var.get())
        self._safe_request(lambda: self.controller.set_overlay_enabled(enabled))

    def _record_demo(self) -> None:
        name = self.demo_name_var.get().strip()
        if not name:
            messagebox.showerror(WINDOW_TITLE, "Enter a short demonstration name.", parent=self.root)
            return
        try:
            duration = float(self.demo_duration_var.get())
        except ValueError:
            messagebox.showerror(WINDOW_TITLE, "Maximum seconds must be a number.", parent=self.root)
            return
        self._safe_request(
            lambda: self.controller.start_demonstration(
                name, duration_seconds=duration, screenshots_enabled=True
            )
        )

    def _stop_demo(self) -> None:
        self._safe_request(self.controller.stop_demonstration)

    def _inspect_demo(self) -> None:
        initial = self.controller.settings.last_demo_directory or str(Path("demo_runs").resolve())
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Select a finalized demonstration directory",
            initialdir=initial if Path(initial).exists() else str(Path.cwd()),
        )
        if selected:
            self._safe_request(lambda: self.controller.inspect_demonstration(selected))

    def _refresh_all(self) -> None:
        self._safe_request(self.controller.refresh_connection)
        port = self.arduino_var.get().strip().upper()
        if port:
            self._safe_request(lambda: self.controller.request_arduino_readiness(port))

    def _poll(self) -> None:
        if not self.root.winfo_exists():
            return
        try:
            state = self.controller.drain_results(limit=100)
            self._render(state)
            now = time.monotonic()
            if now >= self._next_snapshot_refresh:
                self._next_snapshot_refresh = now + 0.5
                self._safe_request(self.controller.request_refresh, show_error=False)
            if state.close_ready:
                if state.close_terminal_failure:
                    messagebox.showwarning(
                        WINDOW_TITLE,
                        "The engine reached a clear terminal failure during shutdown. Details remain in Diagnostics.",
                        parent=self.root,
                    )
                self.controller.join_workers(2.0)
                self.root.destroy()
                return
        except tk.TclError:
            return
        except Exception as error:
            self.status_bar_var.set(f"GUI update error: {type(error).__name__}: {error}")
        self.root.after(POLL_MILLISECONDS, self._poll)

    def _render(self, state: Any) -> None:
        application = state.application
        lifecycle = application.lifecycle
        lifecycle_text = lifecycle.value.upper().replace("_", " ")
        self.lifecycle_var.set(lifecycle_text)
        lifecycle_status = _lifecycle_status(lifecycle)
        colors = _STATUS_COLORS.get(lifecycle_status, _STATUS_COLORS["UNKNOWN"])
        self.lifecycle_badge.configure(background=colors[0], foreground=colors[1])
        blocker = state.blockers[0] if state.blockers else None
        self.header_detail_var.set(
            _plain_blocker(blocker)
            if blocker
            else _lifecycle_detail(lifecycle, application.runtime_control)
        )
        self.status_bar_var.set(
            f"Busy: {', '.join(state.busy_operations)}"
            if state.busy_operations
            else "Ready"
        )
        results = dict(state.operation_results)
        connection_result = _to_mapping(results.get("connection"))
        connection = _connection_mapping(connection_result)
        arduino = _to_mapping(results.get("arduinoReadiness"))
        overlay = _to_mapping(results.get("overlay"))
        if not overlay:
            startup = _to_mapping(results.get("startupPreferences"))
            overlay = _to_mapping(startup.get("overlay"))
        diagnostics = _to_mapping(results.get("diagnostics"))
        inspection = _to_mapping(results.get("demonstrationInspection"))
        self_test = _to_mapping(results.get("quickSelfTest"))
        replay = _to_mapping(results.get("goldenReplay"))
        self._render_connection(connection, state.engine_frame)
        self._render_preflight(connection, arduino, overlay, diagnostics, state)
        self._render_frame(state.engine_frame)
        self._render_events(state.events)
        self._render_demo(application, inspection)
        self._render_diagnostics(
            diagnostics,
            self_test,
            replay,
            connection=connection,
            arduino=arduino,
            overlay=overlay,
        )
        self._render_button_states(state)
        if (
            self._closing
            and not state.close_ready
            and "close" not in state.busy_operations
        ):
            self._closing = False

    def _render_connection(self, connection: Mapping[str, object], frame: Any) -> None:
        observation = frame.to_dict().get("observation") if frame is not None else None
        observation = observation if isinstance(observation, Mapping) else {}
        process_found = _runelite_found(connection)
        pid = _first(connection, "runelitePid", "pid", "processId", default=observation.get("processId"))
        endpoint = _first(connection, "endpointHealthy", "endpoint_healthy")
        loaded = _first(connection, "loadedScene", "loaded_scene", default=observation.get("loadedScene"))
        game_state = _first(connection, "gameState", "game_state", default=observation.get("gameState"))
        focused = _first(connection, "foreground", "clientFocused", "client_focused", default=observation.get("clientFocused"))
        layout = _first(connection, "supportedLayout", "supported_layout", "layoutSupported")
        cursor = _first(
            connection,
            "cursorInside",
            "cursor_inside",
            "cursorInsideRuneLite",
            "cursorInsideClient",
        )
        blocker = _first(connection, "blocker", "currentBlocker")
        self.connection_vars["process"].set(_yes_no(process_found))
        self.connection_vars["pid"].set(str(pid) if pid else "—")
        self.connection_vars["endpoint"].set(_ready_text(endpoint))
        self.connection_vars["loaded"].set(_ready_text(loaded))
        self.connection_vars["game_state"].set(str(game_state or "UNKNOWN"))
        self.connection_vars["foreground"].set(_ready_text(focused))
        self.connection_vars["layout"].set(_ready_text(layout, unknown="UNPROVEN"))
        self.connection_vars["cursor"].set(
            "INSIDE" if cursor is True else "OUTSIDE" if cursor is False else "UNKNOWN"
        )
        self.connection_vars["blocker"].set(_plain_blocker(str(blocker)) if blocker else "None")

    def _render_preflight(
        self,
        connection: Mapping[str, object],
        arduino: Mapping[str, object],
        overlay: Mapping[str, object],
        diagnostics: Mapping[str, object],
        state: Any,
    ) -> None:
        frame = state.engine_frame
        observation = frame.to_dict().get("observation") if frame is not None else None
        observation = observation if isinstance(observation, Mapping) else {}
        cleanup = frame.to_dict().get("cleanup") if frame is not None else None
        cleanup = cleanup if isinstance(cleanup, Mapping) else {}
        coherent = _first(
            connection,
            "coherentFreshObservation",
            "coherent_fresh_observation",
        )
        if coherent is None and observation:
            coherent = bool(
                observation.get("fresh")
                and observation.get("cacheWallClockFresh")
                and observation.get("sourceCoherent")
            )
        repository_ready = _first(
            diagnostics,
            "repositoryReady",
            "repository_ready",
            default=bool(diagnostics.get("commit")) if diagnostics else None,
        )
        rows = (
            ("Repository / application", _status(repository_ready), _detail(diagnostics, "repositoryReason", "repository_reason")),
            ("RuneLite found", _status(_runelite_found(connection)), _detail(connection, "processReason", "process_reason")),
            ("Endpoint healthy", _status(_first(connection, "endpointHealthy", "endpoint_healthy")), _detail(connection, "endpointReason", "endpoint_reason")),
            ("Loaded scene", _status(_first(connection, "loadedScene", "loaded_scene", default=observation.get("loadedScene"))), str(_first(connection, "gameState", "game_state", default=observation.get("gameState")) or "")),
            ("Coherent fresh Observation", _status(coherent), "fresh + wall-clock fresh + source coherent" if coherent else ""),
            ("Supported 175% fixed layout", _status(_first(connection, "supportedLayout", "supported_layout", "layoutSupported")), _detail(connection, "layoutReason", "layout_reason")),
            ("Exact process / session binding", _status(_first(connection, "exactBinding", "exact_binding", "sessionBound", "exactProcessBinding")), _detail(connection, "bindingReason", "binding_reason", "diagnostic")),
            ("Arduino port available", _status(_first(arduino, "portAvailable", "port_available", "available")), _detail(arduino, "portReason", "port_reason")),
            ("Arduino lease available", _status(_first(arduino, "leaseAvailable", "lease_available")), _detail(arduino, "leaseReason", "lease_reason")),
            ("Overlay", _overlay_status(overlay), _detail(overlay, "error", "detail")),
            ("Current blocker", "BLOCKED" if state.blockers else "READY", _plain_blocker(state.blockers[0]) if state.blockers else "None"),
            ("Latest cleanup", _cleanup_status(cleanup, application=state.application), _cleanup_detail(cleanup, state.application)),
        )
        existing = self.preflight_tree.get_children()
        for item in existing:
            self.preflight_tree.delete(item)
        for name, status, detail in rows:
            normalized = status if status in _STATUS_COLORS else "UNKNOWN"
            self.preflight_tree.insert(
                "",
                "end",
                text=name,
                values=(normalized, _bounded_text(detail or "", 72)),
                tags=(normalized,),
            )

    def _render_frame(self, frame: Any) -> None:
        if frame is None:
            for variable in self.live_vars.values():
                variable.set("—")
            self._set_text(self.safety_text, "No EngineFrame has been published.")
            return
        payload = frame.to_dict()
        task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
        observation = payload.get("observation") if isinstance(payload.get("observation"), Mapping) else {}
        decision = payload.get("decision") if isinstance(payload.get("decision"), Mapping) else {}
        selected = payload.get("selectedTarget") if isinstance(payload.get("selectedTarget"), Mapping) else {}
        route = task.get("routeProgress") if isinstance(task.get("routeProgress"), Mapping) else None
        cycle = task.get("cycleProgress") if isinstance(task.get("cycleProgress"), Mapping) else None
        inventory = observation.get("inventory") if isinstance(observation.get("inventory"), Mapping) else {}
        location = observation.get("playerLocation") if isinstance(observation.get("playerLocation"), Mapping) else {}
        last_verification = payload.get("lastVerification") if isinstance(payload.get("lastVerification"), Mapping) else {}
        last_execution = payload.get("lastExecution") if isinstance(payload.get("lastExecution"), Mapping) else {}
        receipt = last_execution.get("receipt") if isinstance(last_execution.get("receipt"), Mapping) else {}
        cleanup = payload.get("cleanup") if isinstance(payload.get("cleanup"), Mapping) else {}
        action = decision.get("action") if isinstance(decision.get("action"), Mapping) else {}
        self.live_vars["task"].set(str(task.get("taskId") or "—"))
        self.live_vars["binding"].set(f"{task.get('profileId') or '—'} / {task.get('definitionId') or '—'}")
        self.live_vars["state"].set(f"{task.get('state') or '—'} ({task.get('status') or '—'})")
        self.live_vars["route_step"].set(str(task.get("routeStep") or "—"))
        self.live_vars["route_progress"].set(_progress_text(route))
        self.live_vars["cycle_progress"].set(_progress_text(cycle))
        self.live_vars["location"].set(
            f"({location.get('x')}, {location.get('y')}, plane {location.get('plane')})"
            if location
            else "—"
        )
        self.live_vars["inventory"].set(_inventory_text(inventory))
        self.live_vars["target"].set(
            f"{selected.get('name')} [{selected.get('objectId')}] — {selected.get('key')}"
            if selected
            else "—"
        )
        self.live_vars["action"].set(
            f"{action.get('kind') or '—'} — {action.get('label') or decision.get('reason') or '—'}"
        )
        self.live_vars["distance"].set(str(selected.get("distance")) if selected.get("distance") is not None else "—")
        self.live_vars["candidates"].set(
            f"{len(payload.get('eligibleCandidates') or [])} eligible / {len(payload.get('rejectedCandidates') or [])} rejected"
        )
        self.live_vars["pending_verification"].set(_compact_json(payload.get("pendingVerification")))
        outcome = last_verification.get("outcome") if isinstance(last_verification.get("outcome"), Mapping) else None
        self.live_vars["last_outcome"].set(
            f"{last_verification.get('status') or '—'} — {_compact_json(outcome)}"
        )
        self.live_vars["execution"].set(
            f"{last_execution.get('status') or '—'} — {last_execution.get('reason') or '—'}"
        )
        self.live_vars["receipt"].set(_receipt_text(receipt))
        self.live_vars["cleanup"].set(_cleanup_detail(cleanup, None))
        self.live_vars["blocker"].set(_plain_blocker(str(payload.get("blocker"))) if payload.get("blocker") else "None")
        checks = payload.get("safetyChecks") if isinstance(payload.get("safetyChecks"), list) else []
        lines = [
            f"{index + 1:02d}. {'PASS' if check.get('allowed') else 'BLOCK'}  {check.get('stage')} / {check.get('code')}"
            for index, check in enumerate(checks)
            if isinstance(check, Mapping)
        ]
        self._set_text(self.safety_text, "\n".join(lines) if lines else "No safety evaluation published for this frame.")

    def _render_events(self, events: tuple[Any, ...]) -> None:
        if events and events[-1].sequence == self._last_event_sequence:
            return
        self.event_tree.delete(*self.event_tree.get_children())
        for event in events:
            self.event_tree.insert(
                "",
                "end",
                values=(event.occurred_at.astimezone().strftime("%H:%M:%S"), event.status, event.message),
            )
        if events:
            self._last_event_sequence = events[-1].sequence
            children = self.event_tree.get_children()
            if children:
                self.event_tree.see(children[-1])

    def _render_demo(self, application: Any, inspection: Mapping[str, object]) -> None:
        if application.lifecycle is LifecycleState.DEMONSTRATION_STOP_REQUESTED:
            self.demo_status_var.set("Stop requested; finalizing and inspecting evidence")
        elif application.active_capture_id:
            elapsed = ""
            if application.started_at:
                elapsed = f" — {(datetime.now(application.started_at.tzinfo) - application.started_at).total_seconds():.1f}s"
            self.demo_status_var.set(f"Recording {application.active_capture_id}{elapsed}")
        else:
            self.demo_status_var.set(f"{application.lifecycle.value.replace('_', ' ').title()}")
        reference = application.recent_demonstration
        if reference is not None:
            self._latest_demo_path = Path(reference.path)
            self.demo_artifact_var.set(str(reference.path))
        if inspection:
            path = _first(inspection, "path", "artifactPath")
            if path:
                self._latest_demo_path = Path(str(path))
                self.demo_artifact_var.set(str(path))
            elif self.controller.settings.last_demo_directory:
                self._latest_demo_path = Path(
                    self.controller.settings.last_demo_directory
                )
                self.demo_artifact_var.set(str(self._latest_demo_path))
            self._set_text(self.demo_text, _inspection_text(inspection))

    def _render_diagnostics(
        self,
        diagnostics: Mapping[str, object],
        self_test: Mapping[str, object],
        replay: Mapping[str, object],
        *,
        connection: Mapping[str, object],
        arduino: Mapping[str, object],
        overlay: Mapping[str, object],
    ) -> None:
        if diagnostics:
            proof = _first(diagnostics, "latestProofPath", "latest_proof_path")
            demo = _first(diagnostics, "latestDemonstrationPath", "latest_demo_path")
            if proof:
                self._latest_proof_path = Path(str(proof))
            if demo and self._latest_demo_path is None:
                self._latest_demo_path = Path(str(demo))
            combined = dict(diagnostics)
            combined["runeLite"] = {
                "pid": connection.get("processId"),
                "sessionId": connection.get("sessionId"),
                "state": connection.get("state"),
                "endpointHealthy": connection.get("endpointHealthy"),
            }
            combined["arduino"] = dict(arduino)
            combined["overlay"] = dict(overlay)
            self._set_text(
                self.diagnostics_text,
                json.dumps(_jsonable(combined), indent=2, sort_keys=True),
            )
        ports = arduino.get("availablePorts")
        if isinstance(ports, list):
            values = tuple(
                dict.fromkeys(
                    [self.arduino_var.get().strip().upper()]
                    + [str(port) for port in ports]
                )
            )
            self.arduino_combo.configure(values=tuple(filter(None, values)))
        if self_test:
            self.self_test_var.set(str(_first(self_test, "status", default="FAIL")))
            path = _first(self_test, "proofPath", "logPath", "proof_path", "log_path")
            if path:
                self._latest_proof_path = Path(str(path)).parent if Path(str(path)).suffix else Path(str(path))
        if replay:
            self.replay_var.set(str(_first(replay, "status", default="FAIL")))
            path = _first(replay, "proofPath", "logPath", "proof_path", "log_path")
            if path:
                self._latest_proof_path = Path(str(path)).parent if Path(str(path)).suffix else Path(str(path))

    def _render_button_states(self, state: Any) -> None:
        lifecycle = state.application.lifecycle
        active_run = state.application.active_run_id is not None
        active_demo = state.application.active_capture_id is not None
        busy = set(state.busy_operations)
        idle_mode = not active_run and not active_demo and state.pending_mode is None
        self.start_button.configure(state="normal" if idle_mode and "mode-start" not in busy else "disabled")
        self.pause_button.configure(state="normal" if lifecycle is LifecycleState.RUNNING else "disabled")
        self.resume_button.configure(state="normal" if lifecycle in {LifecycleState.PAUSED, LifecycleState.PAUSE_REQUESTED} else "disabled")
        self.stop_button.configure(state="normal" if active_run else "disabled")
        self.record_demo_button.configure(state="normal" if idle_mode else "disabled")
        self.stop_demo_button.configure(state="normal" if active_demo else "disabled")
        self.launch_button.configure(state="disabled" if "connection" in busy or not idle_mode else "normal")
        self.login_button.configure(state="disabled" if "connection" in busy or not idle_mode else "normal")
        self.refresh_button.configure(state="disabled" if "connection" in busy else "normal")

    def _status_payload(self) -> dict[str, object]:
        state = self.controller.snapshot()
        return {
            "schema": GUI_PRESENTATION_SCHEMA,
            "capturedAt": datetime.now().astimezone().isoformat(),
            "application": state.application.to_dict(),
            "engineFrame": state.engine_frame.to_dict() if state.engine_frame else None,
            "blockers": list(state.blockers),
            "events": [
                {
                    "sequence": event.sequence,
                    "occurredAt": event.occurred_at.isoformat(),
                    "kind": event.kind,
                    "status": event.status,
                    "message": event.message,
                }
                for event in state.events
            ],
            "operationResults": {
                key: _jsonable(value) for key, value in state.operation_results
            },
        }

    def _copy_status(self) -> None:
        self._copy_text(json.dumps(self._status_payload(), indent=2, sort_keys=True))

    def _save_status(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save current operator status",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"), ("All files", "*.*")),
            initialfile=f"osrs-engine-status-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(self._status_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.status_bar_var.set(f"Saved status: {path}")

    def _copy_diagnostics(self) -> None:
        diagnostics = dict(self.controller.snapshot().operation_results).get("diagnostics")
        self._copy_text(json.dumps(_jsonable(diagnostics), indent=2, sort_keys=True))

    def _copy_text(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()
        self.status_bar_var.set("Copied to clipboard")

    def _open_latest_proof(self) -> None:
        path = self._latest_proof_path or Path("_run_proofs").resolve()
        self._open_folder(path)

    def _open_demo_folder(self) -> None:
        path = self._latest_demo_path or Path("demo_runs").resolve()
        self._open_folder(path)

    def _open_folder(self, path: Path) -> None:
        target = path if path.is_dir() else path.parent
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            raise RuntimeError("Open Folder is supported only on Windows")

    def _request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.controller.save_preferences(
                arduino_port=self.arduino_var.get().strip().upper(),
                overlay_enabled=bool(self.overlay_var.get()),
                geometry=self.root.geometry(),
                update_geometry=True,
            )
            self.controller.request_close(timeout=120.0)
            self.status_bar_var.set("Safe shutdown requested; waiting for verification and cleanup…")
        except Exception as error:
            self._closing = False
            messagebox.showerror(WINDOW_TITLE, f"Could not begin safe shutdown: {type(error).__name__}: {error}", parent=self.root)

    def _safe_request(self, operation: Any, *, show_error: bool = True) -> None:
        try:
            operation()
        except (GuiControllerBusyError, GuiControllerClosedError):
            return
        except (GuiControllerError, TypeError, ValueError, RuntimeError) as error:
            if show_error:
                messagebox.showerror(WINDOW_TITLE, f"{type(error).__name__}: {error}", parent=self.root)
            else:
                self.status_bar_var.set(f"{type(error).__name__}: {error}")

    @staticmethod
    def _set_text(widget: ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _px(self, logical_pixels: int) -> int:
        return max(1, round(logical_pixels * self._display_scale))


class _SingleInstance:
    def __init__(self) -> None:
        self._handle: int | None = None
        self.acquired = False

    def __enter__(self) -> "_SingleInstance":
        if os.name != "nt":
            self.acquired = True
            return self
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\OSRSAutomationEngineOperatorGuiV1")
        if not handle:
            return self
        self._handle = int(handle)
        self.acquired = int(kernel32.GetLastError()) != 183
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._handle is not None and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))  # type: ignore[attr-defined]
        self._handle = None


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # type: ignore[attr-defined]
    except Exception:
        pass


def _display_scale(root: tk.Tk) -> float:
    """Return the device-pixel scale used by Tk window geometry on Windows."""

    if os.name == "nt":
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())  # type: ignore[attr-defined]
            if 96 <= dpi <= 768:
                return dpi / 96.0
        except Exception:
            pass
    try:
        value = float(root.winfo_fpixels("1i")) / 96.0
        return min(8.0, max(1.0, value))
    except (tk.TclError, TypeError, ValueError):
        return 1.0


def _to_mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        converted = converter()
        return dict(converted) if isinstance(converted, Mapping) else {}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _connection_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Unwrap launch results while retaining their operator diagnostic."""

    nested = _to_mapping(value.get("connection"))
    if not nested:
        return dict(value)
    combined = nested
    for key in ("state", "reason", "launched", "successful", "logPath"):
        if key in value:
            combined[f"operation{key[0].upper()}{key[1:]}"] = value[key]
    return combined


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        return _jsonable(converter())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _first(values: Mapping[str, object], *keys: str, default: object = None) -> object:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return default


def _detail(values: Mapping[str, object], *keys: str) -> str:
    value = _first(values, *keys)
    return "" if value is None else str(value)


def _runelite_found(values: Mapping[str, object]) -> object:
    """Present the explicit probe, with exact binding as a logical proof.

    An exact telemetry-PID/window binding cannot exist without a RuneLite
    process.  Keeping this implication in the presentation adapter prevents a
    wrapped or older operator result from rendering an internally
    contradictory UNKNOWN/READY pair.
    """

    found = _first(values, "runeLiteFound", "processFound", "runelite_found")
    if found is None and _first(
        values,
        "exactBinding",
        "exact_binding",
        "sessionBound",
        "exactProcessBinding",
    ) is True:
        return True
    return found


def _status(value: object) -> str:
    if value is True:
        return "READY"
    if value is False:
        return "NOT READY"
    if isinstance(value, str):
        normalized = value.upper().replace("_", " ")
        if normalized in _STATUS_COLORS:
            return normalized
        if normalized in {"PASS", "AVAILABLE", "ACTIVE"}:
            return "READY"
        if normalized in {"FAIL", "UNAVAILABLE", "FAILED"}:
            return "NOT READY"
    return "UNKNOWN"


def _overlay_status(values: Mapping[str, object]) -> str:
    state = str(_first(values, "state", "status", default="disabled")).lower()
    if state == "active":
        return "READY"
    if state == "starting":
        return "RUNNING"
    if state == "failed":
        return "BLOCKED"
    return "UNKNOWN" if not values else "NOT READY"


def _cleanup_status(cleanup: Mapping[str, object], application: Any) -> str:
    if cleanup.get("safe") is True:
        return "COMPLETE"
    if not cleanup or cleanup.get("attempted") is False:
        if application is not None and not application.execute_requested:
            return "UNKNOWN"
        return "UNKNOWN"
    return "BLOCKED"


def _cleanup_detail(cleanup: Mapping[str, object], application: Any) -> str:
    if cleanup.get("safe") is True:
        return "Safe cleanup completed: STOP_ALL, DISARM, STATUS, zero held input, closed ledger/backend."
    if not cleanup or cleanup.get("attempted") is False:
        if application is not None and not application.execute_requested:
            return "Not required in Observe Only."
        return "Not attempted yet."
    return "Cleanup could not be authoritatively confirmed."


def _lifecycle_status(lifecycle: LifecycleState) -> str:
    if lifecycle is LifecycleState.PAUSED:
        return "PAUSED"
    if lifecycle in {LifecycleState.RUNNING, LifecycleState.STARTING, LifecycleState.DEMONSTRATING}:
        return "RUNNING"
    if lifecycle in {LifecycleState.BLOCKED, LifecycleState.ERROR}:
        return "BLOCKED"
    if lifecycle in {LifecycleState.COMPLETE, LifecycleState.STOPPED}:
        return "COMPLETE"
    if lifecycle in {LifecycleState.PAUSE_REQUESTED, LifecycleState.SAFE_STOP_REQUESTED, LifecycleState.DEMONSTRATION_STOP_REQUESTED}:
        return "REQUESTED"
    return "UNKNOWN"


def _lifecycle_detail(lifecycle: LifecycleState, control: object) -> str:
    details = {
        LifecycleState.IDLE: "Ready to start Observe Only or the validated live profile.",
        LifecycleState.STARTING: "Starting the engine worker.",
        LifecycleState.RUNNING: "Engine worker is running.",
        LifecycleState.PAUSE_REQUESTED: "Pause requested; awaiting a no-input boundary.",
        LifecycleState.PAUSED: "Pause acknowledged; the engine is currently paused.",
        LifecycleState.SAFE_STOP_REQUESTED: "Safe Stop requested; awaiting the current transaction, verification, and cleanup.",
        LifecycleState.DEMONSTRATING: "Read-only demonstration capture is active.",
        LifecycleState.DEMONSTRATION_STOP_REQUESTED: "Demonstration stop requested; finalizing evidence.",
        LifecycleState.COMPLETE: "The selected operation completed.",
        LifecycleState.STOPPED: "Safe Stop completed.",
        LifecycleState.BLOCKED: "The engine stopped on a fail-closed blocker.",
        LifecycleState.ERROR: "The engine or operator operation ended with an error.",
    }
    suffix = getattr(control, "value", control)
    return details.get(lifecycle, lifecycle.value) + (f" Control: {suffix}." if suffix else "")


def _plain_blocker(value: str | None) -> str:
    if not value:
        return "None"
    lowered = value.casefold()
    message = next((text for key, text in _BLOCKER_MESSAGES.items() if key in lowered), "The engine reported a blocker.")
    return f"{message} ({value})"


def _yes_no(value: object) -> str:
    return "FOUND" if value is True else "NOT FOUND" if value is False else "UNKNOWN"


def _ready_text(value: object, *, unknown: str = "UNKNOWN") -> str:
    return "READY" if value is True else "NOT READY" if value is False else unknown


def _progress_text(value: Mapping[str, object] | None) -> str:
    if not value:
        return "—"
    return f"{value.get('label')}: {value.get('current')}/{value.get('total')}"


def _inventory_text(value: Mapping[str, object]) -> str:
    if not value:
        return "—"
    if value.get("known") is not True:
        return "Unknown"
    items = value.get("items") if isinstance(value.get("items"), list) else []
    summary: list[str] = []
    for item in items[:5]:
        if isinstance(item, Mapping):
            summary.append(f"{item.get('quantity')}× {item.get('name') or item.get('itemId')}")
    suffix = ", ".join(summary) if summary else "empty"
    return f"{value.get('occupiedSlots')}/{value.get('slotCount')} slots — {suffix}"


def _receipt_text(value: Mapping[str, object]) -> str:
    if not value:
        return "—"
    commands = value.get("commands") if isinstance(value.get("commands"), list) else []
    return (
        f"{value.get('status') or 'UNKNOWN'}; {len(commands)} commands; "
        f"activation={'yes' if value.get('activationAttempted') else 'no'}; "
        f"unresolved={value.get('unresolvedCommandCount', 0)}"
    )


def _compact_json(value: object) -> str:
    if value is None:
        return "—"
    text = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return text if len(text) <= 180 else text[:177] + "…"


def _bounded_text(value: object, maximum: int) -> str:
    text = str(value)
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _inspection_text(values: Mapping[str, object]) -> str:
    sections = (
        ("Validity", [f"valid: {values.get('valid')}", f"status: {values.get('status')}"]),
        ("Semantic timeline", values.get("semanticSummary") or values.get("semantic_summary") or []),
        ("Interacted entities", values.get("interactedEntities") or values.get("interacted_entities") or []),
        ("Route / movement facts", values.get("routePoints") or values.get("route_points") or []),
        ("Gaps", values.get("coverageGaps") or values.get("coverage_gaps") or []),
        ("Ambiguities", values.get("ambiguities") or []),
        ("Review-only suggestions", values.get("candidateSuggestions") or values.get("candidate_suggestions") or []),
        ("Errors", values.get("errors") or []),
    )
    lines: list[str] = []
    for title, content in sections:
        lines.append(title)
        lines.append("-" * len(title))
        if isinstance(content, (list, tuple)):
            lines.extend(f"• {_compact_json(item)}" for item in content)
        else:
            lines.append(_compact_json(content))
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> int:
    _enable_dpi_awareness()
    with _SingleInstance() as instance:
        if not instance.acquired:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(WINDOW_TITLE, "Another operator GUI instance is already running.", parent=root)
            root.destroy()
            return 2
        root = tk.Tk()
        try:
            controller = GuiController(EngineApplication())
            OperatorWindow(root, controller)
            root.mainloop()
        except Exception as error:
            try:
                root.withdraw()
                messagebox.showerror(WINDOW_TITLE, f"GUI startup failed: {type(error).__name__}: {error}", parent=root)
            finally:
                root.destroy()
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
