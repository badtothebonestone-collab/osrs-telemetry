# Tree Area To Bank Auto Template Analysis

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_192931_Tree_area_to_Bank
```

## Auto Selection

- Detected route: `woodcutting_area_to_bank`
- Detected start/end: `woodcutting_area -> bank_area`
- Selected template:
  `C:\Users\badto\osrs-telemetry\route_templates\woodcutting_area_to_bank.route_template.json`
- Selection reason: `route_name_match`
- Alternative template observed:
  `Bank_to_Woodcutting_area`, `bank_area -> woodcutting_area`
- Untemplated route: `false`

## Comparison Result

- Status: `PASS`
- Status reason: `PASS_BASE_TEMPLATE`
- Score: `1.0`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Weak segments: `0`
- Failed postconditions: `0`
- Direction mismatch: `false`

## Route Monitor / History

- Route monitor status: `PASS`
- Route state: `arrived`
- Current/end area: `bank_area`
- Completed/remaining segments: `5 / 0`
- Off route: `false`
- Route history status: `PASS`
- Plane changes: `1`

## Strict Wrong-Template Check

The same recording was also compared explicitly against:

```text
route_templates\Bank_to_Woodcutting_area.route_template.json
```

That strict comparison remains `FAIL` with `statusReason=FAIL_WRONG_ENDPOINT`,
but now reports:

```text
routeTemplateDirectionMismatch=true
```

The failure is therefore correctly explained as a wrong one-way template, not
as bad route data.

## Conclusion

Record Everything auto-template matching fixes the previous
`FAIL_WRONG_ENDPOINT` for this reverse route. The recording is useful route
data and now compares cleanly against the matching reverse template.
