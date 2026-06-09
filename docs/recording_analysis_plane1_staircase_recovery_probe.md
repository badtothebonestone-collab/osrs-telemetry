# Plane-1 Staircase Recovery Probe

Date: 2026-06-09

## Summary

A focused plane-1 Staircase recovery probe was added and run instead of another full woodcutting loop. The probe did not perform a route transition. It moved the live pointer to candidate Staircase aim points, attempted hover/right-click menu capture, and wrote structured artifacts.

The probe found stale route/object context for the expected plane-1 Staircase, but it did not capture fresh menu evidence. A screenshot taken immediately after the probe showed RuneLite on the disconnected/login screen, so the daemon's `3206,3229,1` context and `Cancel` menu samples are not safe route evidence.

## Artifacts

| Artifact | Path | Result |
| --- | --- | --- |
| Latest probe folder | `recordings\20260609_171349_plane1_staircase_recovery_probe` | WARN |
| Summary JSON | `recordings\20260609_171349_plane1_staircase_recovery_probe\plane1_staircase_recovery_probe.json` | Written |
| Attempts JSONL | `recordings\20260609_171349_plane1_staircase_recovery_probe\plane1_staircase_recovery_probe.jsonl` | Written |
| Schema gap report | `recordings\20260609_171349_plane1_staircase_recovery_probe\schema_gap_report.md` | Written |
| Screenshot after probe | `recordings\20260609_171349_plane1_staircase_recovery_probe\screen_after_probe.png` | Shows disconnected/login screen |

Earlier probe folders:

- `recordings\20260609_171156_plane1_staircase_recovery_probe`: found the candidate object, but no attempts were made before bounds extraction was fixed.
- `recordings\20260609_171227_plane1_staircase_recovery_probe`: attempted movement while the pointer was outside the allowed calibrated region.

## Probe Answers

| Question | Answer |
| --- | --- |
| Are we at/near `3206,3229,1`? | The daemon reported `3206,3229,1`, distance `0`, but the source was later proven stale. |
| What Staircase objects are visible on plane 1? | Stale daemon context listed `Staircase` object id `16672` at `3204,3229,1`. |
| Which object ids/world points exist? | Stale object evidence: `16672`, world `3204,3229,1`, distance `2` from reported player tile. |
| What top menu appears when hovering? | Stale menu sample reported `Cancel`, not a route option. |
| Does the right-click menu contain route options? | No fresh `Bottom floor`, `Climb-down`, `Climb-up`, `Middle floor`, or `Top floor` row was captured. |
| Are menu row bounds captured? | The stale `Cancel` rows lacked useful route-row bounds. |
| Is target id/plane/world enough for strict matching? | No. Object identity alone is not enough; strict recovery also needs fresh menu/action evidence from that object and plane. |
| Is there a safe same-plane recovery action? | No proven action yet. |
| Was the route guide updated? | No. Updating it would require inventing unproven plane-1 behavior. |

## Captured Candidate Evidence

The intended target candidate from stale context was:

```text
target: Staircase
objectId: 16672
world: 3204,3229,1
expected player state: 3206,3229,1
attempted canvas points: 191,146; 182,135; 200,135; 191,158
attempted screen points: about 455,309; 440,290; 469,290; 455,330
```

This is useful for the next focused probe, but it is not enough to satisfy the route guide.

## Stale Source Evidence

The menu samples were stale:

```text
top menu: Cancel
postMenuSortAgeMillis: about 5,186,000 to 5,187,000 ms
route options captured: none
```

The canonical snapshot freshness check reported:

```text
fresh: false
allCachedPacketsStale: true
staleReasons: plugin_all_packets_stale
```

The screen observed after the probe showed RuneLite disconnected from the server, so no fresh plane-1 hover/menu proof was available.

## Decision

Keep the blocker:

```text
route_guide_no_same_plane_reentry
```

Do not enrich either route guide from this probe. The missing evidence is still a fresh plane-1 menu/action capture for the Staircase at or near `3206,3229,1`.

## Next Focused Step

After RuneLite is reconnected and the character is again at or near `3206,3229,1`, run:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\plane1_staircase_recovery_probe.py --json
```

Only update the route guide if the probe captures a fresh route-relevant menu/action row such as `Bottom floor` or `Climb-down` for the strict Staircase object and plane.
