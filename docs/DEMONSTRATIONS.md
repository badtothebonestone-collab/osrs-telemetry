# Manual Demonstration Evidence

Manual demonstration capture is a developer aid for explaining a route or
interaction that is difficult to infer from static code. It is read-only: the
operator controls RuneLite, and the recorder never opens Arduino hardware,
injects input, changes focus, or issues a game command.

## Commands

Start only after `run.cmd observe` proves a coherent loaded scene:

```powershell
.\run.cmd record-demo castle-stairs --duration-seconds 45 --annotation "showing the upper staircase"
```

Names are safe lowercase slugs. Capture is bounded to ten minutes, 50,000
events, 32 screenshots, 16 menu entries per sample, 64 client-tick samples, 32
menu samples, and 32 click samples per poll. The default duration is 60 seconds
and the default pointer rate is at most 20 Hz. `Ctrl+C` ends and finalizes the
artifact. Use `--no-screenshots` when images are unnecessary.

Artifacts are written under `demo_runs/<UTC>_<name>/`, which is intentionally
ignored by Git. Inspect a finalized artifact with:

```powershell
.\run.cmd inspect-demo .\demo_runs\20260710T170000000000Z_castle-stairs
```

The inspector returns nonzero for missing files, unsafe paths or symlinks,
oversized files, unexpected files, invalid schemas, discontinuous recorder
sequences, or size/hash tampering. It emits no candidate suggestions when
evidence is invalid.

The in-process `EngineApplication` also exposes tokenized begin/end operations
for a future GUI. Each capture receives a monotonic `capture_id`; an end request
must carry the exact current ID. Automation and demonstration capture are
mutually exclusive. Ending a capture sets the recorder's read-only stop
predicate, waits boundedly for normal finalization, then runs this same
inspector. It never kills the recorder thread or opens an input path.

## Captured evidence

The existing `POST /snapshot` endpoint remains the only telemetry endpoint.
The recorder asks it for the coherent observation facts plus three additive,
bounded evidence views:

- `client_tick_tail`: globally sequenced pointer, hover-menu, and semantic
  `MenuOptionClicked` samples;
- `actor_census`: NPC-only identity, actions, world/scene/local position, and
  distance rows;
- `collision_window`: bounded scene/world cells and RuneLite collision flags.

Every dynamic world payload must match the observation's source tick, session,
process, and geometry frame. Hot samples must match the same session/process
and carry the endpoint's monotonic `eventSequence` and correct lane. A sequence
reset, client identity change, or loaded-scene loss ends capture rather than
joining unrelated evidence.

Scene-object response and acquisition caps, plus NPC, collision-cell, and
hover-menu totals/cap flags, are preserved. If a bounded request truncates a
crowded scene, the inspector
reports a coverage gap rather than presenting the subset as complete.

The recorder stores player world/scene/local location, plane, inventory,
relevant bank/dialogue widgets, nearby objects and NPCs, collision cells, menu
semantics, canvas-relative pointer samples, annotations, and before/after
observations around semantic clicks. Pointer paths are downsampled to 20 Hz.
Screenshots are bounded inside the verified RuneLite canvas and are suppressed
while the bank-PIN surface is open.

RuneLite currently provides semantic click events, not global raw mouse-button
or keyboard transitions. The manifest declares that gap instead of pretending
those events were observed.

## Artifact contract

Each finalized artifact is self-contained:

- `events.jsonl`: append-only, locally and UTC timestamped event envelopes with
  recorder and endpoint sequence provenance;
- `manifest.json`: Git commit/dirty fingerprint, Python/Pillow/pyserial/RuneLite
  versions, wire schemas, request controls, session/PID/frame provenance,
  evidence coverage, annotations, and explicit read-only/no-replay rules;
- `summary.json`: route points, interactions, selected menu options, modeled
  state changes, gaps, ambiguities, semantic sentences, and review-only
  candidates;
- `timeline.md`: concise human-readable events and semantic outcomes;
- `screenshots/*.png`: optional bounded evidence crops;
- `hashes.json`: SHA-256 and byte size for every other artifact file.

An outcome can read, for example, `clicked Staircase 16672 with Climb-up at
plane 1, then observed plane 2`. This is evidence of one observed interaction,
not proof that the route is generally correct.

## Authority boundary

Artifacts are never raw-input programs. Inspection suggestions contain only
authoritative world anchors, known game-object IDs, NPC IDs correlated from a
menu index through an exact same-tick NPC census, actions, and observed plane
transitions.
Walk-here, player, widget, incomplete, and uncorrelated menu identifiers cannot
become entity candidates. Suggestions omit screen/canvas/mouse coordinates and
are marked `reviewRequired` and `never_automatic`.

Before any suggestion becomes a definition or fixture change, an engineer must
review its provenance, implement the smallest explicit task-specific change,
add deterministic tests, run the normal suites and golden replay, and obtain
bounded live proof through the ordinary `Observation -> SafetyGate ->
InputCoordinator -> Verifier` path. A demonstration can never bypass those
contracts or weaken an engine invariant.
