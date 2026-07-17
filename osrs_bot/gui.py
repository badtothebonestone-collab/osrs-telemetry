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
CONNECTION_REFRESH_MILLISECONDS = 1_000

_STATUS_COLORS = {
    "CONNECTING": ("#5f6368", "#ffffff"),
    "READY": ("#0f7d32", "#ffffff"),
    "OBSERVING": ("#1769aa", "#ffffff"),
    "RUNNING": ("#1769aa", "#ffffff"),
    "PAUSED": ("#8a5a00", "#ffffff"),
    "BLOCKED": ("#b00020", "#ffffff"),
    "COMPLETE": ("#0f7d32", "#ffffff"),
    "SAFE_STOPPED": ("#5f6368", "#ffffff"),
    "DISCONNECTED": ("#6b4f3a", "#ffffff"),
    "WAITING_FOR_NEXT_SCENE_UPDATE": ("#1769aa", "#ffffff"),
    "WAITING_FOR_SOURCE_COHERENCE": ("#1769aa", "#ffffff"),
    "INPUT_TRANSACTION_BUSY": ("#1769aa", "#ffffff"),
    "CURSOR_FEEDBACK_SETTLING": ("#1769aa", "#ffffff"),
    "ARDUINO_HEALTH_STALE": ("#8a5a00", "#ffffff"),
    "ARDUINO_COMMAND_FAILED": ("#b00020", "#ffffff"),
    "SENSOR_STALE": ("#8a5a00", "#ffffff"),
    "PRESENTATION_FRAME_STALE": ("#8a5a00", "#ffffff"),
    # Retained only so an older saved GUI payload remains readable.
    "STALE": ("#8a5a00", "#ffffff"),
    "ERROR": ("#b00020", "#ffffff"),
    "NOT READY": ("#b00020", "#ffffff"),
    "UNKNOWN": ("#5f6368", "#ffffff"),
    "REQUESTED": ("#8a5a00", "#ffffff"),
}

_BLOCKER_MESSAGES = {
    "endpoint": "RuneLite telemetry is unavailable.",
    "runelite_not_running": "RuneLite is not running.",
    "loaded_scene": (
        "Waiting for a coherent loaded scene; log in if needed, or let the "
        "current scene transition finish."
    ),
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
        self.keep_terminal_var = tk.BooleanVar(
            value=settings.keep_terminal_summary_visible
        )
        self.frame_section_var = tk.StringVar(value="Current live frame")
        self.terminal_summary_var = tk.StringVar(value="No terminal run summary.")
        self.movement_diagnostics_var = tk.StringVar(
            value=_movement_diagnostics_text({})
        )

        self.connection_vars = {
            key: tk.StringVar(value="UNKNOWN")
            for key in (
                "process",
                "pid",
                "endpoint",
                "loaded",
                "game_state",
                "foreground",
                "freshness",
                "session",
                "source_tick",
                "age",
                "last_updated",
                "layout",
                "cursor",
                "blocker",
                "diagnostic",
            )
        }
        self.live_vars = {
            key: tk.StringVar(value="—")
            for key in (
                "task",
                "run_id",
                "mode",
                "frame_status",
                "freshness",
                "source_identity",
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
                "diagnostic",
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
            ("RuneLite", "process", "Current PID", "pid"),
            ("Endpoint 8893", "endpoint", "Current session", "session"),
            ("Loaded scene", "loaded", "Game state", "game_state"),
            ("Fresh coherent state", "freshness", "Source tick", "source_tick"),
            ("Connection source age", "age", "Connection source time", "last_updated"),
            ("175% fixed layout", "layout", "Cursor", "cursor"),
            ("Foreground", "foreground", "", ""),
        )
        for row, (left_name, left_key, right_name, right_key) in enumerate(rows):
            ttk.Label(connection, text=f"{left_name}:").grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=2
            )
            ttk.Label(connection, textvariable=self.connection_vars[left_key]).grid(
                row=row, column=1, sticky="w", pady=2
            )
            if right_key:
                ttk.Label(connection, text=f"{right_name}:").grid(
                    row=row, column=2, sticky="w", padx=(14, 6), pady=2
                )
                ttk.Label(connection, textvariable=self.connection_vars[right_key]).grid(
                    row=row, column=3, sticky="w", pady=2
                )
        ttk.Label(connection, text="Current blocker:").grid(
            row=7, column=0, sticky="nw", pady=(5, 2)
        )
        ttk.Label(
            connection,
            textvariable=self.connection_vars["blocker"],
            wraplength=560,
        ).grid(row=7, column=1, columnspan=3, sticky="ew", pady=(5, 2))
        connection_buttons = ttk.Frame(connection)
        connection_buttons.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(9, 0))
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
        self.keep_terminal_check = ttk.Checkbutton(
            runtime,
            text="Keep terminal summary visible",
            variable=self.keep_terminal_var,
            command=self._terminal_preference_changed,
        )
        self.keep_terminal_check.grid(
            row=3, column=0, columnspan=3, sticky="w", pady=4
        )
        controls = ttk.Frame(runtime)
        controls.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
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
            "status", width=self._px(220), stretch=False, anchor="center"
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
        self.live_tab.rowconfigure(3, weight=1)
        summary = ttk.LabelFrame(
            self.live_tab,
            text=self.frame_section_var.get(),
            style="Section.TLabelframe",
            padding=10,
        )
        self.frame_summary = summary
        summary.grid(row=0, column=0, sticky="ew")
        for column in (1, 3):
            summary.columnconfigure(column, weight=1)
        items = (
            ("Run ID", "run_id", "Mode", "mode"),
            ("Source frame", "frame_status", "Freshness", "freshness"),
            ("Source PID/session", "source_identity", "Task", "task"),
            ("Profile / definition", "binding", "Task state", "state"),
            ("Route step", "route_step", "Route progress", "route_progress"),
            ("Cycle progress", "cycle_progress", "Player location", "location"),
            ("Inventory", "inventory", "Selected target", "target"),
            ("Selected action", "action", "Target distance", "distance"),
            ("Candidates", "candidates", "Pending verification", "pending_verification"),
            ("Last outcome", "last_outcome", "Execution", "execution"),
            ("Input receipt", "receipt", "Cleanup", "cleanup"),
            ("Blocker", "blocker", "Original diagnostic", "diagnostic"),
        )
        for row, (a_label, a_key, b_label, b_key) in enumerate(items):
            ttk.Label(summary, text=f"{a_label}:").grid(row=row, column=0, sticky="nw", padx=(0, 6), pady=2)
            ttk.Label(summary, textvariable=self.live_vars[a_key], wraplength=420).grid(row=row, column=1, sticky="w", pady=2)
            ttk.Label(summary, text=f"{b_label}:").grid(row=row, column=2, sticky="nw", padx=(18, 6), pady=2)
            ttk.Label(summary, textvariable=self.live_vars[b_key], wraplength=420).grid(row=row, column=3, sticky="w", pady=2)

        movement = ttk.LabelFrame(
            self.live_tab,
            text="Movement / camera / targeting diagnostics",
            style="Section.TLabelframe",
            padding=8,
        )
        movement.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        movement.columnconfigure(0, weight=1)
        ttk.Label(
            movement,
            textvariable=self.movement_diagnostics_var,
            font=("Consolas", 9),
            justify="left",
            wraplength=1_050,
        ).grid(row=0, column=0, sticky="ew")

        terminal = ttk.LabelFrame(
            self.live_tab,
            text="Terminal summary",
            style="Section.TLabelframe",
            padding=8,
        )
        terminal.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            terminal,
            textvariable=self.terminal_summary_var,
            wraplength=1_050,
        ).grid(row=0, column=0, sticky="ew")
        terminal.columnconfigure(0, weight=1)

        panes = ttk.Panedwindow(self.live_tab, orient="horizontal")
        panes.grid(row=3, column=0, sticky="nsew", pady=10)
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
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Copy Current Status", command=self._copy_status).pack(side="left")
        ttk.Button(actions, text="Save Current Status", command=self._save_status).pack(side="left", padx=6)
        ttk.Button(actions, text="Open Latest Proof Folder", command=self._open_latest_proof).pack(side="left")
        ttk.Button(
            actions,
            text="Clear Historical Display",
            command=lambda: self._safe_request(self.controller.clear_historical_display),
        ).pack(side="left", padx=6)

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

    def _terminal_preference_changed(self) -> None:
        try:
            self.controller.save_preferences(
                keep_terminal_summary_visible=bool(self.keep_terminal_var.get())
            )
        except Exception:
            pass

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
            if (
                not self._closing
                and "connection" not in state.busy_operations
                and now - self._last_connection_refresh
                >= CONNECTION_REFRESH_MILLISECONDS / 1_000.0
            ):
                self._last_connection_refresh = now
                self._safe_request(self.controller.refresh_connection, show_error=False)
                port = self.arduino_var.get().strip().upper()
                if port:
                    self._safe_request(
                        lambda: self.controller.request_arduino_readiness(port),
                        show_error=False,
                    )
            elif "connection" in state.busy_operations:
                # Leave a full refresh interval after a slow endpoint probe so
                # explicit Launch/Connect and recovery controls become usable.
                self._last_connection_refresh = now
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
        presentation = state.presentation
        presentation_state = _presentation_state_text(presentation)
        self.lifecycle_var.set(presentation_state)
        colors = _STATUS_COLORS.get(
            presentation_state, _STATUS_COLORS["UNKNOWN"]
        )
        self.lifecycle_badge.configure(background=colors[0], foreground=colors[1])
        presentation_blockers = tuple(getattr(presentation, "blockers", ()))
        blocker = (
            presentation_blockers[0]
            if presentation_blockers
            else state.blockers[0]
            if state.blockers
            else None
        )
        guidance = getattr(presentation, "reconnect_guidance", None)
        wait_elapsed_millis = getattr(presentation, "wait_elapsed_millis", None)
        if (
            isinstance(wait_elapsed_millis, int)
            and not isinstance(wait_elapsed_millis, bool)
        ):
            guidance = (
                f"{guidance or 'Passive wait evidence.'} "
                f"Wait elapsed: {wait_elapsed_millis} ms."
            )
        self.header_detail_var.set(
            f"{_plain_blocker(blocker)} {guidance or ''}".strip()
            if blocker
            else guidance
            or _lifecycle_detail(application.lifecycle, application.runtime_control)
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
        frame_payload = (
            state.engine_frame.to_dict() if state.engine_frame is not None else {}
        )
        self._render_connection(connection, frame_payload, presentation)
        self._render_preflight(
            connection,
            arduino,
            overlay,
            diagnostics,
            state,
            presentation,
            frame_payload,
        )
        self._render_frame(
            state.engine_frame, presentation, application, frame_payload
        )
        self._render_terminal_summary(
            presentation,
            state.engine_frame,
            arduino,
            state.terminal_summary_evidence,
            application,
            frame_payload,
        )
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
        self._render_button_states(state, presentation)
        if (
            self._closing
            and not state.close_ready
            and "close" not in state.busy_operations
        ):
            self._closing = False

    def _render_connection(
        self,
        connection: Mapping[str, object],
        frame_payload: Mapping[str, object],
        presentation: object,
    ) -> None:
        observation = frame_payload.get("observation")
        observation = observation if isinstance(observation, Mapping) else {}
        process_found = _runelite_found(connection)
        pid = getattr(presentation, "process_id", None) or _first(
            connection,
            "runelitePid",
            "pid",
            "processId",
            default=observation.get("processId"),
        )
        session = getattr(presentation, "session_id", None) or _first(
            connection,
            "sessionId",
            "session_id",
            default=observation.get("sessionId"),
        )
        connection_presentation_state = _presentation_state_text(presentation)
        if connection_presentation_state == "DISCONNECTED":
            pid = None
            session = None
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
        self.connection_vars["session"].set(str(session) if session else "—")
        self.connection_vars["endpoint"].set(_ready_text(endpoint))
        self.connection_vars["loaded"].set(_ready_text(loaded))
        self.connection_vars["game_state"].set(str(game_state or "UNKNOWN"))
        self.connection_vars["foreground"].set(_ready_text(focused))
        presentation_state = _presentation_state_text(presentation)
        self.connection_vars["freshness"].set(
            "READY"
            if getattr(presentation, "start_live_allowed", False)
            or getattr(presentation, "current", False)
            else presentation_state
        )
        source_tick = getattr(presentation, "connection_source_tick", None)
        self.connection_vars["source_tick"].set(
            str(source_tick) if source_tick is not None else "—"
        )
        age = getattr(presentation, "connection_age_seconds", None)
        self.connection_vars["age"].set(
            f"{age:.1f} s" if isinstance(age, (int, float)) else "—"
        )
        updated = getattr(presentation, "connection_last_updated_at", None)
        self.connection_vars["last_updated"].set(
            updated.astimezone().strftime("%H:%M:%S")
            if isinstance(updated, datetime)
            else "—"
        )
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
        presentation: object,
        frame_payload: Mapping[str, object],
    ) -> None:
        observation = frame_payload.get("observation")
        observation = observation if isinstance(observation, Mapping) else {}
        cleanup = frame_payload.get("cleanup")
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
        if getattr(presentation, "age_seconds", None) is not None:
            coherent = bool(
                getattr(presentation, "current", False)
                or getattr(presentation, "start_live_allowed", False)
            )
        presentation_blockers = tuple(getattr(presentation, "blockers", ()))
        repository_ready = _first(
            diagnostics,
            "repositoryReady",
            "repository_ready",
            default=bool(diagnostics.get("commit")) if diagnostics else None,
        )
        freshness_states = {
            "WAITING_FOR_NEXT_SCENE_UPDATE",
            "WAITING_FOR_SOURCE_COHERENCE",
            "SENSOR_STALE",
            "PRESENTATION_FRAME_STALE",
        }
        arduino_states = {
            "INPUT_TRANSACTION_BUSY",
            "CURSOR_FEEDBACK_SETTLING",
            "ARDUINO_HEALTH_STALE",
            "ARDUINO_COMMAND_FAILED",
        }
        rows = (
            ("Repository / application", _status(repository_ready), _detail(diagnostics, "repositoryReason", "repository_reason")),
            ("RuneLite found", _status(_runelite_found(connection)), _detail(connection, "processReason", "process_reason")),
            ("Endpoint healthy", _status(_first(connection, "endpointHealthy", "endpoint_healthy")), _detail(connection, "endpointReason", "endpoint_reason")),
            ("Loaded scene", _presentation_scoped_status(_first(connection, "loadedScene", "loaded_scene", default=observation.get("loadedScene")), presentation, freshness_states), str(_first(connection, "gameState", "game_state", default=observation.get("gameState")) or "")),
            ("Coherent fresh Observation", _presentation_scoped_status(coherent, presentation, freshness_states), "fresh + wall-clock fresh + source coherent" if coherent else ""),
            ("Supported 175% fixed layout", _status(_first(connection, "supportedLayout", "supported_layout", "layoutSupported")), _detail(connection, "layoutReason", "layout_reason")),
            ("Exact process / session binding", _status(_first(connection, "exactBinding", "exact_binding", "sessionBound", "exactProcessBinding")), _detail(connection, "bindingReason", "binding_reason", "diagnostic")),
            ("Start Live gate", _presentation_scoped_status(getattr(presentation, "start_live_allowed", False), presentation, freshness_states | arduino_states), getattr(presentation, "reconnect_guidance", None) or "fresh coherent loaded identity required"),
            ("Arduino port available", _presentation_scoped_status(_first(arduino, "portAvailable", "port_available", "available"), presentation, arduino_states), _detail(arduino, "portReason", "port_reason")),
            ("Arduino lease available", _presentation_scoped_status(_first(arduino, "leaseAvailable", "lease_available"), presentation, arduino_states), _detail(arduino, "leaseReason", "lease_reason")),
            ("Overlay", _overlay_status(overlay), _detail(overlay, "error", "detail")),
            ("Current blocker", _presentation_blocker_status(presentation, bool(presentation_blockers or state.blockers)), _plain_blocker((presentation_blockers or state.blockers)[0]) if presentation_blockers or state.blockers else "None"),
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

    def _render_frame(
        self,
        frame: Any,
        presentation: object,
        application: object,
        frame_payload: Mapping[str, object],
    ) -> None:
        if frame is None:
            self.frame_section_var.set("No current or historical frame")
            self.frame_summary.configure(text=self.frame_section_var.get())
            for variable in self.live_vars.values():
                variable.set("—")
            self.movement_diagnostics_var.set(_movement_diagnostics_text({}))
            self._set_text(self.safety_text, "No EngineFrame has been published.")
            return
        current = bool(getattr(presentation, "current", False))
        terminal = bool(getattr(presentation, "terminal_summary", False))
        self.frame_section_var.set(
            "Current live frame"
            if current
            else "Terminal frame — target geometry cleared"
            if terminal
            else "Last known frame — historical, not live authority"
        )
        self.frame_summary.configure(text=self.frame_section_var.get())
        payload = frame_payload
        self.movement_diagnostics_var.set(_movement_diagnostics_text(payload))
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
        self.live_vars["run_id"].set(
            str(getattr(presentation, "frame_run_id", None) or "—")
        )
        self.live_vars["mode"].set(
            "START LIVE — Arduino production input"
            if getattr(application, "execute_requested", False)
            else "OBSERVE ONLY — no gameplay input"
        )
        self.live_vars["frame_status"].set(
            f"EngineFrame {payload.get('sequence')} / {payload.get('stage')} / "
            f"{getattr(getattr(presentation, 'state', None), 'value', 'UNKNOWN')}"
        )
        age = getattr(presentation, "age_seconds", None)
        source_tick = getattr(presentation, "source_tick", None)
        self.live_vars["freshness"].set(
            f"{'CURRENT' if current else 'HISTORICAL'}; age={age:.1f}s; "
            f"sourceTick={source_tick}"
            if isinstance(age, (int, float))
            else "No authoritative freshness timestamp"
        )
        source_pid = getattr(presentation, "source_process_id", None)
        source_session = getattr(presentation, "source_session_id", None)
        self.live_vars["source_identity"].set(
            f"Previous PID {source_pid} / session {source_session}"
            if not current and (source_pid is not None or source_session is not None)
            else f"PID {source_pid} / session {source_session}"
            if source_pid is not None or source_session is not None
            else "—"
        )
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
        if selected and not current:
            self.live_vars["target"].set(
                f"Last known: {self.live_vars['target'].get()}"
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
        self.live_vars["receipt"].set(
            _receipt_text(
                receipt,
                activation_attempted=last_execution.get("activationAttempted"),
            )
        )
        self.live_vars["cleanup"].set(_cleanup_detail(cleanup, None))
        self.live_vars["blocker"].set(_plain_blocker(str(payload.get("blocker"))) if payload.get("blocker") else "None")
        self.live_vars["diagnostic"].set(
            str(getattr(presentation, "diagnostic", None) or "None")
        )
        checks = payload.get("safetyChecks") if isinstance(payload.get("safetyChecks"), list) else []
        lines = [
            f"{index + 1:02d}. {'PASS' if check.get('allowed') else 'BLOCK'}  {check.get('stage')} / {check.get('code')}"
            for index, check in enumerate(checks)
            if isinstance(check, Mapping)
        ]
        if not current:
            lines.insert(
                0,
                "HISTORICAL — safety results below are not current authorization.",
            )
        self._set_text(self.safety_text, "\n".join(lines) if lines else "No safety evaluation published for this frame.")

    def _render_terminal_summary(
        self,
        presentation: object,
        frame: Any,
        arduino: Mapping[str, object],
        retained: Any,
        application: Any,
        frame_payload: Mapping[str, object],
    ) -> None:
        retained_only = retained if frame is None else None
        runtime_status = str(
            getattr(retained_only, "status", None)
            or getattr(presentation, "runtime_status", "")
            or ""
        ).upper()
        presentation_state = str(
            getattr(getattr(presentation, "state", None), "value", "")
        )
        is_terminal = retained_only is not None or runtime_status in {
            "COMPLETE",
            "DRY_RUN",
            "BLOCKED",
            "LIMIT",
            "SAFE_STOPPED",
            "ERROR",
        } or presentation_state in {"COMPLETE", "BLOCKED", "SAFE_STOPPED", "ERROR"}
        if not is_terminal:
            self.terminal_summary_var.set("No terminal run summary.")
            return
        if not self.keep_terminal_var.get():
            self.terminal_summary_var.set(
                "Terminal summary hidden by preference; the immutable frame and cleanup remain in saved status evidence."
            )
            return
        cleanup = (
            getattr(retained_only, "cleanup", None)
            if retained_only is not None
            else getattr(presentation, "cleanup", None)
        )
        last_execution = (
            frame_payload.get("lastExecution")
            if isinstance(frame_payload.get("lastExecution"), Mapping)
            else {}
        )
        receipt = (
            last_execution.get("receipt")
            if isinstance(last_execution.get("receipt"), Mapping)
            else {}
        )
        unresolved = (
            getattr(retained_only, "unresolved_command_count", None)
            if retained_only is not None
            else receipt.get("unresolvedCommandCount")
        )
        current_lease = _first(
            arduino,
            "leaseStatus",
            "lease_status",
            default="UNKNOWN — not separately published",
        )
        terminal_reason = (
            getattr(retained_only, "reason", None)
            if retained_only is not None
            else getattr(presentation, "terminal_reason", None)
        )
        terminal_outcome = (
            getattr(retained_only, "outcome", None)
            if retained_only is not None
            else getattr(presentation, "terminal_outcome", None)
        )

        def cleanup_flag(name: str, yes: str, no: str) -> str:
            value = getattr(cleanup, name, None)
            return yes if value is True else no if value is False else "UNKNOWN"

        evidence_path = getattr(application, "live_evidence_path", None)
        lines = (
            f"{runtime_status or presentation_state} — "
            f"{terminal_reason or 'no terminal reason published'}",
            f"Final typed outcome: {_compact_json(terminal_outcome)}",
            "Final cleanup: "
            f"STOP_ALL={cleanup_flag('stop_all_acknowledged', 'ACK', 'NO')}; "
            f"DISARM={cleanup_flag('disarm_acknowledged', 'ACK', 'NO')}; "
            f"zero held keys={cleanup_flag('zero_held_keys', 'YES', 'NO')}; "
            f"zero held mouse buttons={cleanup_flag('zero_held_mouse_buttons', 'YES', 'NO')}; "
            f"unresolved commands={unresolved if unresolved is not None else 'UNKNOWN'}; "
            "final lease state=UNKNOWN — not separately published; "
            f"ledger closed={cleanup_flag('ledger_closed', 'YES', 'NO')}; "
            f"backend closed={cleanup_flag('backend_closed', 'YES', 'NO')}",
            f"Current Arduino lease readiness (not final receipt): {current_lease}",
            f"Live evidence: {evidence_path or 'not recorded for this run'}",
        )
        self.terminal_summary_var.set("\n".join(lines))

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
        reference = application.recent_demonstration
        if application.lifecycle is LifecycleState.DEMONSTRATION_STOP_REQUESTED:
            self.demo_status_var.set("Stop requested; finalizing and inspecting evidence")
        elif application.active_capture_id:
            elapsed = ""
            if application.started_at:
                elapsed = f" — {(datetime.now(application.started_at.tzinfo) - application.started_at).total_seconds():.1f}s"
            self.demo_status_var.set(f"Recording {application.active_capture_id}{elapsed}")
        else:
            self.demo_status_var.set(
                _demonstration_terminal_status(reference, application.lifecycle)
            )
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

    def _render_button_states(self, state: Any, presentation: object) -> None:
        lifecycle = state.application.lifecycle
        active_run = state.application.active_run_id is not None
        active_demo = state.application.active_capture_id is not None
        busy = set(state.busy_operations)
        busy_details = dict(state.busy_operation_details)
        connection_action_busy = (
            busy_details.get("connection") not in {None, "refresh-connection"}
        )
        idle_mode = not active_run and not active_demo and state.pending_mode is None
        live_selected = self.mode_var.get() == "live"
        start_ready = bool(
            idle_mode
            and "mode-start" not in busy
            and (
                not live_selected
                or getattr(presentation, "start_live_allowed", False)
            )
        )
        self.start_button.configure(
            text="Start Live" if live_selected else "Start Observe Only",
            state="normal" if start_ready else "disabled",
        )
        self.pause_button.configure(state="normal" if lifecycle is LifecycleState.RUNNING else "disabled")
        self.resume_button.configure(state="normal" if lifecycle in {LifecycleState.PAUSED, LifecycleState.PAUSE_REQUESTED} else "disabled")
        self.stop_button.configure(state="normal" if active_run else "disabled")
        self.record_demo_button.configure(state="normal" if idle_mode else "disabled")
        self.stop_demo_button.configure(state="normal" if active_demo else "disabled")
        self.launch_button.configure(
            state="disabled" if connection_action_busy or not idle_mode else "normal"
        )
        self.login_button.configure(
            state="disabled" if connection_action_busy or not idle_mode else "normal"
        )
        self.refresh_button.configure(state="disabled" if "connection" in busy else "normal")

    def _status_payload(self) -> dict[str, object]:
        state = self.controller.snapshot()
        return {
            "schema": GUI_PRESENTATION_SCHEMA,
            "capturedAt": datetime.now().astimezone().isoformat(),
            "application": state.application.to_dict(),
            "presentation": _jsonable(state.presentation),
            "terminalSummary": _jsonable(state.terminal_summary_evidence),
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
                keep_terminal_summary_visible=bool(self.keep_terminal_var.get()),
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


def _movement_diagnostics_text(payload: Mapping[str, object]) -> str:
    """Format bounded EngineFrame diagnostics without adding decision authority."""

    route = _diagnostic_mapping(payload.get("route"))
    camera = _diagnostic_mapping(payload.get("camera"))
    targeting = _diagnostic_mapping(payload.get("targeting"))
    pointer = _diagnostic_mapping(payload.get("pointer"))
    timing = _diagnostic_mapping(payload.get("timing"))
    verification = _diagnostic_mapping(payload.get("lastVerification"))
    outcome = _diagnostic_mapping(verification.get("outcome"))
    camera_result = _diagnostic_mapping(outcome.get("cameraPoseResult"))
    observation = _diagnostic_mapping(payload.get("observation"))
    suppress_camera_coordinates = _coordinate_provenance_is_stale(
        camera,
        observation,
    )
    suppress_targeting_coordinates = _coordinate_provenance_is_stale(
        _diagnostic_mapping(payload.get("selectedTarget")),
        observation,
    )
    suppressed = "suppressed (awaiting fresh decision)"
    camera_target_fields = (
        (("region", suppressed), ("target", suppressed))
        if suppress_camera_coordinates
        else (
            ("region", _diagnostic_bounds(camera.get("desiredFramingRegion"))),
            ("target", _diagnostic_point(camera.get("targetScreenPosition"))),
        )
    )
    targeting_coordinate_fields = (
        (("shape", suppressed), ("inset", suppressed), ("selected", suppressed))
        if suppress_targeting_coordinates
        else (
            ("shape", _diagnostic_bounds(targeting.get("targetShapeBounds"))),
            ("inset", _diagnostic_bounds(targeting.get("insetAimRegion"))),
            ("selected", _diagnostic_point(targeting.get("selectedPoint"))),
        )
    )
    targeting_identity_fields = (
        ("seed", _diagnostic_text(targeting.get("selectionSeed"), 14)),
        ("id", _diagnostic_text(targeting.get("decisionId"), 24)),
    )

    lines = [
        _diagnostic_line(
            "ROUTE",
            route,
            (
                ("progress", _diagnostic_number(route.get("currentProgressTiles"), "t")),
                ("remaining", _diagnostic_number(route.get("remainingDistanceTiles"), "t")),
                ("target", _diagnostic_text(route.get("selectedRouteTarget"))),
                ("request", _diagnostic_number(route.get("requestedTileDistance"), "t")),
                ("actual", _diagnostic_number(route.get("actualProgressTiles"), "t")),
                ("skipped", _diagnostic_count(route.get("skippedGuidancePoints"))),
                ("rejected", _diagnostic_count(route.get("candidateRejections"))),
                (
                    "why",
                    _diagnostic_route_rejection(route.get("candidateRejections")),
                ),
                ("fallback", _diagnostic_text(route.get("fallbackReason"))),
            ),
        ),
        _diagnostic_line(
            "CAMERA",
            camera or observation,
             (
                 ("ctx", _diagnostic_text(camera.get("framingContext"), 12)),
                 ("frame", _diagnostic_text(camera.get("framingClassification"))),
                 ("zoom", _diagnostic_camera_zoom(observation)),
                 ("action", _diagnostic_text(camera.get("cameraAction"))),
                 ("hold", _diagnostic_number(camera.get("holdDurationMillis"), "ms")),
                 (
                     "correction",
                     _diagnostic_number(camera.get("correctionDistancePx"), "px"),
                 ),
                 (
                     "vector",
                     _diagnostic_point(camera.get("screenCorrection")),
                 ),
                 ("edge", _diagnostic_camera_edge(camera)),
                 ("ahead", _diagnostic_number(camera.get("lookaheadPointCount"))),
                 ("pulse", _diagnostic_camera_attempt(camera)),
                 ("total", _diagnostic_number(camera.get("cumulativeHoldMillis"), "ms")),
                 ("result", _diagnostic_camera_result(camera_result)),
                 ("bias", _diagnostic_text(camera.get("routeDirectionBias"))),
             )
             + camera_target_fields,
         ),
        _diagnostic_line(
            "TARGET",
             targeting,
             (
                 ("geometry", _diagnostic_text(targeting.get("authoritativeGeometrySource"))),
             )
             + targeting_coordinate_fields
             + (
                 ("candidates", _diagnostic_number(targeting.get("candidatePointCount"))),
                 ("score", _diagnostic_number(targeting.get("selectedCandidateScore"))),
                 ("previous", _diagnostic_count(targeting.get("previousSelectedPoints"))),
                 ("rejected", _diagnostic_count(targeting.get("rejectedPointReasons"))),
             )
             + targeting_identity_fields,
        ),
        _diagnostic_line(
            "POINTER",
            pointer,
            (
                ("distance", _diagnostic_number(pointer.get("directDistancePx"), "px")),
                ("path", _diagnostic_number(pointer.get("plannedPathLengthPx"), "px")),
                ("duration", _diagnostic_number(pointer.get("plannedDurationSeconds"), "s")),
                ("style", _diagnostic_text(pointer.get("style"))),
                ("settled", _diagnostic_point(pointer.get("settledTarget"))),
                ("seed", _diagnostic_text(pointer.get("seed"), 14)),
            ),
        ),
        _diagnostic_line(
            "TIMING",
            timing,
            (
                ("pre-move", _diagnostic_number(timing.get("preMoveDelaySeconds"), "s")),
                ("settle", _diagnostic_number(timing.get("settleDelaySeconds"), "s")),
                ("pre-click", _diagnostic_number(timing.get("preClickDelaySeconds"), "s")),
                ("post", _diagnostic_number(timing.get("postActionDelaySeconds"), "s")),
                ("route-pause", _diagnostic_number(timing.get("routePauseSeconds"), "s")),
            ),
        ),
    ]
    return "\n".join(lines)


def _coordinate_provenance_is_stale(
    decision: Mapping[str, object],
    observation: Mapping[str, object],
) -> bool:
    """Return true only for an explicit decision/observation mismatch."""

    provenance = (
        decision.get("sourceTick"),
        decision.get("geometryFrameId"),
        observation.get("sourceTick"),
        observation.get("geometryFrameId"),
    )
    if any(value is None for value in provenance):
        return False
    decision_tick, decision_geometry, observation_tick, observation_geometry = provenance
    return (
        decision_tick != observation_tick
        or decision_geometry != observation_geometry
    )


def _diagnostic_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _diagnostic_line(
    label: str,
    values: Mapping[str, object],
    fields: tuple[tuple[str, str], ...],
) -> str:
    if not values:
        return f"{label:<7} -"
    rendered = "  ".join(f"{name}={value}" for name, value in fields)
    return _bounded_text(f"{label:<7} {rendered}", 230)


def _diagnostic_number(value: object, suffix: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    if isinstance(value, int):
        rendered = str(value)
    else:
        rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _diagnostic_text(value: object, maximum: int = 32) -> str:
    if value is None:
        return "-"
    return _bounded_text(str(value).replace("\n", " "), maximum)


def _diagnostic_count(value: object) -> str:
    return str(len(value)) if isinstance(value, (list, tuple)) else "-"


def _diagnostic_camera_zoom(observation: Mapping[str, object]) -> str:
    zoom = observation.get("cameraZoom3d")
    classification = observation.get("cameraZoomClassification")
    desired = _diagnostic_mapping(observation.get("desiredCameraZoomRange"))
    minimum = desired.get("min")
    maximum = desired.get("max")
    if isinstance(zoom, bool) or not isinstance(zoom, int):
        return "-"
    state = _diagnostic_text(classification, 12)
    band = (
        f"{minimum}-{maximum}"
        if isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        else "-"
    )
    return f"{zoom}/{state}[{band}]"


def _diagnostic_route_rejection(value: object) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "-"
    first = _diagnostic_mapping(value[0])
    step = _diagnostic_text(first.get("stepId"), 24)
    codes = first.get("rejectionCodes")
    code = (
        _diagnostic_text(codes[0], 24)
        if isinstance(codes, (list, tuple)) and codes
        else "-"
    )
    return _bounded_text(f"{step}:{code}", 52)


def _diagnostic_camera_edge(camera: Mapping[str, object]) -> str:
    clearance = _diagnostic_number(camera.get("edgeClearancePx"))
    required = _diagnostic_number(camera.get("requiredEdgeMarginPx"))
    return "-" if clearance == "-" and required == "-" else f"{clearance}/{required}px"


def _diagnostic_camera_attempt(camera: Mapping[str, object]) -> str:
    attempt = _diagnostic_number(camera.get("correctionAttempt"))
    limit = _diagnostic_number(camera.get("correctionLimit"))
    return "-" if attempt == "-" and limit == "-" else f"{attempt}/{limit}"


def _diagnostic_camera_result(result: Mapping[str, object]) -> str:
    if not result:
        return "-"
    yaw = _diagnostic_number(result.get("yawDelta"))
    pitch = _diagnostic_number(result.get("pitchDelta"))
    return _bounded_text(f"yaw{yaw},pitch{pitch}", 28)


def _diagnostic_point(value: object) -> str:
    point = _diagnostic_mapping(value)
    x = point.get("x")
    y = point.get("y")
    if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
        return "-"
    return f"({x},{y})"


def _diagnostic_bounds(value: object) -> str:
    bounds = _diagnostic_mapping(value)
    parts = tuple(bounds.get(key) for key in ("x", "y", "width", "height"))
    if any(not isinstance(part, int) or isinstance(part, bool) for part in parts):
        return "-"
    x, y, width, height = parts
    return f"({x},{y} {width}x{height})"


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


def _presentation_state_value(value: object, default: str = "CONNECTING") -> str:
    raw = getattr(value, "value", value)
    text = str(raw).strip() if raw is not None else ""
    return text.upper() if text else default


def _presentation_state_text(presentation: object, *, display: bool = True) -> str:
    exact = _presentation_state_value(getattr(presentation, "state", None))
    if not display:
        return exact
    displayed = getattr(presentation, "display_state", None)
    return _presentation_state_value(displayed, exact) if displayed is not None else exact


def _presentation_scoped_status(
    value: object,
    presentation: object,
    scoped_states: set[str],
) -> str:
    """Render passive state without changing the exact readiness fact."""

    if value is True:
        return "READY"
    exact = _presentation_state_text(presentation, display=False)
    displayed = _presentation_state_text(presentation)
    if exact == "ARDUINO_COMMAND_FAILED" and exact in scoped_states:
        return exact
    if exact in scoped_states:
        if displayed in scoped_states:
            return displayed
        # During the bounded stale-display hold, show a neutral transition;
        # the exact gate supplied in ``value`` is already false immediately.
        return "CONNECTING"
    return _status(value)


def _presentation_blocker_status(
    presentation: object,
    has_blocker: bool,
) -> str:
    if not has_blocker:
        return "READY"
    exact = _presentation_state_text(presentation, display=False)
    displayed = _presentation_state_text(presentation)
    passive = {
        "WAITING_FOR_NEXT_SCENE_UPDATE",
        "WAITING_FOR_SOURCE_COHERENCE",
        "INPUT_TRANSACTION_BUSY",
        "CURSOR_FEEDBACK_SETTLING",
        "ARDUINO_HEALTH_STALE",
        "SENSOR_STALE",
        "PRESENTATION_FRAME_STALE",
    }
    if exact == "ARDUINO_COMMAND_FAILED":
        return exact
    if exact in passive:
        return displayed if displayed in passive else "CONNECTING"
    return "BLOCKED"


def _status(value: object) -> str:
    if value is True:
        return "READY"
    if value is False:
        return "NOT READY"
    if isinstance(value, str):
        normalized = value.upper().strip()
        for candidate in (
            normalized,
            normalized.replace(" ", "_"),
            normalized.replace("_", " "),
        ):
            if candidate in _STATUS_COLORS:
                return candidate
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


def _receipt_text(
    value: Mapping[str, object],
    *,
    activation_attempted: object = None,
) -> str:
    if not value:
        return "—"
    commands = value.get("commands") if isinstance(value.get("commands"), list) else []
    activation = (
        "yes"
        if activation_attempted is True
        else "no"
        if activation_attempted is False
        else "unknown"
    )
    parts = [
        f"{value.get('status') or 'UNKNOWN'}; {len(commands)} commands; "
        f"activation={activation}; "
        f"unresolved={value.get('unresolvedCommandCount', 0)}"
    ]

    mode = value.get("mode")
    if isinstance(mode, str) and mode:
        parts.append(f"mode={mode}")

    required = value.get("requiredCapabilities")
    if isinstance(required, list):
        operations = [
            item.get("operation")
            for item in required
            if isinstance(item, Mapping)
            and isinstance(item.get("operation"), str)
            and item.get("operation")
        ]
        if operations:
            parts.append("requires=" + ",".join(operations))

    negotiated = value.get("negotiatedCapabilities")
    if isinstance(negotiated, Mapping):
        protocol = negotiated.get("protocolVersion")
        firmware = negotiated.get("firmwareVersion")
        capability_bits: list[str] = []
        if isinstance(protocol, str) and protocol:
            capability_bits.append(protocol)
        if isinstance(firmware, str) and firmware:
            capability_bits.append(f"fw={firmware}")
        max_camera_hold = negotiated.get("maxCameraHoldMs")
        if isinstance(max_camera_hold, int) and not isinstance(
            max_camera_hold, bool
        ):
            capability_bits.append(f"camera<={max_camera_hold}ms")
        max_wheel_step = negotiated.get("maxWheelStep")
        if isinstance(max_wheel_step, int) and not isinstance(
            max_wheel_step, bool
        ):
            capability_bits.append(f"wheel<={max_wheel_step}")
        if capability_bits:
            parts.append("negotiated=" + "/".join(capability_bits))

    boundary = value.get("activationBoundary")
    if isinstance(boundary, Mapping):
        command = boundary.get("command")
        boundary_bits = [str(command)] if isinstance(command, str) and command else []
        if boundary.get("requestedDurationMillis") is not None:
            boundary_bits.append(
                "duration="
                f"{boundary.get('requestedDurationMillis')}->"
                f"{boundary.get('appliedDurationMillis')}ms"
            )
        if boundary.get("requestedWheelAmount") is not None:
            boundary_bits.append(
                "wheel="
                f"{boundary.get('requestedWheelAmount')}->"
                f"{boundary.get('appliedWheelAmount')}"
            )
        if boundary.get("acknowledged") is True:
            boundary_bits.append("ACK")
        elif boundary.get("attempted") is True:
            boundary_bits.append("unacknowledged")
        if boundary_bits:
            parts.append("boundary=" + "/".join(boundary_bits))

    camera_verification = value.get("cameraVerification")
    if isinstance(camera_verification, Mapping):
        kind = camera_verification.get("kind")
        status = camera_verification.get("status")
        if isinstance(kind, str) and kind and isinstance(status, str) and status:
            parts.append(f"verify={kind}/{status}")

    return "; ".join(parts)


def _compact_json(value: object) -> str:
    if value is None:
        return "—"
    text = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return text if len(text) <= 180 else text[:177] + "…"


def _first_present(values: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        value = values.get(key)
        if value is not None:
            return value
    return None


def _inspection_sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _manual_walk_targets(values: Mapping[str, object]) -> list[object]:
    direct = _first_present(
        values,
        "manualRouteReviewTargets",
        "manual_route_review_targets",
        "manualRouteTargets",
        "manual_route_targets",
        "manualWalkTargets",
        "manual_walk_targets",
        "manualRouteIntents",
        "manual_route_intents",
    )
    if isinstance(direct, (list, tuple)):
        return list(direct)
    route_evidence = _first_present(values, "routeEvidence", "route_evidence")
    if isinstance(route_evidence, Mapping):
        nested = _first_present(
            route_evidence,
            "manualWalkTargets",
            "manual_walk_targets",
            "walkTargets",
            "walk_targets",
            "intendedTargets",
            "intended_targets",
        )
        if isinstance(nested, (list, tuple)):
            return list(nested)
    return []


def _point_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return "-"
    x = _first_present(value, "x", "worldX", "world_x")
    y = _first_present(value, "y", "worldY", "world_y")
    plane = _first_present(value, "plane", "z")
    if x is None or y is None:
        return "-"
    suffix = f",p{plane}" if plane is not None else ""
    return f"({x},{y}{suffix})"


def _manual_walk_target_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return _bounded_text(_compact_json(value), 300)
    target = _first_present(
        value,
        "manualIntentTarget",
        "manual_intent_target",
        "chosenTargetWorld",
        "chosen_target_world",
        "intendedWorld",
        "intended_world",
        "selectedWorldTile",
        "selected_world_tile",
        "selectedSceneTile",
        "selected_scene_tile",
        "worldTile",
        "world_tile",
        "target",
        "world",
    )
    if isinstance(target, Mapping):
        nested_world = _first_present(target, "world", "worldTile", "world_tile")
        if isinstance(nested_world, Mapping):
            target = nested_world
    sequence = _first_present(
        value, "clickEventSequence", "click_event_sequence", "eventSequence", "sequence"
    )
    classification = _first_present(
        value,
        "intentClassification",
        "intent_classification",
        "classification",
        "status",
    )
    confidence = _first_present(value, "confidence", "resolutionConfidence")
    distance = _first_present(
        value,
        "requestedTileDistance",
        "requested_tile_distance",
        "distanceTiles",
        "distance_tiles",
    )
    correction = _first_present(
        value, "correctionClassification", "correction_classification", "possibleCorrection"
    )
    quick_followup = _first_present(value, "quickFollowup", "quick_followup")
    if isinstance(quick_followup, Mapping):
        correction = _first_present(
            quick_followup, "classification", "relation", "status"
        ) or correction
    supersedes = _first_present(
        value,
        "possiblySupersedesClickEventSequence",
        "possibly_supersedes_click_event_sequence",
    )
    fields = [
        f"click={sequence if sequence is not None else '-'}",
        f"target={_point_text(target)}",
    ]
    if distance is not None:
        fields.append(f"distance={distance}t")
    if classification is not None:
        fields.append(f"intent={classification}")
    if confidence is not None:
        fields.append(f"confidence={confidence}")
    if correction is not None:
        fields.append(f"correction={correction}")
    if supersedes is not None:
        fields.append(f"possibly-supersedes={supersedes}")
    return _bounded_text("  ".join(fields), 300)


_CAMERA_INPUT_METHODS = frozenset({"keyboard", "middle_drag", "mixed"})


def _camera_episode_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return _bounded_text(_compact_json(value), 360)
    association_value = _first_present(
        value,
        "intentAssociation",
        "intent_association",
        "association",
    )
    association = association_value if isinstance(association_value, Mapping) else {}
    method = _first_present(
        value,
        "observedInputMethod",
        "observed_input_method",
        "inputMethod",
        "input_method",
        "method",
    )
    legacy_classification = _first_present(value, "classification")
    if method is None and legacy_classification in _CAMERA_INPUT_METHODS:
        method = legacy_classification
    intent_classification = _first_present(
        value,
        "intentClassification",
        "intent_classification",
        "associationClassification",
        "association_classification",
    )
    if intent_classification is None:
        intent_classification = _first_present(
            association, "classification", "intentClassification", "status"
        )
    click_sequence = _first_present(
        value, "clickEventSequence", "click_event_sequence"
    )
    if intent_classification is None:
        if legacy_classification in _CAMERA_INPUT_METHODS:
            intent_classification = (
                "action_linked" if click_sequence is not None else "legacy_unspecified"
            )
        else:
            intent_classification = legacy_classification or "unspecified"
    confidence = _first_present(value, "associationConfidence", "confidence")
    if confidence is None:
        confidence = _first_present(association, "confidence")
    target = _first_present(value, "target")
    if not isinstance(target, Mapping):
        target = _first_present(association, "target")
    family = _first_present(target, "actionFamily", "action_family") if isinstance(target, Mapping) else None
    pose_delta = _first_present(value, "cameraPoseDelta", "camera_pose_delta", "poseDelta", "pose_delta")
    if not isinstance(pose_delta, Mapping):
        pose_delta = {}
    delta = "/".join(
        f"{label}:{_first_present(pose_delta, *keys)}"
        for label, keys in (
            ("yaw", ("yaw", "yawDelta", "yaw_delta")),
            ("pitch", ("pitch", "pitchDelta", "pitch_delta")),
            ("zoom", ("zoom3d", "zoom", "zoomDelta", "zoom_delta")),
        )
        if _first_present(pose_delta, *keys) is not None
    )
    hold = _first_present(
        value,
        "maxControlHoldMillis",
        "max_control_hold_millis",
        "inputDurationMillis",
        "input_duration_millis",
        "cameraInputDurationMillis",
    )
    drag = _first_present(
        value,
        "maxDragPathPixels",
        "max_drag_path_pixels",
        "dragDistancePixels",
        "drag_distance_pixels",
    )
    delay = _first_present(
        value, "lastCameraInputToClickMillis", "last_camera_input_to_click_millis"
    )
    effective = _first_present(
        value, "effectiveCameraChangeObserved", "effective_camera_change_observed"
    )
    reason = _first_present(value, "inference", "associationReason", "reason")
    if reason is None:
        reasons = _first_present(value, "ambiguityReasons", "ambiguity_reasons")
        if isinstance(reasons, (list, tuple)) and reasons:
            reason = reasons[0]
    fields = [
        f"method={method or '-'}",
        f"association={intent_classification}",
        f"confidence={confidence or '-'}",
        f"click={click_sequence if click_sequence is not None else '-'}",
    ]
    if family is not None:
        fields.append(f"target={family}")
    if delta:
        fields.append(f"delta={delta}")
    if hold is not None:
        fields.append(f"hold={hold}ms")
    if drag is not None:
        fields.append(f"drag={drag}px")
    if delay is not None:
        fields.append(f"to-click={delay}ms")
    if effective is not None:
        fields.append(f"effective={'yes' if effective else 'no'}")
    if reason:
        fields.append(f"reason={_bounded_text(reason, 90)}")
    return _bounded_text("  ".join(fields), 360)


def _route_comparison_text(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return [_bounded_text(_compact_json(value), 360)]
    direction = _first_present(value, "direction", "routeDirection", "route_direction")
    status = _first_present(value, "status", "classification", "comparisonStatus")
    reason = _first_present(value, "reason", "comparisonReason", "comparison_reason")
    definition_id = _first_present(value, "definitionId", "definition_id")
    definition_version = _first_present(value, "definitionVersion", "definition_version")
    clicked = _first_present(
        value, "clickedTargetCount", "clicked_target_count", "manualTargetCount"
    )
    observed = _first_present(
        value,
        "observedPointCount",
        "observed_point_count",
        "playerSampleCount",
        "observedPlayerPointCount",
    )
    selected_metrics = _first_present(
        value, "selectedRouteMetrics", "selected_route_metrics"
    )
    metrics = selected_metrics if isinstance(selected_metrics, Mapping) else value
    average = _first_present(
        metrics,
        "averageCorridorDeviationTiles",
        "average_corridor_deviation_tiles",
        "averageLateralDeviationTiles",
        "average_lateral_deviation_tiles",
        "meanLateralDeviationTiles",
    )
    maximum = _first_present(
        metrics,
        "maximumCorridorDeviationTiles",
        "maximum_corridor_deviation_tiles",
        "maxLateralDeviationTiles",
        "max_lateral_deviation_tiles",
    )
    progress = _first_present(
        metrics, "forwardProgressTiles", "forward_progress_tiles", "progressSummary"
    )
    order = _first_present(value, "orderStatus", "order_status", "orderSummary")
    forward_steps = _first_present(
        metrics, "forwardStepCount", "forward_step_count"
    )
    if order is None and forward_steps is not None:
        order = f"{forward_steps} forward steps"
    backtracking = _first_present(
        metrics,
        "backtrackingEventCount",
        "backtracking_event_count",
        "backtrackingCount",
        "backtracking_count",
        "backtrackingSummary",
    )
    backtracking_tiles = _first_present(
        metrics, "backtrackingTiles", "backtracking_tiles"
    )
    plane_counts = _first_present(
        value, "planeCounts", "plane_counts", "countsByPlane", "counts_by_plane"
    )
    summary = [
        f"definition={definition_id or '-'}@{definition_version if definition_version is not None else '-'}",
        f"direction={direction or '-'}",
        f"status={status or '-'}",
        f"clicked={clicked if clicked is not None else '-'}",
        f"observed={observed if observed is not None else '-'}",
        f"avg-deviation={average if average is not None else '-'}t",
        f"max-deviation={maximum if maximum is not None else '-'}t",
        f"progress={progress if progress is not None else '-'}",
        f"order={order if order is not None else '-'}",
        f"backtracking={backtracking if backtracking is not None else '-'}",
    ]
    lines = [_bounded_text("  ".join(summary), 360)]
    if reason:
        lines.append(f"reason: {_bounded_text(reason, 330)}")
    if backtracking_tiles is not None:
        lines.append(f"backtracking distance: {backtracking_tiles}t")
    if plane_counts is not None:
        lines.append(f"plane-aware counts: {_bounded_text(_compact_json(plane_counts), 320)}")
    distance_summary = _first_present(
        value, "targetDistanceSummary", "target_distance_summary"
    )
    if isinstance(distance_summary, Mapping):
        basis = _first_present(distance_summary, "basis")
        qualification = _first_present(distance_summary, "qualification")
        distances = _first_present(
            distance_summary, "distancesTiles", "distances_tiles"
        )
        histogram = _first_present(distance_summary, "histogram")
        lines.append(
            _bounded_text(
                f"target distances: basis={basis or '-'}  values={_compact_json(distances)}",
                360,
            )
        )
        if qualification:
            lines.append(
                f"distance note: {_bounded_text(qualification, 330)}"
            )
        lines.append(f"distance histogram: {_bounded_text(_compact_json(histogram), 320)}")
    plane_views = _first_present(value, "planeViews", "plane_views")
    if isinstance(plane_views, (list, tuple)):
        for view in plane_views[:4]:
            if not isinstance(view, Mapping):
                continue
            plane = _first_present(view, "plane")
            manual = _inspection_sequence(
                _first_present(view, "manualTargets", "manual_targets")
            )
            player = _inspection_sequence(
                _first_present(view, "observedPlayerPath", "observed_player_path")
            )
            mandatory = _inspection_sequence(
                _first_present(
                    view,
                    "mandatoryDefinitionPoints",
                    "mandatory_definition_points",
                )
            )
            lines.append(
                _bounded_text(
                    f"plane {plane}: "
                    f"manual[{len(manual)}]={_coordinate_list_text(manual)}  "
                    f"observed[{len(player)}]={_coordinate_list_text(player)}  "
                    f"mandatory[{len(mandatory)}]={_coordinate_list_text(mandatory)}",
                    360,
                )
            )
            definitions = _inspection_sequence(
                _first_present(view, "definitionRoutes", "definition_routes")
            )
            for definition in definitions[:2]:
                if not isinstance(definition, Mapping):
                    continue
                route_id = _first_present(definition, "routeId", "route_id")
                points = _inspection_sequence(
                    _first_present(definition, "points", "routePoints", "route_points")
                )
                lines.append(
                    _bounded_text(
                        f"plane {plane} definition {route_id or '-'}[{len(points)}]="
                        f"{_coordinate_list_text(points)}",
                        360,
                    )
                )
    plot = _first_present(
        value,
        "textPlot",
        "text_plot",
        "pointList",
        "point_list",
        "compactPlot",
        "compact_plot",
    )
    if isinstance(plot, str) and plot.strip():
        lines.extend(_bounded_text(line, 360) for line in plot.splitlines()[:12])
    elif isinstance(plot, (list, tuple)):
        lines.extend(_bounded_text(_compact_json(point), 360) for point in plot[:12])
    return lines


def _coordinate_list_text(values: list[object], *, maximum_points: int = 6) -> str:
    points = [_point_text(value) for value in values[:maximum_points]]
    suffix = f" +{len(values) - maximum_points}" if len(values) > maximum_points else ""
    return " -> ".join(points) + suffix if points else "-"


def _bounded_text(value: object, maximum: int) -> str:
    text = str(value)
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _demonstration_terminal_status(
    reference: object, lifecycle: LifecycleState
) -> str:
    if reference is None:
        return lifecycle.value.replace("_", " ").title()
    if getattr(reference, "valid", False) is not True:
        return "Finished — trusted inspection failed"
    reason = getattr(reference, "stop_reason", None)
    requested = getattr(reference, "requested_duration_seconds", None)
    if reason == "duration_elapsed":
        if isinstance(requested, (int, float)) and not isinstance(requested, bool):
            return f"Complete — requested {float(requested):g}s duration elapsed"
        return "Complete — requested duration elapsed"
    if reason in {"facade_stop_requested", "operator_interrupt"}:
        return "Stopped by operator request — artifact finalized and inspected"
    if isinstance(reason, str) and reason:
        return f"Stopped early — {reason.replace('_', ' ')}"
    return lifecycle.value.replace("_", " ").title()


def _inspection_text(values: Mapping[str, object]) -> str:
    route_points = _inspection_sequence(
        values.get("routePoints") or values.get("route_points") or []
    )
    manual_targets = _manual_walk_targets(values)
    route_comparison = _first_present(values, "routeComparison", "route_comparison")
    sections: list[tuple[str, object, object, str | None]] = [
        (
            "Validity",
            [f"valid: {values.get('valid')}", f"status: {values.get('status')}"],
            _compact_json,
            None,
        ),
        (
            "Semantic timeline",
            values.get("semanticSummary") or values.get("semantic_summary") or [],
            _compact_json,
            None,
        ),
        (
            "Interacted entities",
            values.get("interactedEntities") or values.get("interacted_entities") or [],
            _compact_json,
            None,
        ),
        (
            "Observed player path — manual demonstration (review only)",
            route_points,
            _compact_json,
            "Sampled player positions only; these are not clicked tiles or the task definition route.",
        ),
    ]
    if manual_targets:
        sections.append(
            (
                "Manual Walk targets — inferred intent (review only)",
                manual_targets,
                _manual_walk_target_text,
                "Recorded or inferred click destinations; possible corrections remain visible and are not automatically adopted as route points.",
            )
        )
    if route_comparison is not None:
        sections.append(
            (
                "Manual vs definition route (review only)",
                _route_comparison_text(route_comparison),
                str,
                "Application-owned comparison of this demonstration against the currently selected definition; it is not persisted route authority.",
            )
        )
    sections.extend(
        [
            (
                "Camera intent episodes",
                values.get("cameraReviewEpisodes")
                or values.get("camera_review_episodes")
                or values.get("cameraIntentEpisodes")
                or values.get("camera_intent_episodes")
                or [],
                _camera_episode_text,
                "Ephemeral review semantics when available: input method and action association are separate; unlinked movement may be exploratory camera use.",
            ),
            (
                "Reference timing profiles",
                values.get("timingReviewProfiles")
                or values.get("timing_review_profiles")
                or values.get("timingProfiles")
                or values.get("timing_profiles")
                or [],
                _compact_json,
                "Ephemeral corrected review timing is preferred when available; finalized artifact timing remains unchanged.",
            ),
            (
                "Gaps",
                values.get("coverageGaps") or values.get("coverage_gaps") or [],
                _compact_json,
                None,
            ),
            ("Ambiguities", values.get("ambiguities") or [], _compact_json, None),
            (
                "Review-only suggestions",
                values.get("candidateSuggestions")
                or values.get("candidate_suggestions")
                or [],
                _compact_json,
                None,
            ),
            ("Errors", values.get("errors") or [], _compact_json, None),
        ]
    )
    lines: list[str] = []
    for title, content, formatter, note in sections:
        lines.append(title)
        lines.append("-" * len(title))
        if note:
            lines.append(note)
        if isinstance(content, (list, tuple)):
            lines.extend(f"• {formatter(item)}" for item in content)
        else:
            lines.append(formatter(content))
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
