---
title: "Hermeskill Safe Status"
type: maintenance
scope: production-status
owner: edzzzz
status: active
---

# `hermes-safe-status`

`hermes-safe-status` is the compact, read-only production status snapshot for the Hermeskill ↔ Hermes integration.

```bash
hermes-safe-status
```

It is intended for routine checks and normally completes in under one second. For a full diagnostic and a persisted evidence record, use [`hermes-safe-doctor`](hermes-safe-doctor.md).

## Output

The command reports only inexpensive, local state:

```text
Hermes Gateway : PASS
Hermeskill     : PASS
Editable       : PASS
Policy         : permissive
Branch         : main
Commit         : <short HEAD>
Stable Tag     : hermeskill-session-reset-fixed-20260715
Working Tree   : clean
Gateway PID    : <pid>
Gateway Uptime : <duration>
Doctor         : <latest doctor-history mtime>
Update         : <latest update-history mtime>
Overall        : HEALTHY
```

`Overall` is `HEALTHY` only when all of these conditions hold:

| Fast check | Required state |
|---|---|
| Gateway | `hermes-gateway.service` is active with a positive MainPID |
| Plugin | `import hermeskill_hermes` succeeds in the production Hermes Python |
| Editable install | Hermeskill PEP 660 `direct_url.json` identifies an editable project beneath this repository |
| Policy | `~/.hermeskill/config.toml` selects `permissive` |
| Git | Working tree is clean, including untracked files |
| Stable tag | `hermeskill-session-reset-fixed-20260715` exists locally |

Any failed or unavailable required fast check yields `Overall : WARNING` and exit code `1`. `HEALTHY` exits `0`.

## Deliberate Boundaries

`hermes-safe-status` is a snapshot, not a diagnostic or repair workflow. It does **not** run:

- Compatibility checks, daily health, `hermes-safe-doctor`, or pytest.
- Recovery, update, install, restart, reload, enable, disable, or any lifecycle action.
- `git fetch`, network requests, or Control Plane checks.
- Repository-wide scans.

It creates no files and does not write doctor/update history.

## Relationship to Existing Tools

| Tool | Use it when | Writes or changes production state? |
|---|---|---|
| `hermes-safe-status` | A rapid, everyday state snapshot is sufficient | **No** |
| `hermes-safe-doctor` | A full compatibility and operational diagnosis with evidence history is required | Only its diagnostic history record |
| `hermes-safe-update` | An approved Hermes update is required | Yes; it is the approved update path |

`hermes-safe-status` does not call, replace, or modify any of these tools.
