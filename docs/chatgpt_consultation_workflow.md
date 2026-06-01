# ChatGPT Consultation Workflow

Use local project evidence first. ChatGPT consultation is a bounded escalation
path for blockers, architecture questions, safety/input decisions, and ambiguous
next steps; it is not part of routine testing or normal live recovery.

## Preference Order

1. Use local project tools first:
   `current_debug_context`, `current-blocker` / `explain_current_blocker`,
   `pipeline_health`, Knowledge Fabric, MCP/direct query tools,
   `replay_scenario`, `visual_debug_bundle`, tests, docs/source search, and the
   external OSRS knowledge cache.
2. If a real blocker or architecture/safety decision remains, use Chrome Use to
   ask ChatGPT in the already-open ChatGPT conversation.
3. If Chrome Use is unavailable or fails, use Computer Use to ask ChatGPT in the
   visible already-open ChatGPT conversation.
4. If both UI paths fail, print a `PASTE_TO_CHATGPT` block for the user to paste
   manually, then stop or pause.

## When To Ask ChatGPT

Ask only when local evidence leaves a meaningful decision unresolved:

- local query tools disagree;
- architecture or safety/input decisions need a second opinion;
- a long-running goal hits a blocker;
- the next step is genuinely ambiguous;
- user preference is needed;
- two plausible fixes need a tie-breaker.

Do not ask for routine tests, simple syntax fixes, ordinary Git commit/push,
normal loaded-scene recovery, every small action, or anything the local query
layer clearly answers.

## Chrome Use Rules

Prefer Chrome Use because ChatGPT is already open in Chrome inside the VM. Use
only the existing ChatGPT conversation/tab when available. Do not browse
unrelated sites, open random pages, paste secrets, credentials, tokens, auth
files, private data, huge logs, or full JSON dumps.

Paste one concise structured question, send it, wait for the visible answer,
read it, summarize the received answer in execution notes, and continue only if
the answer is clear. Ask at most one clarification if needed.

## Computer Use Fallback

Use Computer Use only if Chrome Use is unavailable, blocked, or unreliable. Use
only the visible already-open ChatGPT conversation. Do not click unrelated
apps/pages. Do not use Computer Use while RuneLite live input or gameplay is
running; stop live/gameplay work before switching to the ChatGPT UI. If the UI
gets weird, stop and print `PASTE_TO_CHATGPT`.

## Manual Fallback

Generate a bounded handoff block:

```powershell
python telemetry-viewer\context_service.py --handoff-summary
```

Use optional fields when the question or test results should be explicit:

```powershell
python telemetry-viewer\context_service.py --handoff-summary --handoff-question "Which fix is safer?" --handoff-tests-run "test_context_service.py PASS"
```

Machine-readable handoff JSON remains available through:

```powershell
python telemetry-viewer\context_service.py --query handoff-summary
python telemetry-viewer\context_service.py --handoff-summary-json
```

## Required Block Format

```text
PASTE_TO_CHATGPT:
Context:
What I tried:
Evidence:
Files changed:
Tests run:
Current blocker:
Specific question:
Options I’m considering:
My recommended next step:
```

The helper fills those headings with compact current context, branch/commit,
bounded git status, current blocker, and the recommended local next step. It
does not include secrets, runtime logs, screenshots, full live sessions,
live_packets, NDJSON, JSONL, or full JSON dumps.

## Resume After ChatGPT Answers

After ChatGPT answers, record a short summary in execution notes and map the
answer back to local evidence. Then continue with local tools: run the relevant
query, test, source search, or code change. Do not treat ChatGPT as authority
over fresh daemon truth, input safety gates, or repo tests.
