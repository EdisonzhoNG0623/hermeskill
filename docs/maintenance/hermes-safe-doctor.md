---
title: "Hermeskill Production Health Doctor"
type: maintenance
scope: production-diagnostics
owner: edzzzz
status: active
---

# `hermes-safe-doctor`

`hermes-safe-doctor` is the production **read-only diagnostic entry point** for the Hermeskill ↔ Hermes integration.

```bash
hermes-safe-doctor
```

It runs the established checks in a fixed order, prints one unified summary, and records a readable Markdown snapshot in:

```text
~/.hermes/doctor-history/YYYY-MM-DD-HHMM.md
```

The history record is the tool's sole intentional write. It is diagnostic evidence, not a runtime or configuration change.

## When to Run It

Run `hermes-safe-doctor` when you need a production-state snapshot, including:

- Before and after an approved Hermes update.
- After investigating a gateway, plugin-loading, or editable-install concern.
- During routine operational review.
- Before escalating a suspected Hermeskill integration problem.
- When comparing current state with a prior `doctor-history` record.

It is safe to run during normal production operation because it does not restart, update, recover, or reconfigure anything.

## Fixed Diagnostic Flow

| Step | Existing source / check | Recorded evidence |
|---|---|---|
| 1 | `show-production-baseline.sh` | Stable tag/commit, current commit/branch, ahead/behind, Git status, editable install, Hermes/Hermeskill versions, gateway PID and uptime |
| 2 | `check-hermes-update-compatibility.sh` | Its complete output and original exit code; no compatibility logic is duplicated |
| 3 | `daily-health-check.sh` when present | Complete output and exit code; otherwise `SKIP` |
| 4 | `systemctl --user show hermes-gateway.service` | Active state, PID, `ActiveEnterTimestamp` |
| 5 | `systemctl show hermeskill-control-plane.service` when installed | Active state, PID, calculated uptime; otherwise `SKIP` |

The final summary reports Gateway, Hermeskill Plugin, Editable Install, Compatibility, Policy, Git, Control Plane, Update History, and Overall status.

## Difference From Related Tools

| Tool | Purpose | May modify production state? |
|---|---|---|
| `hermes-safe-doctor` | On-demand, unified production diagnosis plus Markdown evidence history | **No.** Only writes its diagnostic history record. |
| `hermes-safe-update` | Approved Hermes update orchestration: official update, compatibility validation, exit-10-only recovery, gateway restart, update history | **Yes.** It is the only approved production update path. |
| `check-hermes-update-compatibility.sh` | The authoritative Hermeskill ↔ Hermes compatibility contract | **No.** It emits the compatibility result consumed by doctor, daily health, and safe update. |

`hermes-safe-doctor` does **not** replace either of the other two tools. It calls the existing compatibility script rather than reimplementing its checks.

## Exit Codes

| Code | Meaning |
|---:|---|
| `0` | All required production checks are healthy. Optional control plane may be `SKIP` when not installed. |
| `10` | Compatibility reports lost/incorrect editable installation. No recovery is attempted. |
| `20` | Compatibility reports Hermeskill plugin load failure. |
| `30` | Compatibility reports plugin lifecycle/API incompatibility. |
| `40` | Compatibility reports missing Hermes production Python or inactive gateway; doctor also uses it if its direct gateway status check fails after compatibility passed. |
| `50` | Compatibility reports `policy != permissive`. |
| `60` | Compatibility reports unexpected Hermeskill Git/stable tag/core commit state. |
| `70` | Doctor diagnostic infrastructure failure, for example a missing required baseline or compatibility script. |
| `71` | Installed control-plane service is not healthy while required Hermeskill/Gateway checks otherwise pass. |
| `72` | Non-critical operational evidence check failed: daily-health result disagreed/failed, or update-history directory is unavailable. |
| `73` | Doctor could not persist its requested Markdown history record. |

For compatibility failures, doctor preserves the existing compatibility exit code exactly. A nonzero doctor exit code is **diagnostic only** and never triggers recovery.

## Never List

`hermes-safe-doctor` will never:

- Run `hermes update` or `hermes-safe-update`.
- Install, uninstall, rebuild, or recover an editable package.
- Restart, stop, start, reload, enable, disable, or mask a systemd service.
- Modify Hermes Agent, Gateway, Hermeskill runtime behavior, lifecycle, capability, policy, or configuration.
- Change any existing upgrade/recovery flow.
- Call `recover-hermeskill-integration.sh`.
- Reset, checkout, clean, stash, commit, tag, push, or otherwise mutate Git.
- Automatically remediate a failed check.
- Modify notification, Telegram, WeChat, Feishu, or other platform configuration.

## History Records

Each run writes a self-contained report under `~/.hermes/doctor-history/` with the local timestamp, exit code, summary, baseline, compatibility output, daily-health output, gateway status, and control-plane status.

Use these records as evidence when diagnosing changes over time. They are intentionally separate from `~/.hermes/update-history/`, which belongs only to the approved update flow.
