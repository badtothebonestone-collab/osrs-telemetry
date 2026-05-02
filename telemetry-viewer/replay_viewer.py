import argparse
import json
import mimetypes
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parent))

from telemetry_paths import (  # noqa: E402
    classify_frame_state,
    find_newest_session,
    get_sessions_dir,
    is_segmented_session,
    iter_jsonl,
    list_event_files,
    list_tick_files,
    resolve_frame_path,
    safe_read_json,
    session_size_mb,
)


CATEGORY_BY_EVENT_TYPE = {
    "HitsplatApplied": "combat",
    "ProjectileMoved": "combat",
    "GraphicsObjectCreated": "combat",
    "InteractingChanged": "combat",
    "AnimationChanged": "combat",
    "NpcDeath": "combat",
    "ItemContainerChanged": "inventory",
    "ItemSpawned": "inventory",
    "ItemDespawned": "inventory",
    "ItemQuantityChanged": "inventory",
    "WidgetLoaded": "ui",
    "WidgetClosed": "ui",
    "MenuOpened": "ui",
    "VarbitChanged": "var",
    "VarClientIntChanged": "var",
    "VarClientStrChanged": "var",
    "NpcSpawned": "entity",
    "NpcDespawned": "entity",
    "NpcChanged": "entity",
    "PlayerSpawned": "entity",
    "PlayerDespawned": "entity",
    "PlayerChanged": "entity",
    "StatChanged": "skills",
    "GameStateChanged": "world",
    "OverheadTextChanged": "world",
}

MAX_INLINE_DICTIONARY_BYTES = 256 * 1024


def count_items(items) -> int:
    if not isinstance(items, list):
        return 0

    return sum(
        1
        for item in items
        if isinstance(item, dict)
        and item.get("itemId", -1) > 0
        and item.get("quantity", 0) > 0
    )


def actor_summary(actor) -> str | None:
    if not isinstance(actor, dict):
        return None

    actor_type = actor.get("actorType") or actor.get("type") or "UNKNOWN"
    name = actor.get("name") or actor.get("nameHash") or actor.get("id") or actor.get("index")
    animation = actor.get("animation")
    parts = [str(actor_type)]

    if name is not None:
        parts.append(str(name))

    if animation is not None:
        parts.append(f"anim={animation}")

    return " ".join(parts)


def interacting_target(status: dict) -> str | None:
    interacting_type = status.get("interactingType")
    interacting_name = status.get("interactingName") or status.get("interactingId")

    if interacting_type and interacting_type != "UNKNOWN":
        return f"{interacting_type}:{interacting_name}"

    return None


def event_summary_text(event_type: str | None, payload) -> str:
    if not isinstance(payload, dict):
        return ""

    if event_type == "StatChanged":
        return f"{payload.get('skill')} level={payload.get('level')} boosted={payload.get('boostedLevel')}"

    if event_type == "MenuOpened":
        entries = payload.get("entries") or []
        preview = []

        for entry in entries[:3]:
            if isinstance(entry, dict):
                preview.append(f"{entry.get('option', '')} {entry.get('target', '')}".strip())

        return f"menuEntryCount={payload.get('menuEntryCount')} entries={'; '.join(preview)}"

    if event_type == "ItemContainerChanged":
        return f"containerId={payload.get('containerId')} size={payload.get('size')}"

    if event_type in ("ItemSpawned", "ItemDespawned", "ItemQuantityChanged"):
        return f"id={payload.get('id')} qty={payload.get('quantity')} {payload.get('worldX')},{payload.get('worldY')}"

    if event_type in (
        "AnimationChanged",
        "NpcSpawned",
        "NpcDespawned",
        "PlayerSpawned",
        "PlayerDespawned",
        "PlayerChanged",
        "NpcDeath",
    ):
        return actor_summary(payload.get("actor") if "actor" in payload else payload) or ""

    if event_type == "InteractingChanged":
        return f"{actor_summary(payload.get('source'))} -> {actor_summary(payload.get('target'))}"

    if event_type == "HitsplatApplied":
        return f"{actor_summary(payload.get('actor'))} amount={payload.get('amount')} type={payload.get('hitsplatType')}"

    if event_type == "ProjectileMoved":
        return f"id={payload.get('id')} target={actor_summary(payload.get('target'))}"

    if event_type == "GraphicsObjectCreated":
        return f"id={payload.get('id')} at={payload.get('worldX')},{payload.get('worldY')}"

    if event_type == "GameStateChanged":
        return f"gameState={payload.get('gameState')}"

    if event_type and event_type.startswith("Var"):
        return " ".join(
            f"{key}={payload.get(key)}"
            for key in ("index", "varbitId", "varpId", "value")
            if key in payload
        )

    return " ".join(f"{key}={value}" for key, value in list(payload.items())[:4])


def summarize_event(source: Path, event: dict) -> dict:
    event_type = event.get("eventType")
    payload = event.get("payload")

    return {
        "tickId": event.get("tickId"),
        "timestampUtc": event.get("timestampUtc"),
        "eventType": event_type,
        "category": CATEGORY_BY_EVENT_TYPE.get(event_type, "unknown"),
        "summary": event_summary_text(event_type, payload),
        "source": str(source),
    }


def summarize_tick(
    session_path: Path,
    source: Path,
    tick: dict,
    *,
    is_latest: bool,
    active_session: bool,
) -> dict:
    local_player = tick.get("localPlayer") or {}
    status = tick.get("status") or {}
    active_prayers = [
        prayer.get("name")
        for prayer in (tick.get("activePrayers") or [])
        if isinstance(prayer, dict) and prayer.get("active") and prayer.get("name")
    ]
    frame = classify_frame_state(
        session_path,
        tick,
        is_latest=is_latest,
        active_session=active_session,
    )

    return {
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "gameState": tick.get("gameState"),
        "worldX": local_player.get("worldX"),
        "worldY": local_player.get("worldY"),
        "plane": local_player.get("plane"),
        "hpBoosted": status.get("hitpointsBoosted"),
        "hpReal": status.get("hitpointsReal"),
        "prayerBoosted": status.get("prayerBoosted"),
        "prayerReal": status.get("prayerReal"),
        "runEnergyPercent": status.get("runEnergyPercent"),
        "inventoryCount": count_items(tick.get("inventory")),
        "equipmentCount": count_items(tick.get("equipment")),
        "npcCount": len(tick.get("npcs") or []),
        "playerCount": len(tick.get("players") or []),
        "widgetCount": len(tick.get("widgets") or []),
        "sceneObjectsCount": len(tick.get("sceneObjects") or []),
        "groundItemsCount": len(tick.get("groundItems") or []),
        "activePrayerNames": active_prayers,
        "interactingTarget": interacting_target(status),
        "framePath": frame["framePath"],
        "frameExists": frame["frameExists"],
        "framePending": frame["framePending"],
        "frameExpiredOrMissing": frame["frameExpiredOrMissing"],
        "frameCaptureStatus": frame["frameCaptureStatus"],
        "frameCaptureSource": frame["frameCaptureSource"],
        "captureErrorCount": len(tick.get("captureErrors") or []),
        "source": str(source),
    }


def load_dictionaries(session_path: Path) -> dict:
    dictionary_dir = session_path / "dictionaries"
    summaries = {}

    for name in ("items", "npcs", "objects"):
        path = dictionary_dir / f"{name}.json"
        entry = {"exists": path.exists(), "count": 0, "inline": None}

        if path.exists():
            try:
                entry["sizeBytes"] = path.stat().st_size
            except OSError:
                entry["sizeBytes"] = None

            data = safe_read_json(path)

            if isinstance(data, dict):
                entry["count"] = len(data)

                if entry.get("sizeBytes") is not None and entry["sizeBytes"] <= MAX_INLINE_DICTIONARY_BYTES:
                    entry["inline"] = data
            elif isinstance(data, list):
                entry["count"] = len(data)

                if entry.get("sizeBytes") is not None and entry["sizeBytes"] <= MAX_INLINE_DICTIONARY_BYTES:
                    entry["inline"] = data

        summaries[name] = entry

    return summaries


def session_from_arg(session: str | None, sessions_dir: str | None) -> Path | None:
    if session:
        return Path(session).expanduser().resolve()

    return find_newest_session(get_sessions_dir(sessions_dir))


def load_replay(session_path: Path) -> dict:
    manifest = safe_read_json(session_path / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else None
    tick_files = list_tick_files(session_path)
    event_files = list_event_files(session_path)
    raw_ticks = []
    raw_tick_by_id = {}
    tick_summaries = []
    events = []
    events_by_tick = defaultdict(list)
    event_type_counts = Counter()
    active_session = bool(manifest and manifest.get("active"))

    for source, tick in iter_jsonl(tick_files):
        if not isinstance(tick, dict):
            continue

        raw_ticks.append((source, tick))
        tick_id = tick.get("tickId")

        if tick_id is not None:
            raw_tick_by_id[str(tick_id)] = tick

    latest_tick = raw_ticks[-1][1] if raw_ticks else None

    for source, tick in raw_ticks:
        summary = summarize_tick(
            session_path,
            source,
            tick,
            is_latest=tick is latest_tick,
            active_session=active_session,
        )
        tick_summaries.append(summary)

    for source, event in iter_jsonl(event_files):
        if not isinstance(event, dict):
            continue

        summary = summarize_event(source, event)
        events.append(summary)
        event_type_counts[summary.get("eventType") or "UNKNOWN"] += 1
        tick_id = summary.get("tickId")

        if tick_id is not None:
            try:
                events_by_tick[int(tick_id)].append(summary)
            except (TypeError, ValueError):
                pass

    first_tick_id = tick_summaries[0].get("tickId") if tick_summaries else None
    last_tick_id = tick_summaries[-1].get("tickId") if tick_summaries else None

    return {
        "sessionPath": session_path,
        "manifest": manifest,
        "layout": "segmented" if is_segmented_session(session_path) else "legacy-flat",
        "tickFiles": tick_files,
        "eventFiles": event_files,
        "rawTickById": raw_tick_by_id,
        "tickSummaries": tick_summaries,
        "events": events,
        "eventsByTick": events_by_tick,
        "eventTypeCounts": dict(event_type_counts.most_common(20)),
        "dictionaries": load_dictionaries(session_path),
        "loadedAtUtc": datetime.now(timezone.utc).isoformat(),
        "firstTickId": first_tick_id,
        "lastTickId": last_tick_id,
    }


def is_under_directory(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def html_page() -> bytes:
    body = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OSRS Telemetry Replay Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #656a70;
      --line: #d9ddd5;
      --accent: #2f6f73;
      --accent-strong: #1f5154;
      --warn-bg: #fff6df;
      --warn-text: #694a00;
      --code-bg: #f0f2ef;
    }

    * {
      box-sizing: border-box;
    }

    html,
    body {
      height: 100%;
      overflow: hidden;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.4;
    }

    button,
    input,
    select {
      font: inherit;
    }

    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 0.45rem 0.65rem;
      cursor: pointer;
    }

    button:hover {
      border-color: var(--accent);
    }

    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }

    button.primary:hover {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
    }

    input,
    select {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 0.45rem 0.55rem;
      min-width: 0;
    }

    .app {
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }

    header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      min-height: 0;
    }

    h1 {
      font-size: 1rem;
      margin: 0;
      font-weight: 700;
    }

    .session-meta {
      color: var(--muted);
      font-size: 0.875rem;
      text-align: right;
      overflow-wrap: anywhere;
    }

    main {
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(360px, 1fr);
      gap: 0.75rem;
      min-height: 0;
      overflow: hidden;
      padding: 0.75rem;
    }

    .frame-panel,
    .detail-panel {
      min-width: 0;
      min-height: 0;
    }

    .frame-panel {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
    }

    .frame-topline {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 0.45rem 0.6rem;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.9rem;
    }

    .frame-actions {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      min-width: 0;
    }

    .frame-wrap {
      flex: 1;
      min-height: 0;
      background: #111612;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }

    #frameImage {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      display: none;
    }

    #frameImage.actual-width {
      width: auto;
      max-width: none;
      height: auto;
      max-height: 100%;
    }

    .missing-frame {
      max-width: 28rem;
      padding: 1rem;
      color: #e8ece7;
      text-align: center;
    }

    .detail-panel {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      overflow-y: auto;
      padding-right: 0.15rem;
    }

    .section {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      min-width: 0;
      overflow: hidden;
      flex: 0 0 auto;
    }

    .section.events-section {
      display: flex;
      flex-direction: column;
      min-height: 0;
      max-height: 34vh;
    }

    .section.raw-section {
      overflow: visible;
    }

    .section h2 {
      margin: 0;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--line);
      font-size: 0.95rem;
    }

    details.section summary {
      cursor: pointer;
      padding: 0.65rem 0.75rem;
      font-size: 0.95rem;
      font-weight: 700;
      border-bottom: 1px solid transparent;
    }

    details.section[open] summary {
      border-bottom-color: var(--line);
    }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.5rem 0.75rem;
      padding: 0.75rem;
      font-size: 0.9rem;
    }

    .metric {
      min-width: 0;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
    }

    .metric strong {
      overflow-wrap: anywhere;
    }

    .events-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--line);
    }

    .events-head h2 {
      padding: 0;
      border: 0;
    }

    #eventFilter {
      width: 12rem;
    }

    .events-list {
      max-height: 28vh;
      overflow: auto;
      padding: 0.5rem 0.75rem;
      font-size: 0.86rem;
    }

    .event-row {
      padding: 0.4rem 0;
      border-bottom: 1px solid #edf0eb;
    }

    .event-row:last-child {
      border-bottom: 0;
    }

    .event-type {
      font-weight: 700;
    }

    .event-meta {
      color: var(--muted);
      font-size: 0.78rem;
    }

    pre {
      margin: 0;
      max-height: 28vh;
      overflow: auto;
      background: var(--code-bg);
      padding: 0.75rem;
      font-size: 0.78rem;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    footer {
      display: grid;
      grid-template-columns: auto auto auto minmax(12rem, 1fr) auto auto auto;
      gap: 0.6rem;
      align-items: center;
      padding: 0.55rem 0.75rem;
      border-top: 1px solid var(--line);
      background: var(--panel);
      z-index: 10;
    }

    #timeline {
      width: 100%;
    }

    .jump {
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }

    .status {
      color: var(--muted);
      font-size: 0.85rem;
      overflow-wrap: anywhere;
    }

    .warning {
      background: var(--warn-bg);
      color: var(--warn-text);
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid #f0d590;
      display: none;
    }

    @media (max-width: 900px) {
      main {
        grid-template-columns: 1fr;
        grid-template-rows: minmax(0, 1fr) minmax(240px, 0.9fr);
      }

      .frame-panel {
        min-height: 0;
      }

      .detail-panel {
        min-height: 0;
      }

      footer {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      footer > * {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>OSRS Telemetry Replay Viewer</h1>
      <div class="session-meta" id="sessionMeta">Loading session...</div>
    </header>

    <main>
      <section class="frame-panel">
        <div class="frame-topline">
          <div id="tickLabel">Tick -</div>
          <div class="frame-actions">
            <div id="timestampLabel">-</div>
            <button id="fitToggle" type="button">Fit: contain</button>
          </div>
        </div>
        <div class="frame-wrap">
          <img id="frameImage" alt="Telemetry frame">
          <div id="missingFrame" class="missing-frame">
            Frame missing, pending, or expired by retention. Tick data is still valid.
          </div>
        </div>
      </section>

      <section class="detail-panel">
        <div id="loadWarning" class="warning"></div>

        <section class="section">
          <h2>Tick Summary</h2>
          <div class="summary-grid" id="summaryGrid"></div>
        </section>

        <section class="section events-section">
          <div class="events-head">
            <h2>Recent Events</h2>
            <input id="eventFilter" type="search" placeholder="Filter event type">
          </div>
          <div class="events-list" id="eventsList"></div>
        </section>

        <details class="section raw-section">
          <summary>Raw Tick JSON</summary>
          <pre id="rawJson">{}</pre>
        </details>
      </section>
    </main>

    <footer>
      <button id="prevTick" type="button">Previous tick</button>
      <button id="playPause" class="primary" type="button">Play</button>
      <input id="timeline" type="range" min="0" max="0" value="0">
      <button id="nextTick" type="button">Next tick</button>
      <div class="jump">
        <input id="jumpTick" type="number" placeholder="tickId">
        <button id="jumpButton" type="button">Jump</button>
      </div>
      <select id="speed">
        <option value="1200">0.5x</option>
        <option value="600" selected>1x</option>
        <option value="300">2x</option>
        <option value="120">5x</option>
      </select>
      <div class="status" id="statusText">No ticks loaded</div>
    </footer>
  </div>

  <script>
    const state = {
      session: null,
      ticks: [],
      currentIndex: 0,
      currentEvents: [],
      rawTick: null,
      playTimer: null,
      frameFit: "contain"
    };

    const el = {
      sessionMeta: document.getElementById("sessionMeta"),
      tickLabel: document.getElementById("tickLabel"),
      timestampLabel: document.getElementById("timestampLabel"),
      fitToggle: document.getElementById("fitToggle"),
      frameImage: document.getElementById("frameImage"),
      missingFrame: document.getElementById("missingFrame"),
      loadWarning: document.getElementById("loadWarning"),
      summaryGrid: document.getElementById("summaryGrid"),
      eventsList: document.getElementById("eventsList"),
      eventFilter: document.getElementById("eventFilter"),
      rawJson: document.getElementById("rawJson"),
      prevTick: document.getElementById("prevTick"),
      nextTick: document.getElementById("nextTick"),
      playPause: document.getElementById("playPause"),
      timeline: document.getElementById("timeline"),
      jumpTick: document.getElementById("jumpTick"),
      jumpButton: document.getElementById("jumpButton"),
      speed: document.getElementById("speed"),
      statusText: document.getElementById("statusText")
    };

    async function fetchJson(url) {
      const response = await fetch(url, { cache: "no-store" });

      if (!response.ok) {
        throw new Error(`${url} returned ${response.status}`);
      }

      return response.json();
    }

    function valueOrDash(value) {
      return value === null || value === undefined || value === "" ? "-" : value;
    }

    function setWarning(message) {
      el.loadWarning.textContent = message || "";
      el.loadWarning.style.display = message ? "block" : "none";
    }

    function metric(label, value) {
      return `<div class="metric"><span>${label}</span><strong>${valueOrDash(value)}</strong></div>`;
    }

    function renderSummary(tick) {
      const position = [tick.worldX, tick.worldY, tick.plane].map(valueOrDash).join(", ");
      const hp = `${valueOrDash(tick.hpBoosted)} / ${valueOrDash(tick.hpReal)}`;
      const prayer = `${valueOrDash(tick.prayerBoosted)} / ${valueOrDash(tick.prayerReal)}`;
      const activePrayers = Array.isArray(tick.activePrayerNames) && tick.activePrayerNames.length
        ? tick.activePrayerNames.join(", ")
        : "-";
      const frameState = tick.frameExists
        ? "exists"
        : tick.framePending
          ? "pending"
          : tick.frameExpiredOrMissing
            ? "expiredOrMissing"
            : "-";

      el.summaryGrid.innerHTML = [
        metric("Game state", tick.gameState),
        metric("Position", position),
        metric("HP", hp),
        metric("Prayer", prayer),
        metric("Run", tick.runEnergyPercent),
        metric("Active prayers", activePrayers),
        metric("Interacting", tick.interactingTarget),
        metric("Inventory", tick.inventoryCount),
        metric("Equipment", tick.equipmentCount),
        metric("NPCs / players", `${valueOrDash(tick.npcCount)} / ${valueOrDash(tick.playerCount)}`),
        metric("Scene / ground", `${valueOrDash(tick.sceneObjectsCount)} / ${valueOrDash(tick.groundItemsCount)}`),
        metric("Widgets", tick.widgetCount),
        metric("Frame state", frameState),
        metric("Capture source", tick.frameCaptureSource),
        metric("Capture status", tick.frameCaptureStatus),
        metric("Capture errors", tick.captureErrorCount)
      ].join("");
    }

    function renderEvents() {
      const filter = el.eventFilter.value.trim().toLowerCase();
      const events = state.currentEvents.filter((event) => {
        if (!filter) {
          return true;
        }

        return String(event.eventType || "").toLowerCase().includes(filter)
          || String(event.category || "").toLowerCase().includes(filter);
      });

      if (!events.length) {
        el.eventsList.innerHTML = `<div class="event-row">No nearby events.</div>`;
        return;
      }

      el.eventsList.innerHTML = events.map((event) => `
        <div class="event-row">
          <div><span class="event-type">${valueOrDash(event.eventType)}</span> ${valueOrDash(event.summary)}</div>
          <div class="event-meta">tick ${valueOrDash(event.tickId)} - ${valueOrDash(event.category)} - ${valueOrDash(event.timestampUtc)}</div>
        </div>
      `).join("");
    }

    function setFrame(tick) {
      el.frameImage.style.display = "none";
      el.missingFrame.style.display = "block";
      el.frameImage.removeAttribute("src");
      el.frameImage.classList.toggle("actual-width", state.frameFit === "actual");

      if (!tick || !tick.frameExists) {
        return;
      }

      el.frameImage.onload = () => {
        el.missingFrame.style.display = "none";
        el.frameImage.style.display = "block";
      };
      el.frameImage.onerror = () => {
        el.frameImage.style.display = "none";
        el.missingFrame.style.display = "block";
      };
      el.frameImage.src = `/api/frame/${encodeURIComponent(tick.tickId)}?v=${Date.now()}`;
    }

    async function selectIndex(index) {
      if (!state.ticks.length) {
        return;
      }

      state.currentIndex = Math.max(0, Math.min(index, state.ticks.length - 1));
      const tick = state.ticks[state.currentIndex];
      el.timeline.value = String(state.currentIndex);
      el.jumpTick.value = tick.tickId ?? "";
      el.tickLabel.textContent = `Tick ${valueOrDash(tick.tickId)}`;
      el.timestampLabel.textContent = valueOrDash(tick.timestampUtc);
      el.statusText.textContent = `${state.currentIndex + 1} of ${state.ticks.length}`;
      renderSummary(tick);
      setFrame(tick);
      setWarning("");

      try {
        const [rawTick, eventPayload] = await Promise.all([
          fetchJson(`/api/tick/${encodeURIComponent(tick.tickId)}`),
          fetchJson(`/api/events?tick=${encodeURIComponent(tick.tickId)}&window=5`)
        ]);
        state.rawTick = rawTick;
        state.currentEvents = eventPayload.events || [];
        el.rawJson.textContent = JSON.stringify(rawTick, null, 2);
        renderEvents();
      } catch (error) {
        setWarning(error.message);
      }
    }

    function selectByTickId(tickId) {
      const requested = String(tickId);
      const index = state.ticks.findIndex((tick) => String(tick.tickId) === requested);

      if (index >= 0) {
        selectIndex(index);
      } else {
        setWarning(`Tick not found: ${requested}`);
      }
    }

    function stopPlayback() {
      if (state.playTimer) {
        clearInterval(state.playTimer);
        state.playTimer = null;
      }

      el.playPause.textContent = "Play";
    }

    function startPlayback() {
      stopPlayback();
      el.playPause.textContent = "Pause";
      state.playTimer = setInterval(() => {
        if (state.currentIndex >= state.ticks.length - 1) {
          stopPlayback();
          return;
        }

        selectIndex(state.currentIndex + 1);
      }, Number(el.speed.value));
    }

    function toggleFrameFit() {
      state.frameFit = state.frameFit === "contain" ? "actual" : "contain";
      el.fitToggle.textContent = state.frameFit === "contain" ? "Fit: contain" : "Fit: actual width";
      el.frameImage.classList.toggle("actual-width", state.frameFit === "actual");
    }

    function isTextInput(target) {
      return target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement;
    }

    async function init() {
      try {
        const [session, ticks] = await Promise.all([
          fetchJson("/api/session"),
          fetchJson("/api/ticks")
        ]);
        state.session = session;
        state.ticks = ticks;
        el.sessionMeta.textContent = `${session.layout || "session"} - ${session.tickCount || 0} ticks - ${session.sessionPath || ""}`;
        el.timeline.max = String(Math.max(0, ticks.length - 1));

        if (!ticks.length) {
          setWarning("No ticks found in this session.");
          return;
        }

        await selectIndex(0);
      } catch (error) {
        setWarning(error.message);
        el.sessionMeta.textContent = "Unable to load session";
      }
    }

    el.prevTick.addEventListener("click", () => selectIndex(state.currentIndex - 1));
    el.nextTick.addEventListener("click", () => selectIndex(state.currentIndex + 1));
    el.fitToggle.addEventListener("click", toggleFrameFit);
    el.timeline.addEventListener("input", () => selectIndex(Number(el.timeline.value)));
    el.jumpButton.addEventListener("click", () => selectByTickId(el.jumpTick.value));
    el.jumpTick.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        selectByTickId(el.jumpTick.value);
      }
    });
    el.playPause.addEventListener("click", () => {
      if (state.playTimer) {
        stopPlayback();
      } else {
        startPlayback();
      }
    });
    el.speed.addEventListener("change", () => {
      if (state.playTimer) {
        startPlayback();
      }
    });
    el.eventFilter.addEventListener("input", renderEvents);
    document.addEventListener("keydown", (event) => {
      if (isTextInput(event.target)) {
        return;
      }

      if (event.key === "ArrowRight") {
        event.preventDefault();
        selectIndex(state.currentIndex + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        selectIndex(state.currentIndex - 1);
      } else if (event.key === " ") {
        event.preventDefault();

        if (state.playTimer) {
          stopPlayback();
        } else {
          startPlayback();
        }
      }
    });

    init();
  </script>
</body>
</html>"""
    return body.encode("utf-8")


class ReplayHandler(BaseHTTPRequestHandler):
    replay = None

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, data: bytes):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_missing(self, message: str, status=HTTPStatus.NOT_FOUND):
        self.send_json({"error": message}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self.send_html(html_page())
            return

        if path == "/api/session":
            self.handle_session()
            return

        if path == "/api/ticks":
            self.send_json(self.replay["tickSummaries"])
            return

        if path.startswith("/api/tick/"):
            self.handle_tick(path.removeprefix("/api/tick/"))
            return

        if path == "/api/events":
            self.handle_events(parse_qs(parsed.query))
            return

        if path.startswith("/api/frame/"):
            self.handle_frame(path.removeprefix("/api/frame/"))
            return

        if path == "/api/dictionaries":
            self.send_json(self.replay["dictionaries"])
            return

        self.send_missing("Not found")

    def handle_session(self):
        session_path = self.replay["sessionPath"]
        payload = {
            "sessionPath": str(session_path),
            "manifest": self.replay["manifest"],
            "layout": self.replay["layout"],
            "loadedAtUtc": self.replay["loadedAtUtc"],
            "sessionSizeMb": round(session_size_mb(session_path), 3),
            "tickCount": len(self.replay["tickSummaries"]),
            "eventCount": len(self.replay["events"]),
            "tickSegmentCount": len(self.replay["tickFiles"]),
            "eventSegmentCount": len(self.replay["eventFiles"]),
            "firstTickId": self.replay["firstTickId"],
            "lastTickId": self.replay["lastTickId"],
            "topEventTypeCounts": self.replay["eventTypeCounts"],
            "dictionarySummaries": {
                key: {
                    item_key: item_value
                    for item_key, item_value in value.items()
                    if item_key != "inline"
                }
                for key, value in self.replay["dictionaries"].items()
            },
        }
        self.send_json(payload)

    def handle_tick(self, tick_id: str):
        tick = self.replay["rawTickById"].get(tick_id)

        if tick is None:
            self.send_missing(f"Tick not found: {tick_id}")
            return

        self.send_json(tick)

    def handle_events(self, query: dict):
        tick_values = query.get("tick") or []

        if not tick_values:
            self.send_json(self.replay["events"])
            return

        try:
            selected_tick = int(tick_values[0])
            window = int((query.get("window") or ["5"])[0])
        except ValueError:
            self.send_missing("tick and window must be integers", HTTPStatus.BAD_REQUEST)
            return

        window = max(0, min(window, 1000))
        start = selected_tick - window
        end = selected_tick + window
        events = []

        for tick_id in range(start, end + 1):
            events.extend(self.replay["eventsByTick"].get(tick_id, []))

        self.send_json(
            {
                "tick": selected_tick,
                "window": window,
                "startTick": start,
                "endTick": end,
                "events": events,
            }
        )

    def handle_frame(self, tick_id: str):
        tick = self.replay["rawTickById"].get(tick_id)

        if tick is None:
            self.send_missing(f"Tick not found: {tick_id}")
            return

        frame_path_value = tick.get("framePath")
        frame_path = resolve_frame_path(self.replay["sessionPath"], frame_path_value)

        if frame_path is None:
            self.send_missing(f"No frame associated with tick: {tick_id}")
            return

        if not is_under_directory(frame_path, self.replay["sessionPath"]):
            self.send_missing("Frame path escapes the session directory", HTTPStatus.FORBIDDEN)
            return

        if not frame_path.exists() or not frame_path.is_file():
            self.send_missing(f"Frame missing for tick: {tick_id}")
            return

        content_type = mimetypes.guess_type(frame_path.name)[0] or "application/octet-stream"

        try:
            data = frame_path.read_bytes()
        except OSError:
            self.send_missing(f"Unable to read frame for tick: {tick_id}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args():
    parser = argparse.ArgumentParser(description="Serve a local browser API for OSRS telemetry replay.")
    parser.add_argument("--session", help="Path to a specific telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory.")
    parser.add_argument("--port", type=int, default=8765, help="Local server port. Default: 8765.")
    return parser.parse_args()


def main():
    args = parse_args()
    session_path = session_from_arg(args.session, args.sessions_dir)

    if session_path is None:
        sessions_dir = get_sessions_dir(args.sessions_dir)
        print(f"No telemetry sessions found in: {sessions_dir}", file=sys.stderr)
        return 1

    if not session_path.exists() or not session_path.is_dir():
        print(f"Session directory does not exist: {session_path}", file=sys.stderr)
        return 1

    replay = load_replay(session_path)
    ReplayHandler.replay = replay
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReplayHandler)
    url = f"http://127.0.0.1:{args.port}/"

    print(f"Serving telemetry replay: {session_path}")
    print(f"Open: {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping replay viewer.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
