# Rescue Audit

## Archaeology result

The selected recovery line descends from `463833aaf2b41d8c04b38169c3ed2d4feee64895`.
Its useful baseline was the RuneLite sensor, localhost snapshot endpoint, live
geometry/menu evidence, and fail-closed Arduino HID backend.

Before the rescue, automated evidence showed that Java tests and a large Python
fixture suite passed. Live evidence proved the endpoint and one bounded Arduino
click, but did not prove an autonomous tree-to-bank cycle. Route profiles were
marked `verifiedLive: false`.

## Expansion finding

Commit `619e2b8` is the practical "5.5 expansion" boundary. It added 199 files
and about 83,000 lines across runtime tooling, generated knowledge, docs, tests,
and route data. Capability did not grow proportionally: the same evidence still
stopped before a complete autonomous loop.

The expanded checkout contained overlapping planners, readiness checks, route
arbitration, task scripts, knowledge generation, recovery launchers, 78 Python
entrypoints, and 122 decision/state mutation sites. It also tracked roughly
235 MiB of runtime logs. The plugin independently collected similar state into
both tick snapshots and a world-model cache.

## What was retained

- RuneLite telemetry capture and the read-only snapshot endpoint.
- Live object/menu/widget geometry needed for safe interaction.
- The Arduino firmware, HID backend, arming, focus, movement bounds, watchdog,
  `STOP_ALL`, and `DISARM` concepts.
- Exact-target and later-observation verification behavior.
- Only the fixed Lumbridge route facts required by the supported slice.

## What was replaced or removed

- The Python Brain, Knowledge Fabric, analyzers, planners, task scripts,
  readiness variants, route arbiters, compatibility launchers, and daemons.
- Historical schemas, recovery fixtures, generated knowledge, proof logs, and
  tracked runtime output.
- Mutable telemetry preset endpoints and their config compatibility layer.
- Historical project-state documents that described retired systems.

Git history remains the archive for deleted experiments.

The expansion also contained a bounded Arduino login-prompt helper for saved
sessions. Its historical command was
`python telemetry-viewer\context_service.py --ensure-loaded-scene --arduino-port COM6`.
During this rescue it recognized `play_now`, sent four bounded clicks, and
returned `unsafe` when no loaded scene followed. It also launched an unwanted
second stock RuneLite client. Explicit `STOP_ALL` and `DISARM` acknowledgements
were proven afterward.

The recovery framework was deleted, but its useful behavior was rebuilt as the
small canonical `run.cmd login COMx` path. The new helper never launches another
client, uses only Arduino HID, binds the telemetry-owning window, revalidates
the visual target after moving, and verifies a telemetry transition. It retains
the two prompts backed by genuine templates (saved-account Play Now and Click
here to play) plus the narrow historical idle-disconnect OK geometry. Continue,
credential, MFA, unknown, and ambiguous surfaces remain unsupported.

## New live baseline proof

Two clean snapshot runs produced 12 advancing responses each from a loaded
scene. The first exposed duplicate object-census rows; deduplication was fixed
and the second returned 64 unique keys in 64 rows. After a later stale-client
regression, the verified RuneLite process was relaunched and returned a fresh
loaded scene again.

The rescued CLI parsed the live scene and dry-ran an exact ordinary Tree action
with `Chop down`, object ID `1276`, live screen geometry, and no input sent.

A later live pass exposed an important RuneLite edge case: `LOGGED_IN` and a
local player may be published while the Welcome to Gielinor panel still covers
the scene. The plugin now publishes `welcomeScreenVisible` and the fail-closed
`scenePlayable` bit; Python requires that bit before `loadedScene` can be true.
The login helper also requires two prompt-free observations with an advancing
tick before success.

The final saved-session recovery was proven live through three recognized
surfaces in sequence: idle-disconnect OK, Play Now, and Click here to play.
Every Arduino click reported acknowledged `STOP_ALL` and `DISARM`; a separate
firmware status check reported disarmed, zero keys down, zero mouse buttons
down, zero acknowledgement failures, and zero timeouts. The restored snapshot
was fresh at `(3192, 3244, 0)` with 12 ordinary logs, an explicitly known closed
bank, advancing ticks, and no warnings or missing capabilities. A dry run again
selected exact Tree `1276`; the safety gate accepted its live geometry. A
synthetic full-log inventory selected the first fixed bank-route tile, obtained
an actionable live tile projection, and passed the same pre-move safety gate.
No gameplay action was sent during those pre-cycle proofs.

## Vertical-slice completion proof

On 2026-07-10 the ordinary-log cycle was physically completed: the inventory
reached 28 logs, the character traversed the fixed route and both upward stair
transitions, opened the exact bank booth, deposited 28 to 0 logs, closed the
bank with verified Escape support, traversed the return route, and reached the
tree area with task state `COMPLETE`. Every connected trace records acknowledged
`STOP_ALL` and `DISARM` cleanup.

The evidence must be interpreted precisely. It was accumulated across bounded
continuation runs while route and interaction failures were patched. The
terminal `_run_proofs/vertical_slice/20260710_170849/trace.jsonl` begins at the
last return waypoint; it is not an uninterrupted autonomous-cycle trace. The
ignored trace corpus also omits the complete raw `Observation`, menu, geometry,
and safety inputs needed for a byte-for-byte replay.

Phase 0 therefore preserves two complementary artifacts:

- the ignored live component traces, with the key evidence hashes recorded in
  `tests/fixtures/golden_lumbridge_cycle.json`; and
- a committed sanitized semantic replay, run with `run.cmd replay`, that drives
  the final task code through 28 log gains, both fixed routes, typed bank
  outcomes, one completed cycle, and terminal `COMPLETE`.

This remains the historical regression baseline. A separate 2026-07-11
current-checkpoint run later completed the default profile uninterrupted with
safe terminal cleanup; `docs/ENGINE_STATUS.md` records that stronger evidence.
The classification of the original component corpus above remains unchanged.
