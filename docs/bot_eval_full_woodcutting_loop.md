# Full Woodcutting Loop Bot Evaluation

Date: 2026-06-07

## Result

Verdict: PASS

The live daemon did not answer the dry-run status probe, so this run used replay
mode against the known full-loop Record Everything fixture:

`C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked`

Replay output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260607_204642_woodcutting_loop_eval`

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --recording "C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked" --max-actions 100 --record-everything --analyze-after --json
```

## Trace Artifacts

- `bot_eval_manifest.json`
- `bot_decision_trace.jsonl`
- `bot_action_trace.jsonl`
- `bot_observation_trace.jsonl`
- `bot_postcondition_trace.jsonl`
- `bot_eval_summary.json`
- `linked_recording_folder.txt`

## Phase Results

| Phase | Expected bot decision | Actual decision | Postcondition |
| --- | --- | --- | --- |
| Woodcutting | collect | collect | PASS, animation/log gain proof |
| Inventory full | bank | bank | PASS, route-to-bank selected |
| Route to bank | bank | bank | PASS, arrived bank area |
| Banking/deposit | deposit | deposit | PASS, deposited logs |
| Deposit complete | return_to_resource | return_to_resource | PASS, route-to-trees selected |
| Route to trees | return_to_resource | return_to_resource | PASS, arrived woodcutting area |
| Interruption | collect | collect | PASS, task resumed |
| Resumed cutting | collect | collect | PASS, continued after loop |

Decision mismatches: 0

Postcondition warnings: 0

## Data Used

The replay called the script-facing data that the bot should use:

- `assess_task_run_readiness`
- `get_next_click_plan`
- `get_woodcutting_loop_lifecycle`
- `get_deposit_result`
- `get_route_monitor_status`
- `get_interruption_lifecycle`
- `get_combat_damage_summary`

The fixture proved both route legs:

- `woodcutting_area_to_bank`
- `Bank_to_Woodcutting_area`

Banking was confirmed by direct lifecycle evidence, including deposited Logs x28
and bank container delta availability.

## Click Planning

Human click planning was available but advisory only.

All eight replayed phase click plans returned WARN because replay mode did not
provide fresh live target geometry/readiness. No profile-informed live click was
executed, and no command was sent to the input layer.

This is the intended policy: lifecycle and route state can select the next
primitive, while live target/readiness/hover proof must still gate any real
click.

## Mismatches Found

No bot decision mismatch remained after the evaluator read
`task_run_readiness.data.inferredNextPrimitive` correctly.

The first local replay exposed an evaluator parsing bug that treated the
inferred primitive as `unknown`. The runner now reads the existing readiness
shape correctly and produces the expected decisions.

## Remaining Gaps

- Live daemon was unavailable for this pass, so the evaluation is replay proof,
  not a live bot run.
- Click plans remain advisory until live target geometry/readiness is present.
- The next live validation should produce the same traces while the real bot is
  operating through the guarded input path.

## Live Readiness Follow-up

A bounded no-input smoke was run after this replay:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live-smoke --duration 10 --no-input --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260607_214451_woodcutting_loop_live_smoke`

Result: FAIL readiness.

The smoke did not send input. It wrote `bot_live_readiness.json` and showed:

- `8890/status` timed out.
- `8890/health` timed out.
- `8893/health` timed out.
- Latest disk telemetry was stale.
- Route templates were present.
- `task_script_api` could still read/build advisory state.

Root cause: live daemon/status path unavailable or unresponsive. The bot loop
decision logic should not be changed until a fresh live readiness trace proves a
decision mismatch.

Follow-up fix:

- Live readiness now uses `/health` instead of the heavy `/status` diagnostic payload.
- RuneLite/plugin snapshot was started on `8893`.
- Live target processor and context service were restarted on the newest session.

PASS no-input smoke:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081231_woodcutting_loop_live_smoke`

PASS bounded live dry-run:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081421_woodcutting_loop_live_dry_run`

The first dry-run sent no input commands. It proved the live stack was up, but a
stricter loaded-scene gate correctly downgraded readiness until the game scene
was actually loaded.

After the existing loaded-scene recovery ladder advanced the saved-account Play
Now screen, the final dry-run passed:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081923_woodcutting_loop_live_dry_run`

The final dry-run sent no input commands. It proved the live stack is ready for
the next guarded evaluation phase:

- context service reachable
- plugin snapshot reachable
- telemetry fresh
- game client loaded
- loaded scene ready
- route templates loaded
- task script API readable
- advisory Tree / Chop down click plan available

## Next Task

Run the same evaluator with guarded live action/postcondition tracing only after
the dry-run trace is reviewed. Success means the trace folder contains real
action/postcondition evidence and still reports zero phase decision mismatches.
