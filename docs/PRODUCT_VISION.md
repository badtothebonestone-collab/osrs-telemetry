# Product Vision

## Product

This repository builds a small, modular, OSRS-specific automation engine. It
turns authoritative RuneLite observations into bounded, verified actions sent
through one Arduino HID pathway.

The first and only implemented regression baseline is:

> Lumbridge west ordinary Trees -> full log inventory -> Lumbridge Castle bank
> -> deposit -> close -> return to the Trees.

That baseline is a product foundation, not a generic agent framework.

## Intended user experience

The eventual application should let a user:

1. launch one application;
2. select an OSRS task such as Woodcutting;
3. select a supported task/site definition such as Lumbridge west ordinary
   Trees -> Lumbridge Castle bank;
4. choose validated options such as resource, supported area/bank, duration,
   target level, item/cycle goal, and safe stop conditions;
5. start, pause, inspect, and safely stop the engine;
6. see exactly what the engine observes, targets, rejects, executes, verifies,
   and blocks; and
7. record a read-only manual demonstration so a later engineering session can
   understand a difficult semantic route or interaction and propose a reviewed,
   tested definition update.

The frontend will be thin. Task logic, safety, RuneLite parsing, Arduino
control, verification, and mutable task state never belong in GUI code.

## Product boundaries

This product is not:

- a general game-agent framework;
- a generic AI planner;
- a scripting language, task DSL, or behavior-tree system;
- a knowledge fabric;
- an autonomous learned policy;
- an anti-detection, stealth, or evasion system;
- an LLM-controlled runtime; or
- a marketplace or dynamic plugin loader.

Each supported task keeps an explicit OSRS-specific FSM. Shared engine seams
exist only for observation requests, action intents, invariant safety,
execution receipts, typed outcomes, status, and diagnostics.

## Extension model

Four categories remain separate:

### Profile

User-selectable choices: task/site definition, duration or stopping goal,
permitted inventory/equipment preferences, and supported safe options. Profile
validation is centralized and profiles can only narrow behavior.

### Task/site definition

Immutable, versioned OSRS facts: object/item IDs, actions, areas, bank selector,
route anchors and transitions, arrival radii, inventory predicates, and
provenance. Begin with exactly one built-in Lumbridge definition.

### Runtime configuration

Machine/session settings: endpoint, Arduino port, polling rate, and hard
action/runtime limits.

### Engine invariants

Non-overridable rules: coherent source freshness, loaded-scene proof,
PID/session/focus binding, exact identity, canvas geometry, hover/menu proof,
bank-PIN refusal, later verification, bounded execution, and authoritative
cleanup.

Neither a profile nor a definition may weaken an engine invariant.

## Truth and control

RuneLite API facts remain authoritative for:

- session identity, game tick, and client tick;
- player position, plane, inventory, and equipment;
- object/NPC identity and actions;
- menus, widgets, interfaces, skills, and combat values.

Vision is a future optional evidence source. It may identify visual occlusion,
detect prompts unavailable through the API, veto an unsafe image condition, or
propose a point inside an API-confirmed clickbox. It may not overwrite
authoritative RuneLite identity, state, session, tick, inventory, menu, or
widget facts. No YOLO/model dependency is part of the active mission.

All automated input, including saved-session login assistance, now goes through
one `InputCoordinator`. The Arduino transport is private and there is no
software-input gameplay fallback. Exact post-move hover/menu revalidation,
later outcome verification, and authoritative cleanup remain mandatory.

## Diagnostics, application facade, and operator GUI

The runtime publishes one immutable `EngineFrame`. It includes
task/state, definition/profile, progress, selected and rejected targets,
ordered safety checks, pending and last verification, last execution receipt,
cleanup status, and blockers.

The implemented `EngineApplication` facade exposes the exact one-task catalog,
profile schema/validation, tokenized start/pause/resume/safe-stop lifecycle,
runtime-owned statistics/blockers, exact EngineFrame, and mutually exclusive
demonstration begin/end. Recorders, overlays, CLIs, and GUI surfaces
consume those contracts.
They are read-only observers except for explicit high-level lifecycle commands.
They never select targets, recalculate safety, own task state, or authorize
input. The engine continues normally when diagnostics are disabled.

## Demonstration evidence

Manual demonstration capture is developer-facing and read-only. It synchronizes
RuneLite semantics with bounded pointer/event evidence and produces portable,
hashed artifacts. It never injects input, blindly replays coordinates, activates
generated task data, or bypasses normal safety. Suggestions require human
review, deterministic tests, and normal live proof.

## Memory and restart rules

Keep four memory categories separate:

- **Static knowledge:** immutable version-controlled task/site definitions and
  provenance.
- **Current task state:** owned only by the active task FSM.
- **Historical run data:** append-only metrics, receipts, outcomes, durations,
  and blockers.
- **Demonstration evidence:** append-only observations that may suggest changes
  but cannot authorize behavior.

Never restore pending verification, old coordinates/clickboxes, source ticks,
menu samples, session-bound targets, or armed input state after restart. Reopen
with fresh observation and explicit reconciliation.

## LLM boundary

An LLM may later read immutable definitions, demonstration artifacts, run
history, and diagnostics to help engineers propose code or fixture changes. It
must never directly emit executable input, mutate an active profile, or bypass
the task FSM, safety gate, input coordinator, or verifier.

## Current success criterion

The engine is successful when its one baseline remains reproducible through a
committed replay and bounded live evidence while the internals gain coherent
sensor truth, a minimal task seam, a validated definition/profile, one Arduino
owner, one diagnostic frame, passive inspection tools, and thin lifecycle
contracts—without broadening into a generic control system.

The first full operator GUI now consumes those same contracts without adding
GUI-owned task, safety, input, or verification logic. The CLI remains a bounded
diagnostic surface rather than a second control system.
