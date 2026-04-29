# Analysis Examples

The export tool writes `exports\tick_summary.jsonl` and
`exports\event_summary.jsonl` for lightweight scripts.

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
  Read `framePath`, `frameExists`, `frameCaptureStatus`, and
  `frameCaptureSource` from `exports\tick_summary.jsonl`. Missing files with a
  historical `framePath` usually mean frame retention has expired the image.
  If `frameCaptureSource` is `SCREEN_RECTANGLE`, check `frameCaptureWarning`
  because overlapping windows may appear in that frame.
