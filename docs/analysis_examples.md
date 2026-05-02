# Analysis Examples

The export tool writes generated summaries under the selected session:

```text
exports\session_index.json
exports\tick_summary.jsonl
exports\event_summary.jsonl
```

`telemetry-viewer\replay_viewer.py` is a local browser-based replay viewer for
already-collected telemetry. It is read-only and uses `telemetry_paths.py` for
segmented canonical sessions and legacy flat fallback where applicable:

```text
python telemetry-viewer\replay_viewer.py
python telemetry-viewer\replay_viewer.py --session "C:\path\to\session"
python telemetry-viewer\replay_viewer.py --sessions-dir "C:\path\to\sessions"
python telemetry-viewer\replay_viewer.py --port 8765
```

Example questions:

- When did HP drop?
  Compare `hpBoosted` across consecutive tick summaries.

- What NPC was I interacting with?
  Inspect `interactingTarget` on tick summaries, or `InteractingChanged` events.

- What item container changed?
  Filter event summaries where `eventType == "ItemContainerChanged"`.

- What menu options were available?
  Filter event summaries where `eventType == "MenuOpened"` and inspect the
  compact summary or the source event payload.

- What prayers were active?
  Read `activePrayerNames` from tick summaries.

- What was nearby when an event happened?
  Join `event_summary.tickId` to `tick_summary.tickId`, then inspect nearby
  entity/object counts or the original tick record.

- Is there a screenshot for a tick?
  Read `framePath`, `frameExists`, `framePending`,
  `frameExpiredOrMissing`, `frameCaptureStatus`, and `frameCaptureSource` from
  `exports\tick_summary.jsonl`. Missing files with a historical `framePath`
  usually mean frame retention has expired the image.
  If `frameCaptureSource` is `SCREEN_RECTANGLE`, check `frameCaptureWarning`
  because overlapping windows may appear in that frame.

- Why does `frameExists` briefly show false?
  Frame writes are asynchronous. `frameCaptureStatus == "QUEUED"` means the
  frame capture/write was requested. For the newest active tick, `framePending
  == true` means the image may still be arriving inside the shared freshness
  grace window. For older ticks, a missing frame is reported as
  `frameExpiredOrMissing == true`.
