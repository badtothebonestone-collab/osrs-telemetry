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
menu samples, 32 click samples, and 64 camera-input samples per poll. The
default duration is 60 seconds and the default pointer rate is at most 20 Hz.
The recorder renews a fixed two-second camera-capture lease on each poll, so
the poll interval is limited to one second. `Ctrl+C` ends and finalizes the
artifact, and normal or error cleanup explicitly releases that lease. If the
release request fails, the lease still expires on its fixed bound and a
started artifact records the cleanup gap. Use `--no-screenshots` when images
are unnecessary.

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
The recorder asks it for the coherent observation facts plus additive, bounded
evidence views:

- `client_tick_tail`: globally sequenced pointer and camera-pose samples;
- the same hot envelope's `cameraInputTail`, `postMenuSortTail`, and
  `clickedTail`: camera-control transitions, hover menus, and semantic
  `MenuOptionClicked` samples;
- `actor_census`: NPC-only identity, actions, world/scene/local position, and
  distance rows;
- `collision_window`: bounded scene/world cells and RuneLite collision flags.

Every dynamic world payload must match the observation's source tick, session,
process, and geometry frame. Hot samples must match the same session/process
and carry the endpoint's monotonic `eventSequence` and correct lane. A sequence
reset, client identity change, or loaded-scene loss ends capture rather than
joining unrelated evidence.

The endpoint can briefly reject additive world-model or interaction-hot
capabilities when a game tick, geometry publication, or plane transition crosses
the read-only query handoff. Recording still begins only from a fully loaded,
fully bound response; startup retries only an exact known handoff for at most five
monotonic seconds. After recording starts, the exact world-model handoff, exact
interaction-hot handoff, or their exact combined form is reported as a coverage
gap without ending the already duration/event-bounded capture. The independently
bound hot-event tail remains available for camera, pointer, menu, and click
evidence. During this handoff the rejected world-model payloads are absent, while
the endpoint deliberately retains the stale `interaction_hot` snapshot as
diagnostic evidence. That stale snapshot is accepted only when it has the
supported schema, remains bound to the same session/PID, exactly matches the
endpoint's root `clientTickHot` copy, and accompanies the independently validated
`client_tick_tail`; it is never used as current hover or activation authority.
Any additional or partial warning/capability shape, source-tick
regression, hot sequence reset, session/PID change, stale or incoherent source,
or core loaded-scene loss remains terminal. If a complete world-model frame never
returns, the finalized artifact honestly retains the gap and may contain only
partial route or outcome evidence.

Live recordings persist `requestedDurationSeconds` in both the manifest and the
start event. The trusted inspector cross-checks those values, and the GUI reports
`duration_elapsed` separately from an operator stop or an unexpected early stop.

Scene-object response and acquisition caps, plus NPC, collision-cell, and
hover-menu totals/cap flags, are preserved. If a bounded request truncates a
crowded scene, the inspector
reports a coverage gap rather than presenting the subset as complete.

The recorder stores player world/scene/local location, plane, inventory,
relevant bank/dialogue widgets, nearby objects and NPCs, collision cells, menu
semantics, canvas-relative pointer samples, camera pose, annotations, and
before/after observations around semantic clicks. Pointer paths are downsampled
to 20 Hz. Screenshots are bounded inside the verified RuneLite canvas and are
suppressed while the bank-PIN surface is open.

Camera-input capture is deliberately narrow and disabled by default. A
positive demonstration request activates only a fixed two-second lease; each
poll renews it, a new lease clears the old camera lane, and finalization sends
an explicit disable request. While leased, it observes only candidate W/A/S/D
or arrow camera controls and middle-button press/drag/release events from the
exact focused RuneLite canvas while a loaded player scene is present. It fails
closed when a text field, input mode, or bank-PIN surface is active. It does
not read typed characters, modifiers, other keys, left/right buttons, wheel
events, clipboard data, desktop-global coordinates, or OS-global input hooks,
and it never consumes or changes an event. A RuneLite key remap can make
physical WASD appear as an arrow control, so inspection reports only the
observed supported `keyboard` method rather than guessing the physical key or
claiming that the input changed the camera.

The inspector treats observed camera movement and action association as two
separate facts. `keyboard`, `middle_drag`, and `mixed` describe only the input
method. A completed gesture with an observed pose change may be linked to the
next explicitly non-consumed exact object activation or exact/high-confidence
`Walk here` activation inside the bounded lookback. Exact object review allows
a four-second lookback while Walk review retains the tighter 2.5-second bound;
an intervening semantic click prevents either from claiming an older gesture.
Overlapping supported controls remain one episode until their paired terminal
events arrive, even when a held key is quiet beyond the ordinary join gap.
High-confidence Walk links
bind the gesture to the action event without pretending that one uncertain
world-tile derivation became exact. Completed movement with no such action is
reported as exploratory/unassociated; cancellation and no-pose-effect cases
remain explicit. None of these labels claims to know the operator's private
purpose. A camera episode that occurs after a context menu is already open is
temporal action association, not automatic evidence that the camera acquired
that object. Camera-tuning review therefore distinguishes pre-menu acquisition
from menu-open/post-acquisition movement when the ordering is observable. The
current artifacts do not retain the target projection at the start of every
episode, so they can prove final framing and net pose change but not an exact
before/after projection gain.

Manual Walk targets and observed player positions are also separate evidence
layers. A manual target retains selected-scene, menu-parameter, local-destination,
and resolved-world representations independently, with the RuneLite selected
scene tile serving only as the review marker when present. The chosen review
source and coordinate space are reported separately from the raw resolved-target
source. Player-sample source tick and age are retained; a requested distance is
claimed from a same-source-tick sample or explicitly labeled as a one-tick
estimate, while older samples remain explicit diagnostics. Route distance
review restarts from the accepted player
sample after a plane transition and otherwise uses same-plane target-to-target
spacing so a stale player sample cannot inflate later movements. `routePoints` remains
the sampled player-world path and is never relabeled as a clicked tile. Quick
changed-target follow-ups can be marked as possible supersession/correction,
while same-target follow-ups can be marked as retry/reaffirmation; neither is a
proven mistake without exact outcome evidence. `EngineApplication` may compare
those review markers, plane by plane, with the current immutable definition for
GUI display. That comparison is ephemeral, version-labeled, and never written
back into the verified artifact or task definition.

Tile-object identity is distinct from activation geometry. A direct canvas
activation is an object aim sample only when the authoritative object shape
contains the activation point. A right-click menu selection may instead prove
the exact object from a fresh matching menu tuple plus the object's RuneLite
scene footprint. Its final pointer is a context-menu-row point, not a point
inside the object's clickbox, so it can support the intended Staircase/Bank
interaction while remaining excluded from object aim-point samples. Unverified
activation surfaces likewise never become aim evidence.

Timing is derived from monotonic clocks. Per-click profiles distinguish the
maximum completed control hold, maximum drag path, entire input-episode span,
last-camera-input-to-click delay, actual pointer-movement/settle duration, and
the age of the latest exactly matching hover observation. The compatibility
field `hoverToClickMillis` is therefore freshness, not dwell. Context-menu-row
activations additionally expose `contextMenuOpenToClickMillis`, a conservative
lower bound from one contiguous run of matching `menuOpen=true` evidence. It is
not raw right-button-down timing, which remains intentionally unavailable.
Hover/menu timing is accepted only when option, target, type, identifier, and
both menu parameters match the clicked entry. These are
review-only reference samples for comparing engine/Arduino plans; they are not
Arduino acknowledgements, replay instructions, or permission to change engine
configuration automatically.

Camera, timing, and manual-route derivation changes are manifest-versioned.
Finalized older artifacts keep their byte-exact summary and timeline contract;
the application may show corrected ephemeral review fields without rewriting or
rehashing those artifacts.

## Artifact contract

Each finalized artifact is self-contained:

- `events.jsonl`: append-only, locally and UTC timestamped event envelopes with
  recorder and endpoint sequence provenance;
- `manifest.json`: Git commit/dirty fingerprint, Python/Pillow/pyserial/RuneLite
  versions, wire schemas, request controls, session/PID/frame provenance,
  evidence coverage, annotations, and explicit read-only/no-replay rules;
- `summary.json`: observed player-path points, separately identified manual Walk
  targets for recordings that declare the additive semantics, interactions,
  selected menu options, modeled state changes, gaps, ambiguities, semantic
  sentences, camera-intent episodes, reference timing profiles, and review-only
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
