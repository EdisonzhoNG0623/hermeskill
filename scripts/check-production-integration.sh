#!/usr/bin/env bash
# check-production-integration.sh
#
# READ-ONLY health check for the Hermeskill ↔ Hermes Agent production
# integration. This script MUST NOT mutate any state: no installs, no
# restarts, no config edits, no gateway controls beyond read-only `show`.
#
# Run after any Hermes update, venv rebuild, or manual pip operation to
# confirm Hermeskill is still correctly registered.
#
# Exits non-zero only when a hard failure is detected (missing tag, missing
# editable install, plugin disabled in PluginManager, gateway inactive).

set -u
# Note: deliberately not using `set -e` — this script is informational
# and must keep going through every check even when an earlier one fails.

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_PY="/home/ai/.hermes/hermes-agent/venv/bin/python"
CONFIG_FILE="${HOME}/.hermeskill/config.toml"
GATEWAY_UNIT="hermes-gateway.service"
EXPECTED_TAG="hermeskill-session-reset-fixed-20260715"
EXPECTED_HEAD="79cf830"
EXPECTED_FIX_COMMIT="07139f8"
LOG_SCAN_LINES=500

# Colours (only when stdout is a TTY)
if [ -t 1 ]; then
  C_OK="\033[32m"; C_FAIL="\033[31m"; C_WARN="\033[33m"; C_INFO="\033[36m"; C_RST="\033[0m"
else
  C_OK=""; C_FAIL=""; C_WARN=""; C_INFO=""; C_RST=""
fi

ok()   { printf "  %s✓%s %s\n" "$C_OK"   "$C_RST" "$1"; }
fail() { printf "  %s✗%s %s\n" "$C_FAIL" "$C_RST" "$1"; }
warn() { printf "  %s!%s %s\n" "$C_WARN" "$C_RST" "$1"; }
info() { printf "  %s·%s %s\n" "$C_INFO" "$C_RST" "$1"; }
hdr()  { printf "\n%s%s%s\n" "$C_INFO" "$1" "$C_RST"; printf "%.s─" $(seq 1 ${#1}); printf "\n"; }

exit_code=0
record_fail() { exit_code=1; }

# 1. Repository state
hdr "1. Git Repository State"
cd "$REPO" || { fail "Repository not found at $REPO"; exit 2; }

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
CURRENT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
WORKTREE="$(git status --short 2>/dev/null)"
HAS_TAG="$(git tag --list "$EXPECTED_TAG" | head -1)"

[ "$CURRENT_BRANCH" = "main" ] \
  && ok "Branch: main" \
  || { warn "Branch: $CURRENT_BRANCH (expected main)"; }

if [ "$CURRENT_HEAD" = "$EXPECTED_HEAD" ]; then
  ok "HEAD: $CURRENT_HEAD"
elif [ "$CURRENT_HEAD" = "$EXPECTED_FIX_COMMIT" ]; then
  warn "HEAD: $CURRENT_HEAD (one commit behind expected $EXPECTED_HEAD)"
else
  warn "HEAD: $CURRENT_HEAD (expected $EXPECTED_HEAD or $EXPECTED_FIX_COMMIT)"
fi

if [ -z "$WORKTREE" ]; then
  ok "Working tree: clean"
else
  warn "Working tree has changes:"
  printf '%s\n' "$WORKTREE" | sed 's/^/      /'
fi

if [ -n "$HAS_TAG" ]; then
  ok "Stable tag present: $EXPECTED_TAG"
else
  fail "Stable tag missing: $EXPECTED_TAG"
  record_fail
fi

# 2. Editable install — pip metadata
hdr "2. Editable Install (pip metadata)"
if [ -x "$HERMES_PY" ]; then
  HERMESKILL_LOC="$("$HERMES_PY" -m pip show hermeskill 2>/dev/null \
    | awk -F': ' '/^Location: /{print $2}')"
  HERMESKILL_EDIT="$("$HERMES_PY" -m pip show hermeskill 2>/dev/null \
    | awk -F': ' '/^Editable project location: /{print $2}')"
  PLUGIN_LOC="$("$HERMES_PY" -m pip show hermeskill-hermes 2>/dev/null \
    | awk -F': ' '/^Location: /{print $2}')"
  PLUGIN_EDIT="$("$HERMES_PY" -m pip show hermeskill-hermes 2>/dev/null \
    | awk -F': ' '/^Editable project location: /{print $2}')"

  case "$HERMESKILL_EDIT" in
    *"/packages/hermeskill-sdk") ok "hermeskill editable → $HERMESKILL_EDIT" ;;
    "")                          fail "hermeskill not installed editable (no editable location)"; record_fail ;;
    *)                           warn "hermeskill editable points elsewhere: $HERMESKILL_EDIT" ;;
  esac

  case "$PLUGIN_EDIT" in
    *"/packages/hermeskill-hermes") ok "hermeskill-hermes editable → $PLUGIN_EDIT" ;;
    "")                             fail "hermeskill-hermes not installed editable"; record_fail ;;
    *)                              warn "hermeskill-hermes editable points elsewhere: $PLUGIN_EDIT" ;;
  esac
else
  fail "Hermes python not found at $HERMES_PY"
  record_fail
fi

# 3. Actual import paths (proves pip metadata matches runtime)
hdr "3. Runtime Import Paths"
HERMESKILL_FILE="$("$HERMES_PY" -c 'import hermeskill; print(hermeskill.__file__)' 2>/dev/null || echo '')"
PLUGIN_FILE="$("$HERMES_PY" -c 'import hermeskill_hermes; print(hermeskill_hermes.__file__)' 2>/dev/null || echo '')"

case "$HERMESKILL_FILE" in
  *"/packages/hermeskill-sdk/src/hermeskill/__init__.py")
    ok "hermeskill runtime → $HERMESKILL_FILE" ;;
  "")   fail "hermeskill import FAILED (not on sys.path under editable install)"; record_fail ;;
  *)    warn "hermeskill runtime resolved to non-repo path: $HERMESKILL_FILE" ;;
esac

case "$PLUGIN_FILE" in
  *"/packages/hermeskill-hermes/src/hermeskill_hermes/__init__.py")
    ok "hermeskill_hermes runtime → $PLUGIN_FILE" ;;
  "")  fail "hermeskill_hermes import FAILED"; record_fail ;;
  *)   warn "hermeskill_hermes runtime resolved to non-repo path: $PLUGIN_FILE" ;;
esac

# 4. PluginManager introspection (the authoritative "is it loaded?" check)
hdr "4. PluginManager (authoritative plugin state)"
PM_OUT="$("$HERMES_PY" - <<'PY' 2>/dev/null
import json
try:
    from hermes_cli.plugins import _ensure_plugins_discovered
except Exception as e:
    print(json.dumps({"error": f"import_hermes_cli_failed: {e}"}))
    raise SystemExit(0)

try:
    pm = _ensure_plugins_discovered()
except Exception as e:
    print(json.dumps({"error": f"discover_failed: {e}"}))
    raise SystemExit(0)

hk = "hermeskill"
info = None
for p in pm.list_plugins():
    if p["name"] == hk or p["key"] == hk:
        info = p
        break

hooks = sorted(pm._hooks.keys())
hermeskill_hook_count = sum(len(pm._hooks.get(h, [])) for h in hooks)

if info is None:
    print(json.dumps({"registered": False, "hooks": hooks}))
else:
    print(json.dumps({
        "registered": True,
        "name": info["name"],
        "key": info["key"],
        "enabled": info["enabled"],
        "error": info["error"],
        "tools": info["tools"],
        "hooks_registered": info["hooks"],
        "middleware": info["middleware"],
        "commands": info["commands"],
        "version": info["version"],
        "all_hooks": hooks,
    }))
PY
)"

if [ -z "$PM_OUT" ] || ! printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  fail "PluginManager introspection failed (gateway may not be importable from this shell)"
  info "Output: ${PM_OUT:0:200}"
else
  REGISTERED="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["registered"])')"
  if [ "$REGISTERED" = "True" ]; then
    ENABLED="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["enabled"])')"
    PMERROR="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["error"])')"
    PHOOKS="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["hooks_registered"])')"
    PVERSION="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["version"])')"
    if [ "$ENABLED" = "True" ]; then
      ok "PluginManager: hermeskill registered, enabled=True"
    else
      fail "PluginManager: hermeskill registered but enabled=False"
      record_fail
    fi
    if [ "$PMERROR" = "None" ] || [ -z "$PMERROR" ]; then
      ok "PluginManager error: None"
    else
      fail "PluginManager error: $PMERROR"
      record_fail
    fi
    info "Version: ${PVERSION}  |  Hooks registered by this plugin: ${PHOOKS}"
  else
    fail "PluginManager: hermeskill NOT registered"
    record_fail
  fi

  ALL_HOOKS="$(printf '%s' "$PM_OUT" | "$HERMES_PY" -c 'import json,sys;print(",".join(json.loads(sys.stdin.read())["all_hooks"]))')"
  if [ -n "$ALL_HOOKS" ]; then
    info "All registered hooks: $ALL_HOOKS"
  else
    warn "PluginManager has no hooks registered globally"
  fi
fi

# 5. Config policy
hdr "5. Hermeskill Config Policy"
if [ -r "$CONFIG_FILE" ]; then
  POLICY="$(grep -E '^[[:space:]]*policy[[:space:]]*=' "$CONFIG_FILE" \
    | head -1 | sed -E 's/.*=[[:space:]]*"?([^"]+)"?.*/\1/')"
  if [ "$POLICY" = "permissive" ]; then
    ok "policy = permissive (expected)"
  elif [ -n "$POLICY" ]; then
    warn "policy = $POLICY (expected permissive — see docs §3)"
  else
    fail "policy line not parseable in $CONFIG_FILE"
  fi
  info "Config file: $CONFIG_FILE"
else
  fail "Config not readable: $CONFIG_FILE"
  record_fail
fi

# 6. Gateway state — read-only `show`, never `restart`
hdr "6. Gateway State (read-only)"
GW_STATE="$(systemctl --user show "$GATEWAY_UNIT" \
  --property=ActiveState,SubState,MainPID 2>/dev/null | tr '\n' ' ')"
if [ -n "$GW_STATE" ]; then
  info "$GW_STATE"
  case "$GW_STATE" in
    *"ActiveState=active"*"SubState=running"*) ok "Gateway active and running" ;;
    *"ActiveState=active"*)                   warn "Gateway active but SubState not 'running'" ;;
    *)                                        fail "Gateway NOT active"; record_fail ;;
  esac
else
  fail "systemctl --user show $GATEWAY_UNIT returned nothing (unit missing?)"
  record_fail
fi

# 7. Recent log scan for known failure patterns
hdr "7. Recent Gateway Logs — Failure Pattern Scan"
LOG_PATTERNS='failed to load plugin|traceback|apoptosis|tool_scope_violation|client has been closed|failed to rotate'
if command -v journalctl >/dev/null 2>&1; then
  LOG_OUT="$(journalctl --user -u "$GATEWAY_UNIT" -n "$LOG_SCAN_LINES" --no-pager 2>/dev/null \
    | grep -Ei "$LOG_PATTERNS" || true)"
  if [ -z "$LOG_OUT" ]; then
    ok "No failure patterns in last $LOG_SCAN_LINES log lines"
  else
    warn "Failure patterns detected in recent logs:"
    printf '%s\n' "$LOG_OUT" | tail -n 20 | sed 's/^/      /'
  fi
else
  warn "journalctl not available; skipping log scan"
fi

# 8. Summary
hdr "Summary"
if [ $exit_code -eq 0 ]; then
  printf "%s✓ All checks passed.%s Hermeskill is healthy in production.\n" "$C_OK" "$C_RST"
else
  printf "%s✗ One or more checks failed.%s See docs/operations/hermes-production-integration.md §7 for recovery.\n" "$C_FAIL" "$C_RST"
fi

exit $exit_code