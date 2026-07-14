#!/usr/bin/env bash
# recover-hermeskill-integration.sh
#
# CONDITIONAL recovery — runs ONLY when
# check-hermes-update-compatibility.sh exits with code 10 (editable
# install lost or import paths wrong).
#
# Strictly enforced rules (per Master spec 2026-07-15):
#
#   1. Initial check exit code must be EXACTLY 10. Any other code →
#      refuse to act, log a report, exit that code.
#   2. The Hermeskill repo must exist at $REPO.
#   3. Both package directories must exist:
#        $REPO/packages/hermeskill-sdk
#        $REPO/packages/hermeskill-hermes
#      (If either is missing we cannot recover — log and stop.)
#   4. Git working tree is logged READ-ONLY. We do NOT git reset, git
#      checkout, or any destructive operation. If the tree is dirty,
#      we record it in the report and PROCEED — pip install -e works
#      fine with uncommitted changes.
#   5. We do NOT modify the gateway until pip install succeeds.
#   6. We do NOT run a second recovery if the post-check still fails.
#   7. If the official update wrapper invoked us, we must NOT call
#      `hermes update` again. We never reach outside the Hermeskill
#      install surface.
#
# This script is invoked by:
#   - ~/.local/bin/hermes-safe-update  (after a successful Hermes update)
#   - manually (by Master, in a separate shell — NOT inside the gateway
#     process tree)
#
# It is NEVER invoked by the daily health check. Daily health check
# is read-only and only notifies.

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_AGENT_DIR="/home/ai/.hermes/hermes-agent"
HERMES_PY="${HERMES_AGENT_DIR}/venv/bin/python"
GATEWAY_UNIT="hermes-gateway.service"
CHECK_SCRIPT="${REPO}/scripts/check-hermes-update-compatibility.sh"

SDK_DIR="${REPO}/packages/hermeskill-sdk"
HERMES_PLUGIN_DIR="${REPO}/packages/hermeskill-hermes"

REPORT_DIR="${HOME}/.hermeskill/update-reports"
REPORT_PATH=""

# Redaction patterns (must match check script's list).
REDACT_PATTERNS=(
  'sk_live_' 'sk_test_' 'sk-'
  'api_key=' 'apikey=' 'token=' 'access_token='
  'secret=' 'password=' 'passwd='
  'Bearer ' 'Authorization:'
  'wx_' 'telegram_bot_token' 'feishu_tenant_access_token'
)

redact_line() {
  local line="$1"
  local p
  for p in "${REDACT_PATTERNS[@]}"; do
    line="${line//${p}/[REDACTED]}"
  done
  printf '%s' "$line"
}

REPORT_TMP=""

# Output helpers — tee to REPORT_TMP if it's set, else plain stdout.
# Always define ONCE so they can be called before or after start_report.
ok()    { local m="$1"; printf "  ✓ %s\n" "$m" | tee -a "${REPORT_TMP:-/dev/null}"; }
fail()  { local m="$1"; printf "  ✗ %s\n" "$m" | tee -a "${REPORT_TMP:-/dev/null}"; }
warn()  { local m="$1"; printf "  ! %s\n" "$m" | tee -a "${REPORT_TMP:-/dev/null}"; }
info()  { local m="$1"; printf "  · %s\n" "$m" | tee -a "${REPORT_TMP:-/dev/null}"; }
hdr()   {
  local m="$1"
  {
    printf "\n%s\n" "$m"
    printf '%.s─' $(seq 1 ${#m})
    printf '\n'
  } | tee -a "${REPORT_TMP:-/dev/null}"
}

start_report() {
  local label="${1:-recovery}"
  mkdir -p "$REPORT_DIR" 2>/dev/null || true
  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  REPORT_PATH="${REPORT_DIR}/${ts}-${label}.log"
  REPORT_TMP="$(mktemp -t hkrecover.XXXXXX)"
  : > "$REPORT_TMP"
  : > "$REPORT_PATH"
}

end_report() {
  if [ -z "${REPORT_TMP:-}" ] || [ -z "${REPORT_PATH:-}" ]; then
    return 0
  fi
  {
    printf 'hermeskill recovery — %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'report path: %s\n' "${REPORT_PATH}"
    printf 'reporter: %s\n' "$(basename "$0")"
    printf -- '----------------------------------------\n'
    while IFS= read -r line; do
      redact_line "$line"
      printf '\n'
    done < "$REPORT_TMP"
  } > "$REPORT_PATH"
  rm -f "$REPORT_TMP"
  unset REPORT_TMP
  printf "  · Report: %s\n" "${REPORT_PATH}"
}

# ----------------------------------------------------------------------
# Step 0: Refuse to run inside the gateway's own process tree.
#
# A script invoked from the gateway cannot restart the gateway cleanly:
# SIGTERM sent to the gateway kills this script too. The wrapper
# (hermes-safe-update) is invoked from the user's shell, NOT from
# the gateway, so this check rejects only direct-from-gateway runs.
#
# Detection: if any ancestor process has the same PID as the systemd
# gateway's MainPID, refuse.
# ----------------------------------------------------------------------
start_report "recovery"
hdr "Preconditions: gateway-tree check"

# Helper: get the MainPID of the systemd --user gateway.
get_gateway_mainpid() {
  systemctl --user show "$GATEWAY_UNIT" --property=MainPID --value 2>/dev/null || echo ""
}

GW_MAINPID="$(get_gateway_mainpid)"
info "Gateway MainPID: ${GW_MAINPID:-<unknown>}"

INSIDE_GW=0
if [ -n "$GW_MAINPID" ] && [ "$GW_MAINPID" -gt 0 ]; then
  # Walk the process tree from $$ upward. If we hit the gateway's
  # MainPID, we are inside its process tree.
  CUR=$$
  while [ "$CUR" -gt 1 ]; do
    if [ "$CUR" = "$GW_MAINPID" ]; then
      INSIDE_GW=1
      break
    fi
    CUR="$(ps -o ppid= -p "$CUR" 2>/dev/null | tr -d ' ' || echo '')"
    [ -z "$CUR" ] && break
    [ "$CUR" = "0" ] && break
  done
fi

if [ "$INSIDE_GW" = "1" ]; then
  fail "Refusing to run: this script is being executed from inside the gateway process tree."
  fail "Run from a separate shell, or via hermes-safe-update."
  end_report
  exit 73
fi
ok "Not inside gateway process tree"

# ----------------------------------------------------------------------
# Step 1: Run the check script and parse its exit code STRICTLY.
# ----------------------------------------------------------------------
hdr "Step 1: Initial Check"
if [ ! -x "$CHECK_SCRIPT" ]; then
  fail "Check script not found or not executable: $CHECK_SCRIPT"
  end_report
  exit 74
fi

bash "$CHECK_SCRIPT"
INITIAL_CODE=$?
info "Initial check exit code: $INITIAL_CODE"

# Master spec point 7: "检查脚本退出码严格等于 10"
if [ "$INITIAL_CODE" -ne 10 ]; then
  hdr "Step 1 Result: Refusing to recover"
  case "$INITIAL_CODE" in
    0)  info "Initial state already healthy (code 0). Nothing to recover." ;;
    20) fail "Plugin failed to load (code 20). NOT auto-recoverable. Manual investigation required." ;;
    30) fail "Hook lifecycle incompatible (code 30). NOT auto-recoverable. Code update needed." ;;
    40) fail "Hermes Python missing or Gateway inactive (code 40). NOT auto-recoverable here." ;;
    50) fail "Policy abnormal (code 50). NOT auto-recoverable. Auto-policy-switch is forbidden." ;;
    60) fail "Repo / stable commit abnormal (code 60). NOT auto-recoverable. Local code may be altered." ;;
    *)  fail "Unexpected initial code: $INITIAL_CODE. Refusing to proceed." ;;
  esac
  end_report
  exit "$INITIAL_CODE"
fi

# ----------------------------------------------------------------------
# Step 2: Confirm we CAN recover. Per Master spec point 7:
#   - Hermeskill repo exists
#   - Two package dirs exist
#   - Git working tree is logged READ-ONLY (do NOT reset/checkout/overwrite)
# ----------------------------------------------------------------------
hdr "Step 2: Recovery Preconditions"

if [ ! -d "$REPO/.git" ]; then
  fail "Hermeskill repo missing or not a git repo: $REPO"
  end_report
  exit 75
fi
ok "Hermeskill repo present: $REPO"

if [ ! -d "$SDK_DIR" ]; then
  fail "Package dir missing: $SDK_DIR"
  end_report
  exit 75
fi
ok "Package dir present: $SDK_DIR"

if [ ! -d "$HERMES_PLUGIN_DIR" ]; then
  fail "Package dir missing: $HERMES_PLUGIN_DIR"
  end_report
  exit 75
fi
ok "Package dir present: $HERMES_PLUGIN_DIR"

# Git working tree — read-only snapshot. We do NOT clean.
info "Git working tree status (read-only — will NOT reset/checkout):"
git -C "$REPO" status --porcelain 2>/dev/null | sed 's/^/      /' | tee -a "${REPORT_TMP:-/dev/null}" || true
HEAD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
HEAD_SHORT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
info "HEAD: ${HEAD_SHORT} (${HEAD_SHA})"
info "Uncommitted changes are tolerated — pip install -e works fine with a dirty tree."

# ----------------------------------------------------------------------
# Step 3: Reinstall editable installs. This is the ONLY mutation.
# ----------------------------------------------------------------------
hdr "Step 3: Recovery — Reinstall Editable Installs"

cd "$REPO" || { fail "Cannot cd $REPO"; end_report; exit 76; }

if "$HERMES_PY" -m pip install -e ./packages/hermeskill-sdk -e ./packages/hermeskill-hermes 2>&1 | tee -a "${REPORT_TMP:-/dev/null}" | tail -20; then
  ok "Editable reinstall succeeded"
else
  fail "Editable reinstall FAILED"
  end_report
  exit 77
fi

# ----------------------------------------------------------------------
# Step 4: Restart gateway.
# ----------------------------------------------------------------------
hdr "Step 4: Restart Gateway"
info "systemctl --user restart $GATEWAY_UNIT"
if systemctl --user restart "$GATEWAY_UNIT" 2>&1 | tee -a "${REPORT_TMP:-/dev/null}"; then
  ok "Gateway restart command issued"
else
  fail "Gateway restart command returned non-zero"
  end_report
  exit 78
fi

# Give the gateway a moment to settle.
sleep 2

# ----------------------------------------------------------------------
# Step 5: Post-recovery check. If it still fails → STOP.
# ----------------------------------------------------------------------
hdr "Step 5: Post-Recovery Check"
bash "$CHECK_SCRIPT"
POST_CODE=$?
info "Post-recovery check exit code: $POST_CODE"

if [ "$POST_CODE" -eq 0 ]; then
  ok "Recovery successful. Integration is healthy."
  end_report
  exit 0
fi

# Per spec point 6 of recover rules: we do NOT retry. We do NOT modify
# unrelated state. We surface the failure.
fail "Post-recovery check returned $POST_CODE. Refusing to retry or modify further."
hdr "Recovery Outcome: FAILED (manual investigation required)"
end_report
exit "$POST_CODE"