# Reverse Route Template Extraction

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_192931_Tree_area_to_Bank
```

Extracted template:

```text
C:\Users\badto\osrs-telemetry\route_templates\woodcutting_area_to_bank.route_template.json
```

## Result

- Template schema: `route_template.v1`
- Route name: `woodcutting_area_to_bank`
- Template revision: `1`
- Start area: `woodcutting_area`
- End area: `bank_area`
- Required segment count: `5`
- Optional segment count: `0`
- Review evidence notes: `1`

## Required Segments

1. `area_start`: `Start: woodcutting_area`
2. `walk_segment`: `Walk`
3. `stair_transition`: `Climb-up Staircase`, plane delta `+2`
4. `walk_segment`: `Walk`
5. `area_arrival`: `Arrive: bank_area`

## Endpoint / Task Evidence

The recording includes a strong `Deposit` target match on `Bank Deposit Box`.
That action is preserved as review/endpoint evidence, not as required traversal
progress. The route template is for traversal from the tree area to the bank
area; a future banking/deposit template can make deposit behavior required if
that becomes the intended route semantics.

## Evidence Quality

- Traversal lifecycle: `PASS`
- Route segments: `5/5` successful
- Partial route segments: `0`
- Review evidence: `1`
- Target quality: strong for `Bank Deposit Box / Deposit`
- Duplicate live Arduino clicks: none observed
- Mirror feedback/runaway: not observed

## Conclusion

The reverse route recording is valid for extracting a reusable
`woodcutting_area_to_bank` traversal template. It should not be compared against
the one-way `Bank_to_Woodcutting_area` template.
