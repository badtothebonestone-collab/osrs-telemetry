# Staircase Floor Selection Route Audit

Date: 2026-06-09

## Summary

The successful Bank-to-Woodcutting route recordings prove a direct multi-plane castle stair transition from plane 2 to plane 0. Two later recordings are labeled as using the `Bottom floor` option, but the inspected machine artifacts do not capture a `Bottom floor`, `Middle floor`, or `Top floor` menu row.

The current live blocker at `3206,3229,1` is therefore still a missing same-plane recovery fixture. It is not safe to invent a plane-1 Staircase click or loosen the strict Staircase guard.

## Recordings Inspected

| Recording | Bottom/Middle/Top floor text | Direct plane skip | Plane-1 evidence | Notes |
| --- | --- | --- | --- | --- |
| `recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2` | Not found | Plane 2 to 0, `Climb-down Staircase` | None | Current canonical guide source. |
| `recordings\20260606_121630_bank_to_WC` | Not found | Plane 2 to 0, `Climb-down Staircase` | None | Current canonical guide source. |
| `recordings\20260607_104613_Woodcutting_area_to_bank` | Not found | Plane 0 to 2, `Climb-down Trapdoor` | None | Reverse leg guide source. |
| `recordings\20260606_201613_Bank_to_tree_area` | Not found | Plane 2 to 0, `Climb-down Staircase` | None | Current canonical guide source; has target quality for Staircase object `56231`. |
| `recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor` | Recording label only | Plane 2 to 0, `Climb-down Staircase` | None | Label suggests Bottom floor, but no captured menu row proves it. |
| `recordings\20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area` | Recording label only | Plane 2 to 0, `Climb-down Staircase` | None | Label suggests Bottom floor, but no captured menu row proves it. |

## Route Guide Source

The current `route_guides\Bank_to_Woodcutting_area.route_guide.json` is extracted from:

- `recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2`
- `recordings\20260606_121630_bank_to_WC`
- `recordings\20260606_201613_Bank_to_tree_area`

It now records:

- `floorSelectionInteractions`: empty
- `directPlaneSkips`: three plane-2 to plane-0 Staircase transitions
- no plane-1 path points
- no plane-1 route interactions

## Staircase Evidence

Canonical Bank-to-Woodcutting transition:

| Field | Value |
| --- | --- |
| Target | `Staircase` |
| Object id | `56231` |
| World | `3205,3208,2` |
| Action | `Climb-down` |
| Source plane | `2` |
| Destination plane | `0` |
| Skipped plane | `1` |
| Postcondition | `plane_change` |

Latest stranded live state:

| Field | Value |
| --- | --- |
| Current world | `3206,3229,1` |
| Blocker | `route_guide_no_same_plane_reentry` |
| Nearest same-plane guide point | none |
| Nearest same-plane interaction | none |
| Inferred state | `intermediate_floor_between_route_transitions` |

## Conclusion

Existing evidence supports modeling a direct floor-skip transition, but it does not prove a safe plane-1 recovery action from `3206,3229,1`.

The correct behavior remains:

- keep strict action/id/plane matching
- do not use generic `Climb`
- do not click a name-only Staircase
- stop safely with `route_guide_no_same_plane_reentry`
- include `likelyReason`, `suggestedFixture`, and `safeState` in traces

## Needed Fixture

Record one short recovery sample starting at or near `3206,3229,1` that proves one of:

- the same Staircase exposes a safe `Bottom floor` option from plane 1
- a strict `Climb-down Staircase` route target with object id/world/plane evidence
- a same-plane movement waypoint that safely re-enters the demonstrated route

Until that fixture exists, no live rerun is safe from this blocker.
