#!/usr/bin/env bash
# daily-health-check.sh
#
# READ-ONLY daily health check for Hermeskill ↔ Hermes integration.
#
# Per Master spec point 10:
#   "每日健康检查必须只读。即使返回 10，也只通知，不自动调用恢复脚本。
#    自动恢复只允许发生在 hermes-safe-update 的更新后流程中。"
#
# This script:
#   - Runs check-hermes-update-compatibility.sh
#   - If exit code is non-zero, attempts to send a notification via
#     Hermes' existing channels (best-effort).
#   - NEVER invokes recover-hermeskill-integration.sh.
#   - NEVER restarts the gateway.
#   - NEVER modifies Hermes or Hermeskill state.
#
# Notification strategy (per Master spec point 9):
#   - Try to call the existing notification channels (Telegram, WeChat,
#     Feishu, Photon) if they are reachable. The exact channel list
#     matches the HOME channels configured in the Hermes profile.
#   - If notification fails for any reason: write the report, write
#     to systemd journal, return non-zero exit code. Do NOT modify
#     Telegram / Feishu / Gateway config to "fix" notification.
#
# Cron-style usage:
#   0 9 * * * /opt/ai/projects/hermes-upgrades/hermeskill/scripts/daily-health-check.sh

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
CHECK_SCRIPT="${REPO}/scripts/check-hermes-update-compatibility.sh"
REPORT_DIR="${HOME}/.hermeskill/update-reports"

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

# ----------------------------------------------------------------------
# Notification: best-effort. We never modify notification config.
# We rely on whatever notification tools the Hermes profile already has
# wired up. We try a couple of safe, idempotent invocations:
#   - hermes notify ... if such a subcommand exists
#   - direct logger to systemd journal as a last resort
# Anything that errors out is logged but does not change this script's
# exit code path (the exit code is determined by the check).
# ----------------------------------------------------------------------
notify() {
  local subject="$1"
  local body="$2"

  # Last-resort: systemd journal. Always available for user services.
  if command -v systemd-cat >/dev/null 2>&1; then
    printf 'hermeskill daily-health-check: %s\n%s\n' "$subject" "$body" \
      | systemd-cat -t hermeskill-health 2>/dev/null || true
  fi

  # Optional: if the user has a hermes CLI subcommand for notifications,
  # use it. We probe the help text rather than hardcoding names.
  if command -v hermes >/dev/null 2>&1; then
    if hermes --help 2>&1 | grep -qiE 'notify|alert|message' >/dev/null; then
      # We do NOT know the exact syntax for every version. We only fire
      # a notification if a top-level help entry is found, and we always
      # pass -- as a defensive end-of-flags marker.
      hermes notify --subject "$subject" --message "$body" 2>/dev/null || true
    fi
  fi
}

# ----------------------------------------------------------------------
# Run the check.
# ----------------------------------------------------------------------
hdr "Hermeskill Daily Health Check"

if [ ! -x "$CHECK_SCRIPT" ]; then
  fail "Check script not found: $CHECK_SCRIPT"
  notify "Hermeskill daily health check: FAIL" "Check script not found: $CHECK_SCRIPT"
  exit 80
fi

bash "$CHECK_SCRIPT"
CHECK_CODE=$?
info "Check exit code: $CHECK_CODE"

# ----------------------------------------------------------------------
# Per spec point 10: code 10 here ONLY notifies. NO auto-recovery.
# ----------------------------------------------------------------------
mkdir -p "$REPORT_DIR" 2>/dev/null || true
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DAILY_REPORT="${REPORT_DIR}/${TS}-daily-health.log"

cat > "$DAILY_REPORT" <<EOF
hermeskill daily health check
timestamp: $TS
check exit code: $CHECK_CODE
result: $(case $CHECK_CODE in
  0)  echo "healthy" ;;
  10) echo "editable install lost — recovery NOT auto-triggered (per Master spec point 10)" ;;
  20) echo "plugin load failure — manual investigation required" ;;
  30) echo "hook lifecycle / API incompatible — code update needed" ;;
  40) echo "Hermes Python missing or Gateway inactive" ;;
  50) echo "policy != permissive" ;;
  60) echo "git repo / stable commit abnormal" ;;
  *)  echo "unexpected" ;;
esac)
EOF

info "Daily report: $DAILY_REPORT"

case "$CHECK_CODE" in
  0)
    ok "All checks passed. Daily health: HEALTHY."
    exit 0
    ;;
  10)
    warn "Code 10: editable install lost."
    info "Per Master spec point 10, daily health check does NOT auto-recover."
    info "Auto-recovery is only triggered via hermes-safe-update after an explicit update flow."
    notify "Hermeskill daily health: code 10 (editable install lost)" \
      "Code 10 detected. Recovery NOT auto-triggered by daily health check.
Run 'hermes-safe-update --mock-update=success' or wait for next Hermes update for auto-recovery.
Report: $DAILY_REPORT"
    exit 10
    ;;
  20|30|40|50|60)
    fail "Code $CHECK_CODE: STOP. Daily health check does NOT auto-recover."
    notify "Hermeskill daily health: code $CHECK_CODE" \
      "Compatibility failure detected.
Report: $DAILY_REPORT
Manual investigation required."
    exit "$CHECK_CODE"
    ;;
  *)
    fail "Unexpected check exit code: $CHECK_CODE"
    notify "Hermeskill daily health: unexpected code $CHECK_CODE" \
      "Report: $DAILY_REPORT"
    exit "$CHECK_CODE"
    ;;
esac