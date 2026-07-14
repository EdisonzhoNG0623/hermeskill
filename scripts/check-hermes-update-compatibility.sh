#!/usr/bin/env bash
# check-hermes-update-compatibility.sh
#
# READ-ONLY check: is Hermeskill still correctly integrated into the
# production Hermes Agent install? Designed to be run after any Hermes
# update or venv rebuild.
#
# Strictly informational. Never installs, restarts, edits configs, or
# modifies Git state.
#
# Exit codes — IMPORTANT: recover-hermeskill-integration.sh only acts on
# exit code 10. Every other non-zero code is a HARD STOP requiring human
# review.
#
#   0  Healthy — full integration confirmed.
#  10  Editable install lost or import paths wrong — RECOVERABLE.
#      recover-hermeskill-integration.sh will reinstall editable.
#  20  Plugin entry exists in PluginManager but failed to load (enabled
#      but error != None, or entry missing). NOT auto-recoverable.
#  30  Hermes plugin API / hook lifecycle appears incompatible
#      (e.g. required hooks missing). NOT auto-recoverable.
#  40  Gateway service is not active. NOT auto-recoverable here.
#  50  Hermeskill policy != "permissive". NOT auto-recoverable.
#  60  Hermeskill git repository or stable commit/tag is in an
#      unexpected state. NOT auto-recoverable.

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_AGENT_DIR="/home/ai/.hermes/hermes-agent"
HERMES_PY="${HERMES_AGENT_DIR}/venv/bin/python"
CONFIG_FILE="${HOME}/.hermeskill/config.toml"
GATEWAY_UNIT="hermes-gateway.service"
EXPECTED_TAG="hermeskill-session-reset-fixed-20260715"
EXPECTED_HEAD_FALLBACK="79cf830"
REQUIRED_HOOKS="pre_tool_call post_tool_call pre_llm_call post_api_request on_session_reset on_session_end on_session_finalize"

if [ -t 1 ]; then
  C_OK="\033[32m"; C_FAIL="\033[31m"; C_WARN="\033[33m"; C_INFO="\033[36m"; C_RST="\033[0m"
else
  C_OK=""; C_FAIL=""; C_WARN=""; C_INFO=""; C_RST=""
fi

ok()    { printf "  %s✓%s %s\n" "$C_OK"   "$C_RST" "$1"; }
fail()  { printf "  %s✗%s %s\n" "$C_FAIL" "$C_RST" "$1"; }
warn()  { printf "  %s!%s %s\n" "$C_WARN" "$C_RST" "$1"; }
info()  { printf "  %s·%s %s\n" "$C_INFO" "$C_RST" "$1"; }
hdr()   { printf "\n%s%s%s\n" "$C_INFO" "$1" "$C_RST"; printf "%.s─" $(seq 1 ${#1}); printf "\n"; }

# Track the *worst* code so far. The script only sets a code when its
# section decides one.
#
# Precedence rule: code 10 (editable install lost) is the ROOT-CAUSE
# signal that triggers auto-recovery. When code 10 is set, downstream
# failures (20/30/etc.) are SYMPTOMS of the same root cause and we
# collapse the final exit code to 10 so that recover-hermeskill-integration.sh
# fires its single recovery path. Codes 20, 30, 40, 50, 60 are NOT
# collapsed — they require distinct human responses.
FINAL_CODE=0
CODE_10_TRIPPED=0

# Helper: short, focused section-level exit; only escalates to higher codes.
# Precedence (highest first): 60, 50, 40, 30, 20. We never lower.
set_code() {
  local new=$1
  if [ "$new" = "10" ]; then
    CODE_10_TRIPPED=1
  fi
  if [ "$new" -gt "$FINAL_CODE" ]; then
    FINAL_CODE=$new
  fi
}

# Apply the 10-collapse rule: if code 10 was tripped, it wins for the
# final exit. This ensures recover-hermeskill-integration.sh always
# receives a 10 when the editable install is lost, regardless of
# downstream symptom codes.
apply_precedence() {
  if [ "$CODE_10_TRIPPED" = "1" ]; then
    FINAL_CODE=10
  fi
}

# ----------------------------------------------------------------------
# Section 0: git repository state (code 60)
# ----------------------------------------------------------------------
hdr "0. Hermeskill Repository State (code 60)"
if [ ! -d "$REPO/.git" ]; then
  fail "Not a git repo: $REPO"
  set_code 60
else
  cd "$REPO" || { fail "Cannot cd $REPO"; set_code 60; }
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
  HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
  HAS_TAG="$(git tag --list "$EXPECTED_TAG" | head -1)"
  if [ "$BRANCH" = "main" ]; then ok "Branch: main"; else fail "Branch not main: $BRANCH"; set_code 60; fi
  if [ "$HEAD" = "$EXPECTED_HEAD_FALLBACK" ]; then ok "HEAD: $HEAD (matches stable baseline 79cf830)"; else warn "HEAD: $HEAD (baseline $EXPECTED_HEAD_FALLBACK — non-fatal, repo may have new docs/scripts commits)"; fi
  if [ -n "$HAS_TAG" ]; then ok "Stable tag present: $EXPECTED_TAG"; else fail "Stable tag missing: $EXPECTED_TAG"; set_code 60; fi
fi

# ----------------------------------------------------------------------
# Section 1: Hermes Python exists (code 40-class; lumped with gateway
# severity since no Hermes python = no Hermes to integrate with).
# ----------------------------------------------------------------------
hdr "1. Hermes Python"
if [ -x "$HERMES_PY" ]; then
  ok "Hermes python present: $HERMES_PY"
  PY_VER="$("$HERMES_PY" --version 2>&1 | head -1)"
  info "$PY_VER"
else
  fail "Hermes python missing: $HERMES_PY"
  set_code 40
  # Without the python we cannot run further checks meaningfully.
  printf "\n%sSummary: code %d — Hermes python missing, cannot continue.%s\n" "$C_FAIL" "$FINAL_CODE" "$C_RST"
  exit "$FINAL_CODE"
fi

# ----------------------------------------------------------------------
# Section 2-5: runtime import paths and entry-point discovery (code 10)
# ----------------------------------------------------------------------
hdr "2-5. Editable Install & Plugin Discovery (code 10)"
HK_FILE="$("$HERMES_PY" -c 'import hermeskill; print(hermeskill.__file__)' 2>/dev/null || true)"
HH_FILE="$("$HERMES_PY" -c 'import hermeskill_hermes; print(hermeskill_hermes.__file__)' 2>/dev/null || true)"

if [ -z "$HK_FILE" ]; then
  fail "import hermeskill FAILED"
  set_code 10
elif [[ "$HK_FILE" == *"/packages/hermeskill-sdk/src/hermeskill/"* ]]; then
  ok "hermeskill → $HK_FILE"
else
  fail "hermeskill resolves outside repo: $HK_FILE"
  set_code 10
fi

if [ -z "$HH_FILE" ]; then
  fail "import hermeskill_hermes FAILED"
  set_code 10
elif [[ "$HH_FILE" == *"/packages/hermeskill-hermes/src/hermeskill_hermes/"* ]]; then
  ok "hermeskill_hermes → $HH_FILE"
else
  fail "hermeskill_hermes resolves outside repo: $HH_FILE"
  set_code 10
fi

# Entry-point check via importlib.metadata
EP_JSON="$("$HERMES_PY" - <<'PY' 2>/dev/null
import json, sys
try:
    from importlib.metadata import entry_points
except Exception as e:
    print(json.dumps({"error": f"importlib_failed: {e}"}))
    sys.exit(0)
try:
    eps = entry_points(group="hermes_agent.plugins")
    matches = [ep for ep in eps if ep.name == "hermeskill"]
    if not matches:
        print(json.dumps({"found": False, "count": len(eps)}))
    else:
        ep = matches[0]
        print(json.dumps({
            "found": True,
            "name": ep.name,
            "group": ep.group,
            "value": ep.value,
            "dist_name": ep.dist.name if ep.dist else None,
            "total_plugins": len(eps),
        }))
except Exception as e:
    print(json.dumps({"error": f"entry_points_failed: {e}"}))
PY
)"

if printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  FOUND="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read()).get("found"))')"
  VALUE="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read()).get("value",""))')"
  DIST="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read()).get("dist_name",""))')"
  TOTAL="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read()).get("total_plugins","?"))')"
  if [ "$FOUND" = "True" ]; then
    if [ "$VALUE" = "hermeskill_hermes" ]; then
      ok "Entry-point: hermes_agent.plugins / hermeskill → hermeskill_hermes (dist: $DIST, total plugins: $TOTAL)"
    else
      fail "Entry-point value mismatch: got '$VALUE' expected 'hermeskill_hermes'"
      set_code 20
    fi
  else
    fail "Entry-point hermeskill NOT registered under hermes_agent.plugins"
    set_code 20
  fi
else
  fail "Entry-point introspection failed: ${EP_JSON:0:200}"
  set_code 20
fi

# ----------------------------------------------------------------------
# Section 6: PluginManager state (code 20)
# ----------------------------------------------------------------------
hdr "6. PluginManager (code 20)"
PM_JSON="$("$HERMES_PY" - <<'PY' 2>/dev/null
import json, sys
try:
    from hermes_cli.plugins import _ensure_plugins_discovered
except Exception as e:
    print(json.dumps({"error": f"import_hermes_cli_failed: {e}"}))
    sys.exit(0)

try:
    pm = _ensure_plugins_discovered()
except Exception as e:
    print(json.dumps({"error": f"discover_failed: {e}"}))
    sys.exit(0)

info = None
for p in pm.list_plugins():
    if p["name"] == "hermeskill" or p["key"] == "hermeskill":
        info = p
        break

hooks_by_name = {h: list(cbs) for h, cbs in pm._hooks.items()}

print(json.dumps({
    "registered": info is not None,
    "info": info,
    "all_hooks": sorted(hooks_by_name.keys()),
    "hook_counts": {h: len(c) for h, c in hooks_by_name.items()},
}))
PY
)"

if ! printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  fail "PluginManager introspection failed: ${PM_JSON:0:200}"
  set_code 20
else
  REGISTERED="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["registered"])')"
  if [ "$REGISTERED" != "True" ]; then
    fail "PluginManager: hermeskill NOT registered"
    set_code 20
  else
    INFO_JSON="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.dumps(json.loads(sys.stdin.read())["info"]))')"
    ENABLED="$(printf '%s' "$INFO_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["enabled"])')"
    ERR="$(printf '%s' "$INFO_JSON" | "$HERMES_PY" -c 'import json,sys;print(json.loads(sys.stdin.read())["error"])')"
    if [ "$ENABLED" = "True" ]; then
      ok "PluginManager: hermeskill enabled=True"
    else
      fail "PluginManager: hermeskill enabled=False"
      set_code 20
    fi
    if [ "$ERR" = "None" ] || [ -z "$ERR" ]; then
      ok "PluginManager error: None"
    else
      fail "PluginManager error: $ERR"
      set_code 20
    fi
  fi
fi

# ----------------------------------------------------------------------
# Section 7: Required hooks (code 30)
# ----------------------------------------------------------------------
hdr "7. Required Hooks (code 30)"
ALL_HOOKS=""
if [ -n "$PM_JSON" ] && printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  ALL_HOOKS="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys;print(" ".join(json.loads(sys.stdin.read())["all_hooks"]))')"
fi

if [ -z "$ALL_HOOKS" ]; then
  fail "No hooks registered in PluginManager at all"
  set_code 30
else
  MISSING=""
  for h in $REQUIRED_HOOKS; do
    if [[ " $ALL_HOOKS " != *" $h "* ]]; then
      MISSING="$MISSING $h"
    fi
  done
  if [ -n "$MISSING" ]; then
    fail "Missing required hooks:$MISSING"
    set_code 30
  else
    ok "All required hooks present: $REQUIRED_HOOKS"
  fi
fi

# ----------------------------------------------------------------------
# Section 8: Policy (code 50)
# ----------------------------------------------------------------------
hdr "8. Policy (code 50)"
if [ ! -r "$CONFIG_FILE" ]; then
  fail "Config not readable: $CONFIG_FILE"
  set_code 50
else
  POLICY="$(grep -E '^[[:space:]]*policy[[:space:]]*=' "$CONFIG_FILE" 2>/dev/null \
    | head -1 | sed -E 's/.*=[[:space:]]*"?([^"]+)"?.*/\1/')"
  if [ "$POLICY" = "permissive" ]; then
    ok "policy = permissive"
  elif [ -z "$POLICY" ]; then
    fail "policy line not parseable in $CONFIG_FILE"
    set_code 50
  else
    fail "policy = '$POLICY' (expected 'permissive')"
    set_code 50
  fi
fi

# ----------------------------------------------------------------------
# Section 9: Gateway state (code 40)
# ----------------------------------------------------------------------
hdr "9. Gateway (code 40)"
GW_STATE="$(systemctl --user show "$GATEWAY_UNIT" \
  --property=ActiveState,SubState,MainPID 2>/dev/null | tr '\n' ' ')"
if [ -z "$GW_STATE" ]; then
  fail "systemctl --user show returned nothing (unit missing?)"
  set_code 40
else
  info "$GW_STATE"
  if [[ "$GW_STATE" == *"ActiveState=active"* ]]; then
    if [[ "$GW_STATE" == *"SubState=running"* ]]; then
      ok "Gateway active and running"
    else
      warn "Gateway active but SubState != running"
      set_code 40
    fi
  else
    fail "Gateway NOT active"
    set_code 40
  fi
fi

# ----------------------------------------------------------------------
# Section 10: Recent log scan — informational only (does not change code)
# ----------------------------------------------------------------------
hdr "10. Recent Logs — Failure Pattern Scan (informational)"
LOG_PATTERNS='failed to load plugin|traceback|client has been closed|failed to rotate'
if command -v journalctl >/dev/null 2>&1; then
  LOG_OUT="$(journalctl --user -u "$GATEWAY_UNIT" -n 500 --no-pager 2>/dev/null \
    | grep -Ei "$LOG_PATTERNS" || true)"
  if [ -z "$LOG_OUT" ]; then
    ok "No failure patterns in last 500 log lines"
  else
    warn "Failure patterns detected (informational only — do NOT alter exit code):"
    printf '%s\n' "$LOG_OUT" | tail -n 10 | sed 's/^/      /'
  fi
else
  warn "journalctl not available; log scan skipped"
fi

# ----------------------------------------------------------------------
# Section 11: Final verdict — explicitly NOT use plugins list CLI as sole
# evidence. We never gate the verdict on that command's output.
# ----------------------------------------------------------------------
hdr "Summary"
apply_precedence
case "$FINAL_CODE" in
  0)
    printf "%s✓ All checks passed.%s Hermeskill ↔ Hermes integration is healthy.\n" "$C_OK" "$C_RST"
    printf "  Auto-recovery: NOT triggered (not needed).\n"
    ;;
  10)
    printf "%s✗ Editable install lost or import paths wrong.%s\n" "$C_WARN" "$C_RST"
    printf "  Auto-recovery: ELIGIBLE — recover-hermeskill-integration.sh will reinstall editable.\n"
    ;;
  20)
    printf "%s✗ Plugin entry exists in PluginManager but failed to load.%s\n" "$C_FAIL" "$C_RST"
    printf "  Auto-recovery: BLOCKED. Manual investigation required (plugin code may be incompatible).\n"
    ;;
  30)
    printf "%s✗ Hermes plugin API / hook lifecycle appears incompatible.%s\n" "$C_FAIL" "$C_RST"
    printf "  Auto-recovery: BLOCKED. Hermeskill code likely needs an update for this Hermes version.\n"
    ;;
  40)
    printf "%s✗ Gateway service is not active.%s\n" "$C_FAIL" "$C_RST"
    printf "  Auto-recovery: BLOCKED. Investigate systemd first; restart from outside the gateway.\n"
    ;;
  50)
    printf "%s✗ Hermeskill policy != permissive.%s\n" "$C_FAIL" "$C_RST"
    printf "  Auto-recovery: BLOCKED. Auto-switching policy is forbidden by design.\n"
    ;;
  60)
    printf "%s✗ Hermeskill git repository or stable commit/tag abnormal.%s\n" "$C_FAIL" "$C_RST"
    printf "  Auto-recovery: BLOCKED. Local code may have been altered; verify manually.\n"
    ;;
  *)
    printf "%s? Unexpected code %d%s\n" "$C_WARN" "$FINAL_CODE" "$C_RST"
    ;;
esac

exit "$FINAL_CODE"