#!/usr/bin/env bash
# recover-hermeskill-integration.sh
#
# CONDITIONAL recovery — runs ONLY when
# check-hermes-update-compatibility.sh exits with code 10 (editable
# install lost or import paths wrong).
#
# Hard rules (enforced by code, not just comments):
#   * Will NOT git reset, git checkout (overwrite), cherry-pick, or
#     modify Hermeskill source code.
#   * Will NOT modify Hermes Agent source code.
#   * Will NOT auto-roll Hermes back.
#   * Will NOT switch Hermeskill policy.
#   * Will NOT restart the gateway from inside the gateway process
#     (this script runs from the user's shell, a cron job, or the
#     hermes-safe-update wrapper — never from inside the gateway's
#     own shell, where SIGTERM would kill the command mid-flight).
#   * Will NOT modify any policy files.
#   * If the second check fails, it stops and surfaces diagnostics.
#     It will NEVER retry in a loop, NEVER escalate to git operations,
#     NEVER modify unrelated configs.

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_PY="/home/ai/.hermes/hermes-agent/venv/bin/python"
GATEWAY_UNIT="hermes-gateway.service"
CHECK_SCRIPT="$REPO/scripts/check-hermes-update-compatibility.sh"

if [ -t 1 ]; then
  C_OK="\033[32m"; C_FAIL="\033[31m"; C_WARN="\033[33m"; C_INFO="\033[36m"; C_RST="\033[0m"
else
  C_OK=""; C_FAIL=""; C_WARN=""; C_INFO=""; C_RST=""
fi

hdr()   { printf "\n%s%s%s\n" "$C_INFO" "$1" "$C_RST"; printf "%.s─" $(seq 1 ${#1}); printf "\n"; }
ok()    { printf "  %s✓%s %s\n" "$C_OK"   "$C_RST" "$1"; }
fail()  { printf "  %s✗%s %s\n" "$C_FAIL" "$C_RST" "$1"; }
warn()  { printf "  %s!%s %s\n" "$C_WARN" "$C_RST" "$1"; }
info()  { printf "  %s·%s %s\n" "$C_INFO" "$C_RST" "$1"; }

# ----------------------------------------------------------------------
# Step 1: Verify preconditions
# ----------------------------------------------------------------------
hdr "Preconditions"

if [ ! -x "$CHECK_SCRIPT" ]; then
  fail "Check script missing or not executable: $CHECK_SCRIPT"
  exit 70
fi

if [ ! -x "$HERMES_PY" ]; then
  fail "Hermes python missing: $HERMES_PY"
  exit 71
fi

if [ ! -d "$REPO/packages/hermeskill-sdk" ] || [ ! -d "$REPO/packages/hermeskill-hermes" ]; then
  fail "Hermeskill repo missing packages: $REPO"
  exit 72
fi

# Verify we are NOT executing from inside the running gateway process.
# We do this conservatively: refuse to restart if our parent process
# chain leads to the gateway's main PID. We look up the gateway's
# MainPID first, then walk our parent chain.
GW_MAINPID="$(systemctl --user show "$GATEWAY_UNIT" --property=MainPID 2>/dev/null \
  | sed -E 's/^MainPID=//')"
if [ -n "$GW_MAINPID" ] && [ "$GW_MAINPID" != "0" ]; then
  # Walk our parent PIDs
  PARENT_PIDS="$$"
  CUR=$$
  while [ "$CUR" != "1" ] && [ "$CUR" != "0" ]; do
    CUR="$(awk '{print $4}' /proc/$CUR/stat 2>/dev/null || echo 0)"
    [ -z "$CUR" ] && break
    PARENT_PIDS="$PARENT_PIDS $CUR"
    # Safety bound
    if [ "${#PARENT_PIDS}" -gt 200 ]; then break; fi
  done
  for pid in $PARENT_PIDS; do
    if [ "$pid" = "$GW_MAINPID" ]; then
      fail "Refusing to restart gateway: this script is being executed from inside the gateway process tree."
      fail "Run this script from a separate shell (or via hermes-safe-update)."
      exit 73
    fi
  done
  ok "Not running inside the gateway process (gateway MainPID=$GW_MAINPID)"
fi

# ----------------------------------------------------------------------
# Step 2: Initial check
# ----------------------------------------------------------------------
hdr "Initial Check"
bash "$CHECK_SCRIPT"
INITIAL_CODE=$?
info "Initial check exit code: $INITIAL_CODE"

# Only proceed on 10 (recoverable editable-install loss).
# Anything else: stop and surface the verdict. Do NOT auto-modify.
if [ "$INITIAL_CODE" != "10" ]; then
  case "$INITIAL_CODE" in
    0)
      info "Initial state already healthy (code 0). Nothing to recover."
      exit 0
      ;;
    20)
      fail "Plugin failed to load (code 20). NOT auto-recoverable. Manual investigation required."
      ;;
    30)
      fail "Hook lifecycle incompatible (code 30). NOT auto-recoverable. Code update needed."
      ;;
    40)
      fail "Gateway not active (code 40). NOT auto-recoverable here."
      ;;
    50)
      fail "Policy abnormal (code 50). NOT auto-recoverable. Auto-policy-switch is forbidden."
      ;;
    60)
      fail "Repo / stable commit abnormal (code 60). NOT auto-recoverable. Local code may be altered."
      ;;
    *)
      fail "Unexpected initial code: $INITIAL_CODE. Refusing to proceed."
      ;;
  esac
  exit "$INITIAL_CODE"
fi

# ----------------------------------------------------------------------
# Step 3: Recoverable. Reinstall editable installs.
# ----------------------------------------------------------------------
hdr "Recovery: Reinstall Editable Installs"
cd "$REPO" || { fail "Cannot cd $REPO"; exit 74; }

info "Running: $HERMES_PY -m pip install -e ./packages/hermeskill-sdk -e ./packages/hermeskill-hermes"
if "$HERMES_PY" -m pip install \
    -e ./packages/hermeskill-sdk \
    -e ./packages/hermeskill-hermes; then
  ok "Editable reinstall completed"
else
  fail "Editable reinstall FAILED"
  exit 75
fi

# ----------------------------------------------------------------------
# Step 4: Restart gateway
# ----------------------------------------------------------------------
hdr "Restart Gateway"
info "systemctl --user restart $GATEWAY_UNIT"
if systemctl --user restart "$GATEWAY_UNIT"; then
  ok "Gateway restart command issued"
else
  fail "Gateway restart FAILED"
  exit 76
fi

# Give the gateway a moment to come back up. This is a bounded wait,
# not a polling loop.
info "Waiting up to 30s for gateway to reach running state..."
for i in $(seq 1 30); do
  STATE="$(systemctl --user show "$GATEWAY_UNIT" --property=SubState 2>/dev/null \
    | sed -E 's/^SubState=//')"
  if [ "$STATE" = "running" ]; then
    ok "Gateway running after ${i}s"
    break
  fi
  sleep 1
done
if [ "$STATE" != "running" ]; then
  fail "Gateway did not reach running state within 30s (SubState=$STATE)"
  warn "Recovery incomplete. Manual investigation required. Will NOT continue modifying state."
  exit 77
fi

# ----------------------------------------------------------------------
# Step 5: Re-check. If still failing, STOP — never loop, never modify.
# ----------------------------------------------------------------------
hdr "Post-Recovery Check"
bash "$CHECK_SCRIPT"
FINAL_CODE=$?
info "Final check exit code: $FINAL_CODE"

if [ "$FINAL_CODE" = "0" ]; then
  ok "Recovery SUCCESSFUL — integration healthy."
  exit 0
else
  fail "Recovery INCOMPLETE — final check returned $FINAL_CODE."
  warn "Stopping. Will NOT retry. Will NOT modify any other state."
  warn "Manual investigation required."
  exit "$FINAL_CODE"
fi