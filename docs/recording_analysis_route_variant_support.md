# Route Variant Support

## What Changed

Route template comparison now supports registered variants and
navigation-support substitutions while still using `traversal_lifecycle`
`routeSegments` as the primary route model.

The base template is unchanged as the canonical route when the template
semantics are correct. Variants add targeted alternative ways to satisfy one
required segment when postconditions, target quality, and endpoint evidence are
strong.

Follow-up audit note: user review later confirmed that
`Bank_to_Woodcutting_area` does not require Door/Open. That means the
`walk_here_large_door` variant was compensating for an over-strict required
segment. The corrected revision-2 template demotes both `Open Door` and
`Walk here Large door` to navigation/support evidence, and the old variant is
deprecated.

## Schemas

- `route_template_variant.v1`
- `route_navigation_support_substitution.v1`

Comparison still writes:

```text
route_template_comparison.json
```

The comparison now includes:

- `statusReason`
- `matchedVariantName`
- `matchedVariantNames`
- `navigationSupportSubstitutions`
- `allowedExtraSegments`
- `validUnregisteredVariant`

## Status Reasons

- `PASS_BASE_TEMPLATE`: direct template match.
- `PASS_REGISTERED_VARIANT`: registered variant satisfied a required segment.
- `WARN_VALID_UNREGISTERED_VARIANT`: valid substitution found, but not yet
  registered.
- `WARN_EXTRA_REVIEW_EVIDENCE`: route matched with extra review-only evidence.
- `WARN_PARTIAL_BUT_ENDPOINT_REACHED`: endpoint is right but evidence is less
  direct.
- `FAIL_MISSING_REQUIRED_SEGMENT`: required progress was not satisfied.
- `FAIL_WRONG_ENDPOINT`: route ended in the wrong area.
- `FAIL_OUT_OF_ORDER_REQUIRED_SEGMENT`: strict progress happened out of order.
- `FAIL_FAILED_POSTCONDITION`: a required postcondition failed.

## Navigation-Support Matching

Navigation-support segments can satisfy route progress only when the route
still reaches the expected endpoint and the movement/postcondition evidence is
strong.

The concrete variant originally registered in this pass:

- Base segment: `door_transition`, `Open Door`
- Variant: `walk_segment`, `Walk here Large door`
- Required postcondition: movement
- Minimum movement: `3` tiles
- Minimum target quality: `medium`
- Source recording:
  `C:\Users\badto\osrs-telemetry\recordings\20260606_105427_manual_route-bank_to_woodcutting_area_v3`

After registration, the second fresh run compared as:

- Status: `PASS`
- Status reason: `PASS_REGISTERED_VARIANT`
- Matched variant: `walk_here_large_door`
- Score: `1.0`
- Missing segments: `0`
- Extra segments: `0`

## Minimap / Navigation Clicks

The second fresh recording did not expose an explicit `minimap_click`, but it
did contain a strong navigation-support menu selection:

- Event: `97`
- Option/target: `Walk here` / `Large door`
- Movement after click: about `10.296` tiles
- Endpoint: `woodcutting_area`

That evidence is now treated as navigation-support evidence for this route.
Harmless minimap/navigation clicks should attach to nearby walk/movement
evidence or remain review evidence. They are not required template segments by
default.

## UI And Context

The UI can now run `Register Latest As Variant` from the Route / Traversal
workflow. Compact summaries expose the route-template status reason, matched
variant name, navigation-support substitution count, and allowed extra segment
count.

The context service exposes the same compact fields so callers can distinguish
a base pass, registered variant pass, valid unregistered variant warning, and
true route mismatch without reading raw steps.
