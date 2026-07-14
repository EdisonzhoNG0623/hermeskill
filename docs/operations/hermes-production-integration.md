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

Differences vs `check-production-integration.sh`:

- Uses explicit exit codes (0/10/20/30/40/50/60) instead of just pass/fail.
- Designed to drive `scripts/recover-hermeskill-integration.sh`.
- The exit code **collapses to 10** whenever the editable install is lost,
  even if downstream checks (PluginManager, hooks) also fail — code 10 is
  the root-cause signal that triggers the single auto-recovery path.
- Exits 20/30/40/50/60 are NOT auto-recoverable and stop the wrapper.

Use the update-time check after every `hermes update` run. The unified
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
3. Run `check-hermes-update-compatibility.sh`.
4. Dispatch on the exit code:
   - **0** → no recovery needed, gateway restart verification only.
   - **10** → call `recover-hermeskill-integration.sh` (re-editable only).
   - **20 / 30 / 40 / 50 / 60** → STOP, print diagnostics, do NOT
     auto-modify anything. The user must investigate manually.
5. Print a before/after report (versions, HEADs, recovery action, final
   state).

### Auto-hook policy

Hermes does **not** expose a generic user-facing post-update hook.
Only Windows-gateway resume logic lives in `hermes_cli/main.py`
(`_resume_windows_gateways_after_update`). Therefore:

- **No automatic hook is installed.**
- `hermes-safe-update` is the **only** integration point.
- **No timer** re-runs the recovery in a loop.
- A daily read-only health check (no auto-fix) is recommended as a
  future cron, but is NOT installed by this change.

### Recovery boundaries (hard rules)

`recover-hermeskill-integration.sh` will **only** act when the
check returns exit code 10. In that case it:

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
- Run while inside the gateway's own process tree (the wrapper is
  always invoked from the user's shell or a cron job, not from inside
  the gateway).

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