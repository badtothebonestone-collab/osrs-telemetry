import argparse
import json
import mimetypes
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


HOST = "127.0.0.1"
DEFAULT_PORT = 8810
DEFAULT_SCENARIO = "bank_area"
MISSING_SCENARIO_MESSAGE = (
    "Run python telemetry-viewer\\build_scenario_dataset.py --scenario bank_area first."
)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    records = []
    warnings = []

    if not path.exists():
        return records, warnings

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()

                if not text:
                    continue

                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"{path.name}:{line_number}: invalid JSON: {error.msg}")
                    continue

                if isinstance(record, dict):
                    records.append(record)
                else:
                    warnings.append(f"{path.name}:{line_number}: expected JSON object")
    except OSError as error:
        warnings.append(f"could not read {path}: {error}")

    return records, warnings


def compact_counts(counter: Counter, limit: int | None = None) -> dict:
    return {str(key): count for key, count in counter.most_common(limit)}


def frame_for(record: dict) -> dict:
    value = record.get("frame")
    return value if isinstance(value, dict) else {}


def candidate_target(candidate: dict) -> dict:
    value = candidate.get("target")
    return value if isinstance(value, dict) else {}


def candidate_name(candidate: dict) -> str:
    target = candidate_target(candidate)

    for key in ("name", "targetName", "objectName", "itemName", "npcName", "fallbackName", "targetId"):
        value = target.get(key)

        if value is not None and str(value).strip():
            return str(value)

    target_type = target.get("targetType") or "target"
    target_id = target.get("rawId")

    if target_id is None:
        target_id = target.get("id")

    return f"{target_type}[{target_id if target_id is not None else 'unknown'}]"


def context_name(target: dict) -> str:
    for key in ("name", "targetName", "fallbackName", "targetId"):
        value = target.get(key)

        if value is not None and str(value).strip():
            return str(value)

    target_type = target.get("targetType") or "target"
    target_id = target.get("rawId")

    if target_id is None:
        target_id = target.get("id")

    return f"{target_type}[{target_id if target_id is not None else 'unknown'}]"


def selected_candidates(record: dict) -> list[dict]:
    value = record.get("selectedCandidates")
    return value if isinstance(value, list) else []


def context_targets(record: dict) -> list[dict]:
    context = record.get("context")

    if not isinstance(context, dict):
        return []

    value = context.get("targets")
    return value if isinstance(value, list) else []


def tick_id_for(record: dict) -> int | None:
    value = record.get("tickId")
    return value if isinstance(value, int) else None


class ScenarioDataset:
    def __init__(self, session: Path | None, scenario: str):
        self.session = session
        self.scenario = scenario
        self.scenario_dir = session / "scenario_datasets" if session else None
        self.scenario_path = self.scenario_dir / f"{scenario}.jsonl" if self.scenario_dir else None
        self.index_path = self.scenario_dir / "scenario_index.json" if self.scenario_dir else None
        self.records: list[dict] = []
        self.index: dict = {}
        self.messages: list[str] = []
        self.warnings: list[str] = []
        self.records_by_tick: dict[int, dict] = {}
        self.tick_dimensions: dict[int, dict] = {}

    def load(self) -> None:
        if self.session is None:
            self.messages.append("No telemetry session found.")
            return

        if self.scenario_path is None or not self.scenario_path.exists():
            self.messages.append(MISSING_SCENARIO_MESSAGE.replace("bank_area", self.scenario))
            return

        self.records, warnings = read_jsonl(self.scenario_path)
        self.warnings.extend(warnings)
        index = safe_read_json(self.index_path) if self.index_path else None
        self.index = index if isinstance(index, dict) else {}

        for record in self.records:
            tick_id = tick_id_for(record)

            if tick_id is not None:
                self.records_by_tick[tick_id] = record

        self._load_dimension_hints()

    def _load_dimension_hints(self) -> None:
        if self.session is None:
            return

        geometry_dir = self.session / "interaction_geometry"

        for path in (geometry_dir / "world_targets.jsonl", geometry_dir / "ui_targets.jsonl"):
            records, warnings = read_jsonl(path)

            if path.exists():
                self.warnings.extend(warnings[:5])

            for record in records:
                tick_id = tick_id_for(record)

                if tick_id is None or tick_id in self.tick_dimensions:
                    continue

                frame = frame_for(record)
                canvas = record.get("canvas") if isinstance(record.get("canvas"), dict) else {}
                self.tick_dimensions[tick_id] = {
                    "frameWidth": frame.get("width"),
                    "frameHeight": frame.get("height"),
                    "canvasWidth": canvas.get("width"),
                    "canvasHeight": canvas.get("height"),
                    "sourcePath": str(path),
                }

    def dimensions_for_tick(self, tick_id: int) -> dict:
        record = self.records_by_tick.get(tick_id)
        frame = frame_for(record) if record else {}
        hints = self.tick_dimensions.get(tick_id, {})
        return {
            "frameWidth": hints.get("frameWidth") or frame.get("width"),
            "frameHeight": hints.get("frameHeight") or frame.get("height"),
            "canvasWidth": hints.get("canvasWidth"),
            "canvasHeight": hints.get("canvasHeight"),
            "sourcePath": hints.get("sourcePath"),
        }

    def frame_path_for_record(self, record: dict) -> Path | None:
        if self.session is None:
            return None

        frame_path = frame_for(record).get("path")

        if not frame_path:
            return None

        candidate = Path(str(frame_path))

        if not candidate.is_absolute():
            candidate = self.session / candidate

        try:
            resolved = candidate.resolve()
            session_root = self.session.resolve()
            resolved.relative_to(session_root)
        except (OSError, ValueError):
            return None

        return resolved

    def frame_path_for_tick(self, tick_id: int) -> Path | None:
        record = self.records_by_tick.get(tick_id)
        return self.frame_path_for_record(record) if record else None

    def summary(self) -> dict:
        candidate_name_counts = Counter()
        preferred_geometry_counts = Counter()
        warning_count = 0
        selected_candidate_count = 0
        context_target_count = 0
        ticks = []

        for record in self.records:
            tick_id = tick_id_for(record)

            if tick_id is not None:
                ticks.append(tick_id)

            warnings = record.get("warnings")
            warning_count += len(warnings) if isinstance(warnings, list) else 0
            candidates = selected_candidates(record)
            selected_candidate_count += len(candidates)
            context_target_count += len(context_targets(record))

            for candidate in candidates:
                candidate_name_counts[candidate_name(candidate)] += 1
                preferred_geometry_counts[candidate.get("preferredAimGeometryType") or "none"] += 1

        return {
            "sessionPath": str(self.session) if self.session else None,
            "scenarioType": self.index.get("scenarioType") or self.scenario,
            "scenarioRecordCount": (
                self.index.get("scenarioRecordCount")
                if isinstance(self.index.get("scenarioRecordCount"), int)
                else len(self.records)
            ),
            "selectedCandidateCount": (
                self.index.get("selectedCandidateCount")
                if isinstance(self.index.get("selectedCandidateCount"), int)
                else selected_candidate_count
            ),
            "contextTargetCount": (
                self.index.get("contextTargetCount")
                if isinstance(self.index.get("contextTargetCount"), int)
                else context_target_count
            ),
            "tickRange": [min(ticks), max(ticks)] if ticks else None,
            "candidateNameCounts": compact_counts(candidate_name_counts, 25),
            "preferredGeometryCounts": compact_counts(preferred_geometry_counts),
            "warnings": list(self.messages) + list(self.warnings) + self.index.get("warnings", []) + ([f"{warning_count} record warnings"] if warning_count else []),
            "paths": {
                "scenarioDataset": str(self.scenario_path) if self.scenario_path else None,
                "scenarioIndex": str(self.index_path) if self.index_path else None,
            },
        }

    def compact_records(self) -> list[dict]:
        rows = []

        for index, record in enumerate(self.records):
            candidates = selected_candidates(record)
            frame = frame_for(record)
            rows.append(
                {
                    "index": index,
                    "tickId": record.get("tickId"),
                    "frameExists": frame.get("exists"),
                    "selectedCandidateCount": len(candidates),
                    "contextTargetCount": len(context_targets(record)),
                    "topCandidates": [
                        {
                            "name": candidate_name(candidate),
                            "score": candidate.get("score"),
                            "rankWithinScenario": candidate.get("rankWithinScenario"),
                        }
                        for candidate in candidates[:5]
                    ],
                }
            )

        return rows


def html_page() -> str:
    return r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scenario Inspector</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: #111417;
      color: #e8edf2;
    }
    body {
      margin: 0;
      min-height: 100vh;
      overflow: hidden;
      background: #111417;
    }
    button, input {
      color: inherit;
      background: #1e252c;
      border: 1px solid #33414d;
      border-radius: 4px;
      padding: 6px 8px;
    }
    button {
      cursor: pointer;
    }
    button:hover, tr:hover {
      background: #27313a;
    }
    .app {
      display: grid;
      grid-template-columns: minmax(520px, 1fr) 420px;
      height: 100vh;
    }
    .main, .side {
      min-height: 0;
      display: flex;
      flex-direction: column;
    }
    .main {
      border-right: 1px solid #2b333b;
    }
    .toolbar, .panel {
      padding: 10px;
      border-bottom: 1px solid #2b333b;
      background: #171c21;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .viewer {
      flex: 1;
      min-height: 0;
      display: grid;
      place-items: center;
      background: #0b0d0f;
      position: relative;
      overflow: auto;
    }
    .frame-wrap {
      position: relative;
      max-width: 100%;
      max-height: 100%;
    }
    #frameImage {
      display: block;
      max-width: 100%;
      max-height: calc(100vh - 260px);
      object-fit: contain;
    }
    #overlay {
      position: absolute;
      left: 0;
      top: 0;
      pointer-events: none;
    }
    .placeholder {
      width: min(900px, 90vw);
      min-height: 420px;
      display: grid;
      place-items: center;
      color: #aab7c4;
      border: 1px dashed #43505c;
      background: #10151a;
      text-align: center;
      padding: 20px;
    }
    .tables {
      height: 230px;
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      border-top: 1px solid #2b333b;
      min-height: 0;
    }
    .table-box {
      min-width: 0;
      min-height: 0;
      overflow: auto;
      border-right: 1px solid #2b333b;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 12px;
    }
    th, td {
      padding: 5px 7px;
      border-bottom: 1px solid #252d34;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      vertical-align: middle;
    }
    th {
      position: sticky;
      top: 0;
      background: #182027;
      z-index: 1;
      text-align: left;
    }
    tr.selected {
      background: #314357;
    }
    .side {
      overflow: hidden;
    }
    .side-scroll {
      overflow: auto;
      min-height: 0;
      flex: 1;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      font-size: 12px;
    }
    .card {
      background: #20272e;
      border: 1px solid #303a44;
      border-radius: 6px;
      padding: 8px;
    }
    .card b {
      display: block;
      font-size: 18px;
      margin-top: 2px;
    }
    .muted {
      color: #9eabb7;
    }
    .warning {
      margin: 8px 0;
      padding: 8px;
      color: #ffd49a;
      background: #342514;
      border: 1px solid #694a1c;
      border-radius: 6px;
    }
    .debug {
      display: none;
      margin: 0;
      padding: 8px 10px;
      color: #c3d4e4;
      background: #101820;
      border-bottom: 1px solid #2b333b;
      font-family: Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #0e1216;
      border: 1px solid #2c343d;
      border-radius: 6px;
      padding: 8px;
      font-size: 12px;
    }
    label {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
    }
    input[type="number"] {
      width: 74px;
    }
    input[type="range"] {
      width: 110px;
    }
  </style>
</head>
<body>
  <div class="app">
    <main class="main">
      <div class="toolbar">
        <button id="prev">Previous</button>
        <button id="next">Next</button>
        <label>Jump <input id="jumpTick" type="number" min="0"></label>
        <button id="jump">Go</button>
        <label><input id="scaleCanvas" type="checkbox" checked> Scale canvas geometry to frame</label>
        <label><input id="showDebug" type="checkbox"> Show coordinate debug</label>
        <label><input id="showCandidates" type="checkbox" checked> selected candidates</label>
        <label><input id="showContext" type="checkbox" checked> context</label>
        <label><input id="showObstacleContext" type="checkbox" checked> obstacle/navigation</label>
        <label><input id="showLabels" type="checkbox" checked> labels</label>
        <label><input id="showRankScore" type="checkbox" checked> rank/score</label>
        <label>opacity <input id="opacity" type="range" min="0.1" max="1" step="0.05" value="0.75"></label>
        <label>max context <input id="maxContext" type="number" min="0" value="50"></label>
      </div>
      <div id="messages"></div>
      <pre class="debug" id="coordinateDebug"></pre>
      <div class="viewer">
        <div class="frame-wrap" id="frameWrap">
          <img id="frameImage" alt="">
          <canvas id="overlay"></canvas>
        </div>
        <div class="placeholder" id="placeholder" hidden>
          Frame file missing or expired by retention. Scenario record details are still available.
        </div>
      </div>
      <div class="tables">
        <div class="table-box">
          <table>
            <thead>
              <tr><th style="width:50px">Rank</th><th style="width:64px">Score</th><th>Name</th><th style="width:86px">Type</th><th>Role / Category</th><th style="width:120px">Aim</th><th>Reasons</th></tr>
            </thead>
            <tbody id="candidateRows"></tbody>
          </table>
        </div>
        <div class="table-box">
          <table>
            <thead>
              <tr><th>Name</th><th style="width:92px">Type</th><th>Role / Category</th><th style="width:90px">Geometry</th></tr>
            </thead>
            <tbody id="contextRows"></tbody>
          </table>
        </div>
      </div>
    </main>
    <aside class="side">
      <div class="panel">
        <h3 style="margin:0 0 8px">Scenario</h3>
        <div class="summary-grid" id="summary"></div>
      </div>
      <div class="panel">
        <h3 style="margin:0 0 8px">Ticks</h3>
        <select id="recordSelect" style="width:100%; padding:6px; background:#1e252c; color:#e8edf2; border:1px solid #33414d"></select>
      </div>
      <div class="side-scroll">
        <div class="panel">
          <h3 style="margin:0 0 8px">Selected Tick</h3>
          <pre id="recordDetails">{}</pre>
        </div>
        <div class="panel">
          <h3 style="margin:0 0 8px">Selected Candidate</h3>
          <pre id="candidateDetails">{}</pre>
        </div>
      </div>
    </aside>
  </div>
  <script>
    const state = {
      summary: null,
      records: [],
      currentIndex: 0,
      currentRecord: null,
      currentDimensions: {},
      selectedCandidateIndex: 0,
      frameLoaded: false,
      frameNatural: null,
    };

    const el = {
      summary: document.getElementById("summary"),
      messages: document.getElementById("messages"),
      recordSelect: document.getElementById("recordSelect"),
      frameImage: document.getElementById("frameImage"),
      overlay: document.getElementById("overlay"),
      frameWrap: document.getElementById("frameWrap"),
      placeholder: document.getElementById("placeholder"),
      candidateRows: document.getElementById("candidateRows"),
      contextRows: document.getElementById("contextRows"),
      recordDetails: document.getElementById("recordDetails"),
      candidateDetails: document.getElementById("candidateDetails"),
      prev: document.getElementById("prev"),
      next: document.getElementById("next"),
      jumpTick: document.getElementById("jumpTick"),
      jump: document.getElementById("jump"),
      scaleCanvas: document.getElementById("scaleCanvas"),
      showDebug: document.getElementById("showDebug"),
      coordinateDebug: document.getElementById("coordinateDebug"),
      showCandidates: document.getElementById("showCandidates"),
      showContext: document.getElementById("showContext"),
      showObstacleContext: document.getElementById("showObstacleContext"),
      showLabels: document.getElementById("showLabels"),
      showRankScore: document.getElementById("showRankScore"),
      opacity: document.getElementById("opacity"),
      maxContext: document.getElementById("maxContext"),
    };

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }

    async function api(path) {
      const response = await fetch(path);
      if (!response.ok) throw new Error(await response.text());
      return await response.json();
    }

    function card(label, value) {
      return `<div class="card"><span class="muted">${escapeHtml(label)}</span><b>${escapeHtml(value ?? "none")}</b></div>`;
    }

    function renderSummary() {
      const summary = state.summary || {};
      el.summary.innerHTML = [
        card("records", summary.scenarioRecordCount ?? 0),
        card("candidates", summary.selectedCandidateCount ?? 0),
        card("context", summary.contextTargetCount ?? 0),
        card("tick range", summary.tickRange ? summary.tickRange.join("-") : "none"),
      ].join("");

      const warnings = summary.warnings || [];
      el.messages.innerHTML = warnings.length
        ? warnings.map(w => `<div class="warning">${escapeHtml(w)}</div>`).join("")
        : "";
    }

    function recordLabel(row) {
      const names = (row.topCandidates || []).map(item => `${item.name} ${item.score}`).join(", ");
      return `tick ${row.tickId} (${row.selectedCandidateCount} candidates) ${names}`;
    }

    function renderRecordSelect() {
      el.recordSelect.innerHTML = state.records.map((row, index) => {
        return `<option value="${index}">${escapeHtml(recordLabel(row))}</option>`;
      }).join("");
      el.recordSelect.value = String(state.currentIndex);
    }

    function pointText(point) {
      if (!point || typeof point.x !== "number" || typeof point.y !== "number") return "-";
      return `${Math.round(point.x)},${Math.round(point.y)}`;
    }

    function targetName(target) {
      if (!target) return "-";
      return target.name || target.targetName || target.fallbackName || target.targetId || `${target.targetType || "target"}[${target.rawId ?? target.id ?? "unknown"}]`;
    }

    function geometrySummary(target) {
      for (const key of ["clickboxBounds", "convexHullBounds"]) {
        const b = target[key];
        if (b && typeof b.x === "number") return key;
      }
      for (const key of ["tilePolygon", "clickboxPolygon", "convexHullPolygon"]) {
        if (Array.isArray(target[key])) return key;
      }
      for (const key of ["canvasPoint", "canvasLocation", "canvasCenter"]) {
        if (target[key]) return key;
      }
      return "-";
    }

    function renderTables() {
      const record = state.currentRecord || {};
      const candidates = record.selectedCandidates || [];
      const context = record.context?.targets || [];
      el.candidateRows.innerHTML = candidates.map((candidate, index) => {
        const target = candidate.target || {};
        const reasons = (candidate.reasons || []).slice(0, 5).join(",");
        return `<tr class="${index === state.selectedCandidateIndex ? "selected" : ""}" data-index="${index}">
          <td>${escapeHtml(candidate.rankWithinScenario ?? "-")}</td>
          <td>${escapeHtml(candidate.score ?? "-")}</td>
          <td title="${escapeHtml(targetName(target))}">${escapeHtml(targetName(target))}</td>
          <td>${escapeHtml(target.targetType || "-")}</td>
          <td>${escapeHtml(target.targetRole || "-")} / ${escapeHtml(target.targetCategory || "-")}</td>
          <td>${escapeHtml(pointText(candidate.aimPoint))}</td>
          <td title="${escapeHtml(reasons)}">${escapeHtml(reasons || "-")}</td>
        </tr>`;
      }).join("");
      el.contextRows.innerHTML = context.map(target => {
        return `<tr>
          <td title="${escapeHtml(targetName(target))}">${escapeHtml(targetName(target))}</td>
          <td>${escapeHtml(target.targetType || "-")}</td>
          <td>${escapeHtml(target.targetRole || "-")} / ${escapeHtml(target.targetCategory || "-")}</td>
          <td>${escapeHtml(geometrySummary(target))}</td>
        </tr>`;
      }).join("");

      for (const row of el.candidateRows.querySelectorAll("tr")) {
        row.addEventListener("click", () => {
          state.selectedCandidateIndex = Number(row.dataset.index);
          renderAll();
        });
      }
    }

    function normalizePoint(point) {
      if (Array.isArray(point) && point.length >= 2) return {x: Number(point[0]), y: Number(point[1])};
      if (point && typeof point.x === "number" && typeof point.y === "number") return {x: point.x, y: point.y};
      return null;
    }

    function tickDims() {
      const record = state.currentRecord || {};
      const frame = record.frame || {};
      const hints = state.currentDimensions || {};
      const naturalWidth = state.frameNatural?.width || el.frameImage.naturalWidth || null;
      const naturalHeight = state.frameNatural?.height || el.frameImage.naturalHeight || null;
      const frameWidth = naturalWidth || Number(hints.frameWidth) || Number(frame.width) || null;
      const frameHeight = naturalHeight || Number(hints.frameHeight) || Number(frame.height) || null;
      const frameMetadataWidth = Number(frame.width) || Number(hints.frameWidth) || null;
      const frameMetadataHeight = Number(frame.height) || Number(hints.frameHeight) || null;
      const canvasWidth = Number(hints.canvasWidth) || Number(record.canvas?.width) || null;
      const canvasHeight = Number(hints.canvasHeight) || Number(record.canvas?.height) || null;
      return {
        frameWidth,
        frameHeight,
        frameMetadataWidth,
        frameMetadataHeight,
        canvasWidth,
        canvasHeight,
        renderedWidth: el.overlay.width || null,
        renderedHeight: el.overlay.height || null,
      };
    }

    function sourceSpaceFor(item) {
      const explicit = item?.coordinateSpace || item?.geometry?.coordinateSpace;
      if (explicit === "canvasPixels" || explicit === "framePixels") return explicit;
      if (item?.pixelBox) return "framePixels";
      return "canvasPixels";
    }

    function toFramePoint(point, item) {
      const normalized = normalizePoint(point);
      if (!normalized) return null;
      const sourceSpace = sourceSpaceFor(item);
      const dims = tickDims();
      let x = normalized.x;
      let y = normalized.y;

      if (
        sourceSpace === "canvasPixels" &&
        el.scaleCanvas.checked &&
        dims.canvasWidth &&
        dims.canvasHeight &&
        dims.frameWidth &&
        dims.frameHeight
      ) {
        x = x * dims.frameWidth / dims.canvasWidth;
        y = y * dims.frameHeight / dims.canvasHeight;
      }

      return Number.isFinite(x) && Number.isFinite(y) ? {x, y} : null;
    }

    function scalePoint(point, item) {
      const framePoint = toFramePoint(point, item);
      if (!framePoint) return null;
      const dims = tickDims();
      const frameWidth = dims.frameWidth || dims.frameMetadataWidth || el.overlay.width;
      const frameHeight = dims.frameHeight || dims.frameMetadataHeight || el.overlay.height;
      const sx = frameWidth ? el.overlay.width / frameWidth : 1;
      const sy = frameHeight ? el.overlay.height / frameHeight : 1;
      return {x: framePoint.x * sx, y: framePoint.y * sy};
    }

    function drawPolygon(ctx, points, item, stroke, fill, width) {
      if (!Array.isArray(points) || points.length < 2) return;
      const scaled = points.map(normalizePoint).map(point => scalePoint(point, item)).filter(Boolean);
      if (scaled.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(scaled[0].x, scaled[0].y);
      for (const point of scaled.slice(1)) ctx.lineTo(point.x, point.y);
      ctx.closePath();
      ctx.strokeStyle = stroke;
      ctx.lineWidth = width;
      ctx.stroke();
      if (fill) {
        ctx.fillStyle = fill;
        ctx.fill();
      }
    }

    function drawBounds(ctx, bounds, item, stroke, fill, width) {
      if (!bounds || typeof bounds.x !== "number" || typeof bounds.y !== "number") return;
      const a = scalePoint({x: bounds.x, y: bounds.y}, item);
      const b = scalePoint({x: bounds.x + bounds.w, y: bounds.y + bounds.h}, item);
      if (!a || !b) return;
      ctx.strokeStyle = stroke;
      ctx.fillStyle = fill || "transparent";
      ctx.lineWidth = width;
      ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
      if (fill) ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
    }

    function drawPoint(ctx, point, item, color, radius) {
      const scaled = scalePoint(point, item);
      if (!scaled) return;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(scaled.x, scaled.y, radius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(scaled.x - radius - 4, scaled.y);
      ctx.lineTo(scaled.x + radius + 4, scaled.y);
      ctx.moveTo(scaled.x, scaled.y - radius - 4);
      ctx.lineTo(scaled.x, scaled.y + radius + 4);
      ctx.stroke();
      return scaled;
    }

    function labelAt(ctx, point, text, color) {
      if (!point || !el.showLabels.checked) return;
      ctx.font = "12px Segoe UI, Arial";
      ctx.textBaseline = "top";
      const width = ctx.measureText(text).width + 8;
      ctx.fillStyle = "rgba(10, 14, 18, 0.8)";
      ctx.fillRect(point.x + 6, point.y + 6, width, 20);
      ctx.fillStyle = color;
      ctx.fillText(text, point.x + 10, point.y + 9);
    }

    function drawCandidate(ctx, candidate, selected) {
      const color = selected ? "#ffef7a" : "#ffd05c";
      const fill = selected ? "rgba(255, 208, 92, 0.22)" : "rgba(255, 208, 92, 0.10)";
      const width = selected ? 4 : 2;
      const geom = candidate.preferredAimGeometry;
      let labelPoint = null;
      if (Array.isArray(geom)) {
        drawPolygon(ctx, geom, candidate, color, fill, width);
      } else if (geom && typeof geom.x === "number" && typeof geom.w === "number") {
        drawBounds(ctx, geom, candidate, color, fill, width);
      } else if (geom && typeof geom.x === "number") {
        labelPoint = drawPoint(ctx, geom, candidate, color, selected ? 6 : 4);
      }
      labelPoint = drawPoint(ctx, candidate.aimPoint, candidate, color, selected ? 6 : 4) || labelPoint;
      const name = targetName(candidate.target || {});
      const prefix = el.showRankScore.checked ? `#${candidate.rankWithinScenario ?? "?"} ${candidate.score ?? ""} ` : "";
      labelAt(ctx, labelPoint, `${prefix}${name}`, color);
    }

    function drawContext(ctx, target) {
      const role = String(target.targetRole || "").toLowerCase();
      if (el.showObstacleContext.checked && !["obstacle", "navigation"].includes(role)) return;
      const color = role === "obstacle" ? "#7ec8ff" : "#73e1b4";
      const fill = role === "obstacle" ? "rgba(126, 200, 255, 0.06)" : "rgba(115, 225, 180, 0.06)";
      drawPolygon(ctx, target.tilePolygon || target.canvasTilePolygon, target, color, fill, 1);
      drawBounds(ctx, target.clickboxBounds || target.convexHullBounds, target, color, fill, 1);
      const point = target.canvasPoint || target.canvasLocation || target.canvasCenter;
      const labelPoint = drawPoint(ctx, point, target, color, 3);
      labelAt(ctx, labelPoint, targetName(target), color);
    }

    function renderOverlay() {
      const canvas = el.overlay;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = Number(el.opacity.value) || 0.75;
      const record = state.currentRecord || {};
      if (el.showContext.checked) {
        const maxContext = Math.max(0, Number(el.maxContext.value) || 50);
        for (const target of (record.context?.targets || []).slice(0, maxContext)) drawContext(ctx, target);
      }
      if (el.showCandidates.checked) {
        (record.selectedCandidates || []).forEach((candidate, index) => {
          if (index !== state.selectedCandidateIndex) drawCandidate(ctx, candidate, false);
        });
        const selected = (record.selectedCandidates || [])[state.selectedCandidateIndex];
        if (selected) drawCandidate(ctx, selected, true);
      }
      ctx.globalAlpha = 1;
      renderCoordinateDebug();
    }

    function syncCanvasSize() {
      const rect = el.frameImage.getBoundingClientRect();
      el.overlay.width = Math.max(1, Math.round(rect.width));
      el.overlay.height = Math.max(1, Math.round(rect.height));
      el.overlay.style.width = `${el.overlay.width}px`;
      el.overlay.style.height = `${el.overlay.height}px`;
      renderOverlay();
    }

    function renderCoordinateDebug() {
      if (!el.showDebug.checked) {
        el.coordinateDebug.style.display = "none";
        return;
      }
      const dims = tickDims();
      const canvasToFrameX = dims.canvasWidth && dims.frameWidth ? dims.frameWidth / dims.canvasWidth : null;
      const canvasToFrameY = dims.canvasHeight && dims.frameHeight ? dims.frameHeight / dims.canvasHeight : null;
      const renderScaleX = dims.frameWidth && dims.renderedWidth ? dims.renderedWidth / dims.frameWidth : null;
      const renderScaleY = dims.frameHeight && dims.renderedHeight ? dims.renderedHeight / dims.frameHeight : null;
      const mode = el.scaleCanvas.checked ? "canvasPixels -> framePixels -> rendered image" : "raw coordinates -> rendered image";
      const lines = [
        `tickId: ${(state.currentRecord || {}).tickId ?? "none"}`,
        `scale mode: ${mode}`,
        `image natural: ${state.frameNatural?.width || el.frameImage.naturalWidth || "unknown"} x ${state.frameNatural?.height || el.frameImage.naturalHeight || "unknown"}`,
        `image rendered: ${dims.renderedWidth || "unknown"} x ${dims.renderedHeight || "unknown"}`,
        `frame metadata: ${dims.frameMetadataWidth || "unknown"} x ${dims.frameMetadataHeight || "unknown"}`,
        `canvas metadata: ${dims.canvasWidth || "unknown"} x ${dims.canvasHeight || "unknown"}`,
        `canvas->frame scale: ${canvasToFrameX ? canvasToFrameX.toFixed(4) : "unknown"} x ${canvasToFrameY ? canvasToFrameY.toFixed(4) : "unknown"}`,
        `frame->rendered scale: ${renderScaleX ? renderScaleX.toFixed(4) : "unknown"} x ${renderScaleY ? renderScaleY.toFixed(4) : "unknown"}`,
      ];
      el.coordinateDebug.textContent = lines.join("\n");
      el.coordinateDebug.style.display = "block";
    }

    async function loadRecord(index) {
      state.currentIndex = Math.max(0, Math.min(index, state.records.length - 1));
      state.selectedCandidateIndex = 0;
      const payload = await api(`/api/record/${state.currentIndex}`);
      state.currentRecord = payload.record;
      state.currentDimensions = payload.dimensions || {};
      el.recordSelect.value = String(state.currentIndex);
      el.jumpTick.value = state.currentRecord.tickId ?? "";
      renderAll();
      loadFrame();
    }

    function loadFrame() {
      const record = state.currentRecord || {};
      const frameExists = record.frame?.exists;
      state.frameLoaded = false;
      state.frameNatural = null;
      el.frameImage.hidden = true;
      el.overlay.hidden = true;
      el.placeholder.hidden = !frameExists;
      if (!frameExists) {
        renderOverlay();
        return;
      }
      el.frameImage.src = `/api/frame/${record.tickId}?t=${Date.now()}`;
    }

    function renderDetails() {
      const record = state.currentRecord || {};
      const candidate = (record.selectedCandidates || [])[state.selectedCandidateIndex] || null;
      const compactRecord = {
        tickId: record.tickId,
        frame: record.frame,
        selectedCandidateCount: (record.selectedCandidates || []).length,
        contextCounts: {
          byRole: record.context?.countsByRole || {},
          byCategory: record.context?.countsByCategory || {},
        },
        warnings: record.warnings || [],
        safety: record.safety || {},
      };
      el.recordDetails.textContent = JSON.stringify(compactRecord, null, 2);
      el.candidateDetails.textContent = JSON.stringify(candidate || {}, null, 2);
    }

    function renderAll() {
      renderRecordSelect();
      renderTables();
      renderDetails();
      if (state.frameLoaded) syncCanvasSize();
    }

    async function init() {
      state.summary = await api("/api/summary");
      const recordsPayload = await api("/api/records");
      state.records = recordsPayload.records || [];
      renderSummary();
      renderRecordSelect();
      if (state.records.length) await loadRecord(0);
    }

    el.frameImage.addEventListener("load", () => {
      state.frameLoaded = true;
      state.frameNatural = {
        width: el.frameImage.naturalWidth,
        height: el.frameImage.naturalHeight,
      };
      el.frameImage.hidden = false;
      el.overlay.hidden = false;
      el.placeholder.hidden = true;
      requestAnimationFrame(syncCanvasSize);
    });
    el.frameImage.addEventListener("error", () => {
      state.frameLoaded = false;
      state.frameNatural = null;
      el.frameImage.hidden = true;
      el.overlay.hidden = true;
      el.placeholder.hidden = false;
    });
    window.addEventListener("resize", () => { if (state.frameLoaded) syncCanvasSize(); });
    el.prev.addEventListener("click", () => loadRecord(state.currentIndex - 1));
    el.next.addEventListener("click", () => loadRecord(state.currentIndex + 1));
    el.recordSelect.addEventListener("change", () => loadRecord(Number(el.recordSelect.value)));
    el.jump.addEventListener("click", () => {
      const tick = Number(el.jumpTick.value);
      const index = state.records.findIndex(row => row.tickId === tick);
      if (index >= 0) loadRecord(index);
    });
    for (const control of [el.scaleCanvas, el.showDebug, el.showCandidates, el.showContext, el.showObstacleContext, el.showLabels, el.showRankScore, el.opacity, el.maxContext]) {
      control.addEventListener("change", renderOverlay);
      control.addEventListener("input", renderOverlay);
    }
    init().catch(error => {
      el.messages.innerHTML = `<div class="warning">${escapeHtml(error.message || error)}</div>`;
    });
  </script>
</body>
</html>
"""


class ScenarioHandler(BaseHTTPRequestHandler):
    dataset: ScenarioDataset

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_html(html_page())
            return

        if path == "/api/summary":
            self.send_json(self.dataset.summary())
            return

        if path == "/api/records":
            self.send_json({"records": self.dataset.compact_records()})
            return

        if path.startswith("/api/record/"):
            self.handle_record(path)
            return

        if path.startswith("/api/frame/"):
            self.handle_frame(path)
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def handle_record(self, path: str) -> None:
        index_text = path.rsplit("/", 1)[-1]

        try:
            index = int(index_text)
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "record index must be an integer")
            return

        if index < 0 or index >= len(self.dataset.records):
            self.send_error_json(HTTPStatus.NOT_FOUND, "record index out of range")
            return

        record = self.dataset.records[index]
        tick_id = tick_id_for(record)
        dimensions = self.dataset.dimensions_for_tick(tick_id) if tick_id is not None else {}
        self.send_json({"index": index, "record": record, "dimensions": dimensions})

    def handle_frame(self, path: str) -> None:
        tick_text = path.rsplit("/", 1)[-1]

        try:
            tick_id = int(tick_text)
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "tick id must be an integer")
            return

        frame_path = self.dataset.frame_path_for_tick(tick_id)

        if frame_path is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame path unavailable or outside selected session")
            return

        if not frame_path.exists() or not frame_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame file missing or expired by retention")
            return

        content_type = mimetypes.guess_type(str(frame_path))[0] or "application/octet-stream"

        try:
            data = frame_path.read_bytes()
        except OSError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "frame file could not be read")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status=status)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read-only local browser inspector for scenario datasets. "
            "It overlays scenario-selected candidate geometry on retained frames for QA only."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO, help="Scenario dataset name. Default: bank_area.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local HTTP port, default {DEFAULT_PORT}.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    dataset = ScenarioDataset(session, args.scenario)
    dataset.load()
    ScenarioHandler.dataset = dataset
    server = ThreadingHTTPServer((HOST, args.port), ScenarioHandler)
    url = f"http://{HOST}:{args.port}/"

    print(f"Scenario inspector: {url}")
    print(f"Session: {session if session else 'none'}")
    print(f"Scenario: {args.scenario}")

    for message in dataset.messages:
        print(message)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping scenario inspector.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
