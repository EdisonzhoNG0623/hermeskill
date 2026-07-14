---
title: "Hermes Update Maintenance"
type: maintenance
scope: hermes-updates
owner: edzzzz
status: active
---

# Hermes Update Maintenance

## Why `hermes-safe-update`, not `hermes update`

Hermeskill is integrated into Hermes Agent via the **editable install**
mechanism (`pip install -e`) plus the `hermes_agent.plugins` entry-point
group. When you run the bare `hermes update` command, three things can
silently break:

1. **Editable install gets wiped.** The official update flow may rebuild
   the production venv, which removes the `.pth` files that point at
   `/opt/ai/projects/hermes-upgrades/hermeskill/`. After the rebuild,
   `import hermeskill` either fails outright or resolves to a stale
   `site-packages/` copy.

2. **Plugin entry-point is unregistered.** If `importlib.metadata`
   no longer sees `hermeskill-hermes`'s `hermes_agent.plugins` group
   (e.g. the new Hermes version changed how it scans plugins), the
   `PluginManager` will silently skip hermeskill and only `enable` it
   if explicitly listed in `~/.hermes/config.yaml`.

3. **Hook names drift.** Hermes may rename lifecycle hooks between
   versions. Hermeskill's `on_session_reset`, `on_session_finalize`,
   etc. won't be wired up if Hermes expects `session_reset_v2` or
   similar. The plugin still "loads" but every hook callback is dead.

`hermes-safe-update` wraps the official `hermes update --yes` with three
compensations that the bare command does NOT do:

- **Conditional auto-recovery.** If the post-update compatibility
  check returns exit code **10** (editable install lost), `hermes-safe-update`
  automatically re-runs `pip install -e` on both hermeskill packages,
  then re-checks. The bare `hermes update` does nothing for this case.
- **Log-correlated current-session check.** Compatibility is verified
  against the gateway's `ActiveEnterTimestamp` log slice, so a previous
  session's `apoptosis` or `tool_scope_violation` is not misclassified
  as a current-session failure.
- **Pre-fail STOP.** If `hermes update --yes` itself returns non-zero,
  `hermes-safe-update` STOPS immediately — no recovery, no restart,
  no misclassification as code 10. The full update output is preserved
  in `~/.hermeskill/update-reports/` for human diagnosis.

## Upgrade Flow

```
Update
   ↓
Compatibility Check
   ↓
Healthy?
├── Yes → Restart Gateway → Verify → Write History → PASS
└── No
      │
      ▼
   Auto Recovery (only if exit code is exactly 10)
      │
      ▼
   Re-check
      │
      ▼
   PASS / STOP
```

| Step | Actor | Exit codes |
|------|-------|------------|
| Update | `hermes update --yes` (via wrapper) | 0=clean, ≠0=STOP |
| Compatibility Check | `check-hermes-update-compatibility.sh` | 0,10,20,30,40,50,60 |
| Auto Recovery | `recover-hermeskill-integration.sh` | runs ONLY if check == 10 |
| Restart Gateway | `systemctl --user restart hermes-gateway` | 0=ok, ≠0=STOP |
| Verify | re-run compatibility check | must == 0 |
| Write History | `write-update-history.sh` | 0=ok, ≠0=warn-continues |

## Exit Code Reference

| Code | Meaning | Auto-recovery? |
|------|---------|----------------|
| **0**  | All checks passed. | NO |
| **10** | Editable install lost, or import path wrong, or `inspect.getfile` outside repo, or `pip show` is non-editable, or gateway loaded hermeskill from `site-packages`. | **YES — only code that triggers recovery.** |
| **20** | Plugin entry exists in PluginManager but failed to load (e.g. `error != None`), OR current-session log patterns indicate load-time failures. | NO — manual investigation. |
| **30** | Hermeskill hook names incompatible (missing required hook), OR current-session lifecycle API errors, OR PluginManager doesn't have hermeskill registered at all. | NO — hermeskill code may need an update. |
| **40** | Hermes production python missing OR gateway service not active. | NO — environment must be fixed first. |
| **50** | Hermeskill `policy != permissive`. | NO — auto-policy-switch is **forbidden**. |
| **60** | Hermeskill git repository / stable commit / stable tag is in an unexpected state. | NO — local code may have been altered. |

**Precedence**: when multiple codes fire simultaneously, the **highest**
code wins (60 > 50 > 40 > 30 > 20 > 10). The resolver is in
`check-hermes-update-compatibility.sh::final_code()`.

## Recovery Boundaries

`recover-hermeskill-integration.sh` will ONLY run if:

1. The check exit code is **exactly 10** (strict equality, not `>=`).
2. The hermeskill repository exists at `/opt/ai/projects/hermes-upgrades/hermeskill`.
3. Both package directories exist:
   - `packages/hermeskill-sdk/`
   - `packages/hermeskill-hermes/`

When it runs, it:

- Logs the **read-only git state** (HEAD, status porcelain, branch,
  tag). It NEVER resets, checkouts, cleans, or modifies the working tree.
- Tolerates a dirty working tree (uncommitted changes are reported,
  not refused). Editable installs work fine with uncommitted content.
- Runs `pip install -e` on both packages.
- Restarts the gateway (only via the user's wrapper — never from inside
  a gateway-spawned subprocess).
- Re-runs the check. If it doesn't return 0, recovery is refused
  (no retry loop, no escalation).

## What this stack will NEVER do

- ❌ `git reset`, `git checkout`, `git clean`, `git stash drop`
- ❌ Overwrite local hermeskill source code
- ❌ Roll back Hermes to a previous version
- ❌ Modify Hermeskill's policy setting
- ❌ Switch `~/.hermeskill/config.toml` from `permissive` to anything else
- ❌ Infinite retries
- ❌ Auto-recover on `code != 10`
- ❌ Touch Telegram / Feishu / WeChat configuration
- ❌ Open a Hermes session just to run `hermes update`

## Related Scripts

| Path | Role | Read-only? |
|------|------|------------|
| `scripts/check-hermes-update-compatibility.sh` | Compatibility check (used by wrapper, daily health, recover). | YES |
| `scripts/recover-hermeskill-integration.sh` | Conditional recovery (exit-10 only). | NO — reinstalls editable + restarts gateway. |
| `scripts/daily-health-check.sh` | Daily read-only check; never auto-recovers, even on code 10. | YES |
| `scripts/write-update-history.sh` | One-shot Markdown writer invoked by wrapper on success. | YES |
| `scripts/show-production-baseline.sh` | One-screen baseline snapshot for humans. | YES |
| `~/.local/bin/hermes-safe-update` | User-facing wrapper around `hermes update --yes` + check + (conditional) recover + history. | NO — orchestrates everything. |

## Artifacts

| Path | Purpose | Lifecycle |
|------|---------|-----------|
| `~/.hermes/update-history/YYYY-MM-DD-HHMM.md` | Per-update Markdown record. | Permanent. |
| `~/.hermeskill/update-reports/YYYYMMDDTHHMMSSZ-<label>.log` | Per-run redacted log (every check, recover, safe-update). | Permanent. |
| `~/.hermeskill/config.toml` | Hermeskill policy + settings. | Read by check; never modified by any script. |