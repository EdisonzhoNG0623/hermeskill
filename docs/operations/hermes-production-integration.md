---
title: Hermeskill — Hermes Production Integration & Recovery
type: operations
status: stable
created: 2026-07-15
owner: hermeskill-builder
scope: hermeskill ↔ Hermes Agent (gateway on `/opt/ai`)
tags:
  - hermeskill
  - hermes
  - editable-install
  - session-reset
  - recovery
  - operations
---

# Hermeskill — Hermes Production Integration & Recovery

> Stable record for how Hermeskill currently integrates with the production
> Hermes Agent, and how to verify / recover that integration after any change
> (Hermes update, venv rebuild, manual pip operation, gateway restart).

---

## 1. Current Architecture & Installation

### Repository

- Local path: `/opt/ai/projects/hermes-upgrades/hermeskill`
- Workspace: `uv` workspace, three packages under `packages/`:
  - `hermeskill-sdk`  → core (WatcherState, symptom checks, control-plane client)
  - `hermeskill-hermes` → Hermes plugin (entry point `hermeskill_hermes.register`)
  - `hermeskill-control-plane` → control plane service (separate process)

### Installation method (CRITICAL — do not change blindly)

Both `hermeskill-sdk` and `hermeskill-hermes` are installed as **editable**
installs into the production Hermes venv:

```text
/home/ai/.hermes/hermes-agent/venv/bin/python
```

```text
hermeskill        → /opt/ai/projects/hermes-upgrades/hermeskill/packages/hermeskill-sdk   (editable)
hermeskill-hermes → /opt/ai/projects/hermes-upgrades/hermeskill/packages/hermeskill-hermes (editable)
```

The plugin registers itself via the `hermes_agent.plugins` entry-point group
declared in `packages/hermeskill-hermes/pyproject.toml`.

### Config

- File: `~/.hermeskill/config.toml`
- Currently: `policy = "permissive"` (intentional — see §3)
- Base URL: `http://127.0.0.1:8000` (control plane)

### Gateway

- Unit: `hermes-gateway.service` (user unit)
- Must remain `active (running)`.

---

## 2. Current Stable Commits, Tags & Branch

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD commit | `79cf830` |
| Core fix commit | `07139f8` — `fix(hermeskill): preserve supervision across session boundaries` |
| Merge commit (HEAD) | `79cf830` — `merge: preserve Hermeskill supervision across session boundaries` |
| Tag | `hermeskill-session-reset-fixed-20260715` |
| Remote | `https://github.com/EdisonzhoNG0623/hermeskill.git` |
| Remote branch tracking | `origin/main` |

To verify at any time:

```bash
cd /opt/ai/projects/hermes-upgrades/hermeskill
git rev-parse HEAD
git rev-parse --abbrev-ref HEAD
git status --short
git tag --list hermeskill-session-reset-fixed-20260715
```

---

## 3. What the 07139f8 Fix Changes — And Why It Matters

### Behaviour addressed

Before the fix, Hermeskill supervision was tied to the *current* session loop.
That produced three classes of failure:

1. **`session reset received without an active session loop`** — emitted when
   Hermes issued `/new` or `/reset` while Hermeskill's session-scoped
   resources (WatcherState, async loop closures) had already been torn down.
2. **Process-level resources closed on every session end** — HTTP client to
   the control plane, the supervision worker, and the asyncio loop were
   being shut down on routine session boundaries, forcing full re-init on the
   next `/new`. That produced visible `client has been closed` and
   `failed to rotate watcher state` errors.
3. **`coding-default` falsely triggering apoptosis on `terminal`** — because
   the default policy enumerates an `allowlist` and Hermeskill's normalizer
   classified `terminal` as out-of-scope during a coding-default run,
   it logged a `tool_scope_violation` and aborted the session.

### What the fix does

| Concern | Before | After |
|---|---|---|
| Config path resolution | Relative to CWD | Resolved relative to user home / explicit absolute path |
| `/new`, `/reset`, session finalize | Tore down supervision | Rotates `WatcherState` per session; keeps process-level worker alive |
| HTTP client & event loop | Closed on session end | Closed only on actual process shutdown |
| WatcherState identity | Stable across resets → collisions | Per-session instance, deterministic rotation log |
| `coding-default` policy | Active, flagged `terminal` as out-of-scope | Switched to `permissive` so `terminal` does not trigger `tool_scope_violation` |

### Why `permissive` is the current policy

`coding-default` is not safe with `terminal` allowed — Hermeskill's
allowlist was originally too narrow, and reverting to it would re-introduce
the `tool_scope_violation` failure mode fixed in `07139f8`. The policy
remains `permissive` until a corrected, hermeskill-aware allowlist is
designed. **Do not auto-revert to `coding-default`.**

---

## 4. What Could Happen After a Hermes Update

| Trigger | Likely effect |
|---|---|
| Hermes core update touches `hermes_cli/plugins.py` or hook dispatch | Plugin may fail to register or hooks may not fire. Check via §5. |
| Hermes rebuilds its venv (`pip install --force-reinstall ...`) | **Editable installs are not preserved.** Hermeskill will appear gone. |
| Hermes updates entry-point discovery logic | May silently stop scanning workspace-style editable installs. |
| Hermes renames `hermes_agent.plugins` entry-point group | Plugin would no longer be discovered. |
| Hermes changes hook signatures (`register_hook`) | Hooks may be registered but never invoked. |
| Gateway restart | Should be transparent; verify `active (running)` post-restart. |

In all of the above, **never assume** that because `hermes plugins list`
shows nothing, Hermeskill is gone — see §5.

---

## 5. Post-Update Verification Commands

Run these in order after any Hermes update. None of them mutate state.

```bash
# 1. Editable installs still point at the repo?
/home/ai/.hermes/hermes-agent/venv/bin/python -m pip show hermeskill
/home/ai/.hermes/hermes-agent/venv/bin/python -m pip show hermeskill-hermes

# 2. SDK actually imports from the editable source path?
/home/ai/.hermes/hermes-agent/venv/bin/python -c \
  "import hermeskill; print(hermeskill.__file__)"
/home/ai/.hermes/hermes-agent/venv/bin/python -c \
  "import hermeskill_hermes; print(hermeskill_hermes.__file__)"

# 3. Plugin still registered inside the gateway?
#    Use the bundled check script — see §6.
bash /opt/ai/projects/hermes-upgrades/hermeskill/scripts/check-production-integration.sh

# 4. Gateway health (read-only — do NOT restart from inside the gateway)
/usr/bin/systemctl --user show hermes-gateway.service \
  --property=ActiveState,SubState,MainPID
```

A clean run shows:

- Both pip entries report `Editable project location: /opt/ai/projects/hermes-upgrades/hermeskill/packages/...`
- `hermeskill.__file__` resolves under `packages/hermeskill-sdk/src/hermeskill/`
- `hermeskill_hermes.__file__` resolves under `packages/hermeskill-hermes/src/hermeskill_hermes/`
- Check script reports `PluginManager.hermeskill: enabled=True, error=None`
- Gateway: `ActiveState=active`

---

## 6. The Bundled Check Script (Read-Only)

**Path:** `scripts/check-production-integration.sh`

This script does **only** checks. It never modifies config, never restarts
the gateway, never re-installs anything. Run it freely after any change.

```bash
bash /opt/ai/projects/hermes-upgrades/hermeskill/scripts/check-production-integration.sh
```

It prints, among other things:

- Current branch and HEAD
- Working tree cleanliness
- Whether the stable tag exists
- SDK and plugin import paths (verifies editable install is real)
- `PluginManager` entry for `hermeskill` (name, enabled, error)
- Registered hooks (proves the plugin actually wired into Hermes)
- `~/.hermeskill/config.toml` current `policy`
- `hermes-gateway.service` state via `systemctl --user show` (read-only)
- A scan of recent gateway logs for the failure patterns §3 was fixing:
  `failed to load plugin`, `traceback`, `apoptosis`, `tool_scope_violation`,
  `client has been closed`, `failed to rotate`

---

## 6.5. Update-Time Compatibility Check (Stricter, Recovery-Aware)

For Hermes update flows, a separate stricter check is provided:

**Path:** `scripts/check-hermes-update-compatibility.sh`

Exit codes (precedence 60 > 50 > 40 > 30 > 20 > 10):

| Code | Trigger | Auto-recovery |
|------|---------|---------------|
| 0    | All checks passed | NO |
| 10   | editable install lost, import paths wrong, entry-point missing | **YES** — only code that triggers recovery |
| 20   | plugin registered but error ≠ None; OR current-session load-time log patterns | NO |
| 30   | PluginManager has no hermeskill entry; OR required hooks missing; OR current-session lifecycle API errors | NO |
| 40   | Hermes production Python missing; OR Gateway not active | NO |
| 50   | policy ≠ permissive | NO |
| 60   | git repo / stable commit / core-fix-ancestry / tag abnormal | NO |

### Sectional details

- **Section 0 — Git state (code 60).** Verifies the repo, branch=main,
  HEAD exists, AND `git merge-base --is-ancestor 07139f8 HEAD` succeeds,
  AND tag `hermeskill-session-reset-fixed-20260715` resolves to
  `07139f8`. Working tree is logged READ-ONLY — never reset, never
  checked-out, never overwritten.
- **Section 1 — Hermes Python (early bail — code 40).** If
  `~/.hermes/hermes-agent/venv/bin/python` is missing or not
  executable, return 40 immediately. Skip all python-dependent
  sections.
- **Sections 2-5 — Editable install (code 10).** Imports must
  succeed AND resolve under `/opt/ai/projects/hermes-upgrades/hermeskill/packages/...`.
  Entry-point must point at `hermeskill_hermes`.
- **Section 6 — PluginManager (code 20 / 30).** Plugin not registered
  → 30. Plugin registered with non-empty `error` → 20.
- **Section 7 — Required hooks (code 30).** All seven hooks must be
  present in `pm._hooks`: pre_tool_call, post_tool_call, pre_llm_call,
  post_api_request, on_session_reset, on_session_end, on_session_finalize.
- **Section 8 — Policy (code 50).** `~/.hermeskill/config.toml`
  must have `policy = "permissive"`.
- **Section 9 — Gateway (code 40).** Reads MainPID, ActiveState,
  SubState, ActiveEnterTimestamp via `systemctl --user show`. State
  must be active/running.
- **Section 10 — Current-session logs only.** The script reads
  `journalctl --user -u hermes-gateway.service --since=<ActiveEnterTimestamp>`.
  Patterns classified per Master spec:
  - Lifecycle / API errors (`unsupported hook`, `invalid hook`,
    `lifecycle api`, `hook not registered`, `unknown hook`) → 30.
  - Load-time errors (`failed to load plugin`, hermeskill traceback,
    `client has been closed`, `failed to rotate`) → 20.
  - `apoptosis` / `tool_scope_violation` are only counted as 20 when
    the current policy is permissive — old-session failures are
    ignored.

### Reports

Every run writes a redacted log to
`~/.hermeskill/update-reports/<UTC-timestamp>-compatibility-check.log`.
Redaction patterns cover `sk_live_`, `sk_test_`, `sk-`, `api_key=`,
`token=`, `Bearer `, `Authorization:`, `wx_`, `telegram_bot_token`,
`feishu_tenant_access_token`, and similar substrings.

Use the update-time check after every Hermes update. The unified
wrapper is `~/.local/bin/hermes-safe-update`.

---

## 7. Recovery — When Editable Install Is Lost

Typical symptom: `pip show hermeskill` shows the package at the venv site-packages
but `Editable project location` is empty / wrong, or the package is missing
entirely.

**Recovery (exact command block — do not improvise):**

```bash
cd /opt/ai/projects/hermes-upgrades/hermeskill

/home/ai/.hermes/hermes-agent/venv/bin/python -m pip install \
  -e ./packages/hermeskill-sdk \
  -e ./packages/hermeskill-hermes

# Then verify (read-only):
bash /opt/ai/projects/hermes-upgrades/hermeskill/scripts/check-production-integration.sh
```

After verifying the check script reports `enabled=True, error=None`, restart
the gateway **from a separate shell, not from inside the running gateway**:

```bash
# Run from a fresh login shell, not from the gateway's own tmux/session.
/usr/bin/systemctl --user restart hermes-gateway.service
/usr/bin/systemctl --user status hermes-gateway.service --no-pager
```

> **Why the restart caveat:** Issuing `systemctl --user restart` from inside
> the gateway process triggers SIGTERM propagation that kills the command
> itself before completion. Always restart from outside.

---

## 8. Gateway Restart & Log Inspection

Read-only gateway state and log commands (safe to run from anywhere):

```bash
# State — read-only, never triggers SIGTERM
/usr/bin/systemctl --user show hermes-gateway.service \
  --property=ActiveState,SubState,MainPID,ExecMainStartTimestamp

# Recent logs (filter to failure patterns)
/usr/bin/journalctl --user -u hermes-gateway.service -n 500 \
  | grep -Ei 'hermeskill|apoptosis|tool_scope|traceback|client has been closed|failed to rotate|failed to load plugin'

# Hook activity (last 200 lines, filtered)
/usr/bin/journalctl --user -u hermes-gateway.service -n 200 \
  --no-pager | grep -E 'hermeskill|hook'
```

Restart command (only from outside the gateway):

```bash
/usr/bin/systemctl --user restart hermes-gateway.service
```

---

## 9. Rollback

To revert Hermeskill to the known-good state:

```bash
cd /opt/ai/projects/hermes-upgrades/hermeskill

# Confirm the stable tag is present
git tag --list hermeskill-session-reset-fixed-20260715

# Reset main to the tag (preserves the tag itself)
git checkout main
git reset --hard hermeskill-session-reset-fixed-20260715

# Re-assert the editable installs (defensive — they should already be valid)
cd /opt/ai/projects/hermes-upgrades/hermeskill
/home/ai/.hermes/hermes-agent/venv/bin/python -m pip install \
  -e ./packages/hermeskill-sdk \
  -e ./packages/hermeskill-hermes

# Restart gateway from a separate shell
/usr/bin/systemctl --user restart hermes-gateway.service
```

If the tag is missing locally:

```bash
git fetch --tags origin
git checkout main
git reset --hard hermeskill-session-reset-fixed-20260715
```

---

## 10. Forbidden Actions

These will break the integration or undo the fix:

| Forbidden | Why |
|---|---|
| Switching `policy` back to `coding-default` | Re-introduces `tool_scope_violation` on `terminal` until an updated allowlist is designed. |
| Re-installing Hermeskill as a non-editable (`pip install ./packages/...` without `-e`) | Detaches the running venv from the source tree; future edits invisible. |
| Issuing `systemctl --user restart hermes-gateway.service` **from inside the running gateway** | The restart SIGTERM propagates to the parent shell and kills the command. |
| Deleting or renaming the `hermeskill-session-reset-fixed-20260715` tag | Removes the rollback anchor. |
| Editing `hermeskill/policies.py` to "tighten" rules without re-running the §5 verification | Regressions to the session-boundary fix are silent. |
| Treating `hermes plugins list --plain --no-bundled` not showing `hermeskill` as proof the plugin is missing | The plugin may still be loaded — verify via `PluginManager` (see §5 / §6). |
| Auto-merging main → production without running `check-production-integration.sh` | Skips the only authoritative end-to-end check. |

---

## 11. The Unified Update Wrapper: `hermes-safe-update`

**Path:** `~/.local/bin/hermes-safe-update`

A single command that replaces `hermes update` for any flow where
Hermeskill integration matters. Workflow:

1. Snapshot pre-update state (Hermes version, HEAD, Hermeskill HEAD,
   import paths, gateway PID).
2. Run `hermes update --yes` (the canonical non-interactive path from
   `hermes update --help`; nothing invented).
   - **If the official update returns non-zero → STOP.** Per Master
     spec point 6: do NOT restart the gateway, do NOT run recovery,
     do NOT classify as code 10, preserve the full update output in
     the report. Exit with the update's exit code.
3. Run `check-hermes-update-compatibility.sh`.
4. Dispatch on the exit code:
   - **0** → no recovery needed. Print before/after summary. Exit 0.
   - **10** → call `recover-hermeskill-integration.sh` (re-editable
     only). If recovery fails, propagate that exit code.
   - **20 / 30 / 40 / 50 / 60** → STOP, print diagnostics, do NOT
     auto-modify anything. The user must investigate manually.
5. Print a before/after report (versions, HEADs, recovery action, final
   state).

### Test modes (no real Hermes update is ever invoked without `--yes`)

| Flag | Effect |
|------|--------|
| `--mock-update=success` | Simulate `hermes update --yes` returning 0 |
| `--mock-update=fail`    | Simulate `hermes update --yes` returning non-zero (42) |
| `--mock-update=partial` | Simulate a partial update that breaks integration |
| `--skip-update`         | Skip the official update; run the check only |
| `--yes`                 | Actually run `hermes update --yes` on the production install |

By default the wrapper REFUSES to run a real update — exit 79 with a
help message. This is per Master spec point 11: do NOT trigger real
updates in this round.

### Auto-hook policy

Hermes does **not** expose a generic user-facing post-update hook.
Only Windows-gateway resume logic lives in `hermes_cli/main.py`
(`_resume_windows_gateways_after_update`). Therefore:

- **No automatic hook is installed.**
- `hermes-safe-update` is the **only** integration point.
- **No timer** re-runs the recovery in a loop.

### Notification strategy

If `hermes update --yes` fails or recovery fails, the wrapper writes
to `~/.hermeskill/update-reports/` (always) and attempts to send a
notification via:

- `systemd-cat -t hermeskill-health` (always available for user
  services).
- `hermes notify ...` if a Hermes CLI notification subcommand exists.

If notification fails, the wrapper does NOT modify Telegram, Feishu,
or Gateway config to "fix" it. It surfaces the failure through the
report and exit code only.

### Recovery boundaries (hard rules)

`recover-hermeskill-integration.sh` will **only** act when the
check returns exit code 10. In that case it:

- Re-runs the check and parses its exit code STRICTLY — only proceeds
  on exit 10.
- Re-verifies both package directories exist.
- Logs the git working tree status READ-ONLY (does NOT reset,
  checkout-overwrite, or otherwise mutate). Uncommitted changes are
  tolerated and explicitly noted.
- Reinstalls the two editable packages:
  `pip install -e ./packages/hermeskill-sdk -e ./packages/hermeskill-hermes`
- Restarts the gateway.
- Re-runs the check.
- If the second check still fails → STOP. Never retries, never modifies
  unrelated state.

It will **never**:

- `git reset`, `git checkout` (overwrite), or cherry-pick.
- Modify Hermes Agent source.
- Auto-roll Hermes back.
- Switch Hermeskill policy.
- Run while inside the gateway's own process tree (detected via
  ppid-walk up to the gateway MainPID; refuses with exit 73).
- Retry recovery after a failed post-check.

---

## 11.5. The Daily Health Check (Read-Only)

**Path:** `scripts/daily-health-check.sh`

A read-only health check suitable for cron. Per Master spec point 10:
**even if the check returns code 10, this script NEVER invokes
recovery.** Recovery is only triggered via `hermes-safe-update` after
an explicit update flow.

Workflow:

1. Run `check-hermes-update-compatibility.sh`.
2. Write a daily-health report to
   `~/.hermeskill/update-reports/<UTC-timestamp>-daily-health.log`.
3. If non-zero, attempt notification via:
   - `systemd-cat -t hermeskill-health` (always available)
   - `hermes notify ...` if the Hermes CLI supports it
4. If notification fails: write the report, return non-zero, do NOT
   modify any notification channel configuration.
5. Exit with the check's exit code.

Suggested cron entry (do not install without explicit Master
authorization):

```cron
0 9 * * * /opt/ai/projects/hermes-upgrades/hermeskill/scripts/daily-health-check.sh
```

---

## 12. Plain-Language Recovery (for non-engineers)

If something feels wrong with Hermeskill after a Hermes update:

1. **Don't panic.** The plugin is installed as "editable", which means it is
   wired straight into the source folder. If Hermes itself was rebuilt, the
   wiring may have been wiped — that's recoverable in three commands.

2. **Run the check script first.** It will tell you, in plain text, whether
   everything is healthy.

   ```bash
   bash /opt/ai/projects/hermes-upgrades/hermeskill/scripts/check-production-integration.sh
   ```

3. **If the script says the editable install is broken**, paste this block
   into a fresh terminal (NOT the chat where the gateway is running):

   ```bash
   cd /opt/ai/projects/hermes-upgrades/hermeskill
   /home/ai/.hermes/hermes-agent/venv/bin/python -m pip install \
     -e ./packages/hermeskill-sdk \
     -e ./packages/hermeskill-hermes
   /usr/bin/systemctl --user restart hermes-gateway.service
   ```

4. **Run the check script again.** If it now reports
   `hermeskill: enabled=True, error=None`, recovery is complete.

5. **If something still looks wrong**, the rollback anchor is the git tag
   `hermeskill-session-reset-fixed-20260715`. Reverting to it is safe and
   is documented in §9.

The recovery procedure is intentionally short and idempotent. There is no
scenario where running it twice produces different results.