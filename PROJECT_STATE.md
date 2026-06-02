# Project State

## Current Loop

The live loop is coordinate-first and query-first. It should choose one meaningful action, run it through the HumanInputController -> ArduinoHIDBackend path, then wait for tick/state proof before continuing. Navigation, traversal, interaction, and banking remain separate decision layers.

## Navigation Trace

Navigation tracing now exists in the working tree. It records compact `navigation_decision_trace.v1` evidence for route decisions and includes reason strings for wait, click, advance, recover, and fail outcomes.

## Known Pain Points

- Route/pathing can still misread unfamiliar locations or choose recovery movement that is not aligned with the intended bank/resource route.
- Service-view and camera recovery still need bounded tuning from live daemon evidence.
- Stale endpoint or daemon truth can block readiness and must be diagnosed through live queries before gameplay actions.

## Stabilization Priority

Use the new navigation trace to explain the next route/pathing failure, then fix only the proven behavior and add a focused regression check. Do not add global pathfinding, randomization, or stale architecture.
