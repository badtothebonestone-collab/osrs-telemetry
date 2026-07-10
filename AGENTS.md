# Engineering Rules

## Product boundary

The only supported product is the Lumbridge west ordinary-tree to Lumbridge
Castle bank vertical slice described in `docs/RESCUE_CONTRACT.md`.

Keep one source of truth for each layer:

- RuneLite snapshot endpoint
- `Observation`
- `WoodcutBankTask`
- `SafetyGate`
- `ArduinoActionInterface`
- `Verifier`

Do not add planners, task languages, knowledge systems, compatibility launchers,
fallback input backends, recovery frameworks, or configuration frameworks.

## Safety

- Live input is Arduino-only.
- Never type credentials or MFA.
- `run.cmd login COMx` may click only the retained idle-disconnect OK,
  saved-session Play Now, and Click here to play surfaces; all other
  login/recovery surfaces fail closed.
- Require a fresh loaded scene, exact target identity, verified screen geometry,
  exact post-move hover, and post-action verification.
- Always issue and confirm `STOP_ALL` and `DISARM` after a connected attempt.
- Fail closed; a missing proof is not a reason to add a fallback.
- Keep dry-run behavior free of input and hardware connections.

## Development

- Use `run.cmd` as the public entrypoint.
- Everything downstream of the plugin consumes `Observation`; do not read
  plugin caches or raw response dictionaries elsewhere.
- Keep the task explicit and specific to the supported route.
- Prefer deletion and direct code over new abstraction.
- Preserve the Arduino firmware/backend unless hardware evidence proves a
  change is necessary.
- Add focused tests for behavior, not for retired architecture.

## Validation

With RuneLite closed:

```powershell
.\run.cmd test
```

For live read-only proof:

```powershell
.\run.cmd plugin
.\run.cmd observe
.\run.cmd task
.\run.cmd login COM6
```

`task` is dry-run. Both login assistance and gameplay execution are explicit,
Arduino-only modes; gameplay uses `run.cmd execute COM6`.
