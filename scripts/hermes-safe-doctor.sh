#!/usr/bin/env bash
# hermes-safe-doctor.sh
#
# Production health diagnostic for the Hermeskill ↔ Hermes integration.
#
# This script is diagnostic-only. It never updates packages, edits settings,
# restarts services, recovers the editable install, or invokes any lifecycle
# action. Its only intentional write is the requested Markdown history record
# under ~/.hermes/doctor-history/.
#
# It delegates compatibility decisions to the existing compatibility script;
# it does not duplicate compatibility or recovery logic.

set -u

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
REPO="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BASELINE_SCRIPT="${SCRIPT_DIR}/show-production-baseline.sh"
COMPATIBILITY_SCRIPT="${SCRIPT_DIR}/check-hermes-update-compatibility.sh"
DAILY_HEALTH_SCRIPT="${SCRIPT_DIR}/daily-health-check.sh"
GATEWAY_UNIT="hermes-gateway.service"
CONTROL_PLANE_UNIT="hermeskill-control-plane.service"
DOCTOR_HISTORY_DIR="${HOME}/.hermes/doctor-history"
UPDATE_HISTORY_DIR="${HOME}/.hermes/update-history"

TMP_DIR="$(mktemp -d -t hermeskill-doctor.XXXXXX)" || {
  printf 'FATAL: unable to create temporary diagnostic directory\n' >&2
  exit 70
}
trap 'rm -rf "${TMP_DIR}"' EXIT

BASELINE_OUT="${TMP_DIR}/baseline.out"
COMPATIBILITY_OUT="${TMP_DIR}/compatibility.out"
DAILY_OUT="${TMP_DIR}/daily-health.out"
GATEWAY_OUT="${TMP_DIR}/gateway.out"
CONTROL_PLANE_OUT="${TMP_DIR}/control-plane.out"

BASELINE_CODE=70
COMPATIBILITY_CODE=70
DAILY_CODE=0
GATEWAY_CODE=70
CONTROL_PLANE_CODE=0
HISTORY_WRITE_CODE=0

GATEWAY_STATUS="FAIL"
PLUGIN_STATUS="FAIL"
EDITABLE_STATUS="FAIL"
COMPATIBILITY_STATUS="FAIL"
POLICY_STATUS="FAIL"
GIT_STATUS="FAIL"
CONTROL_PLANE_STATUS="SKIP"
UPDATE_HISTORY_STATUS="FAIL"
DAILY_STATUS="SKIP"
OVERALL="❌ UNHEALTHY"
FINAL_EXIT_CODE=70

run_and_capture() {
  local output_file="$1"
  shift
  "$@" >"${output_file}" 2>&1
  local rc=$?
  cat "${output_file}"
  return "${rc}"
}

status_from_compatibility_output() {
  if [ "${COMPATIBILITY_CODE}" -eq 0 ]; then
    COMPATIBILITY_STATUS="PASS"
  else
    COMPATIBILITY_STATUS="FAIL"
  fi

  if grep -q '^  ✓ PluginManager: hermeskill enabled=.*error=None' "${COMPATIBILITY_OUT}" \
    && ! grep -q 'PluginManager: hermeskill NOT registered' "${COMPATIBILITY_OUT}"; then
    PLUGIN_STATUS="PASS"
  else
    PLUGIN_STATUS="FAIL"
  fi

  if grep -q '^  ✓ pip show hermeskill is editable → ' "${COMPATIBILITY_OUT}"; then
    EDITABLE_STATUS="PASS"
  else
    EDITABLE_STATUS="FAIL"
  fi

  if grep -q '^  ✓ policy = permissive$' "${COMPATIBILITY_OUT}"; then
    POLICY_STATUS="PASS"
  else
    POLICY_STATUS="FAIL"
  fi

  # Git health belongs to the existing compatibility contract (code 60).
  # A dirty tree is intentionally reported by that script but is not silently
  # reclassified here into a new policy.
  if [ "${BASELINE_CODE}" -eq 0 ] && [ "${COMPATIBILITY_CODE}" -ne 60 ]; then
    GIT_STATUS="PASS"
  else
    GIT_STATUS="FAIL"
  fi
}

check_gateway_service() {
  local properties
  properties="$(systemctl --user show "${GATEWAY_UNIT}" \
    --property=ActiveState,MainPID,ActiveEnterTimestamp --no-pager 2>&1)"
  GATEWAY_CODE=$?
  printf '%s\n' "${properties}" >"${GATEWAY_OUT}"

  local active pid entered
  active="$(printf '%s\n' "${properties}" | sed -n 's/^ActiveState=//p' | head -1)"
  pid="$(printf '%s\n' "${properties}" | sed -n 's/^MainPID=//p' | head -1)"
  entered="$(printf '%s\n' "${properties}" | sed -n 's/^ActiveEnterTimestamp=//p' | head -1)"

  printf 'Active               : %s\n' "${active:-unknown}"
  printf 'PID                  : %s\n' "${pid:-unknown}"
  printf 'ActiveEnterTimestamp : %s\n' "${entered:-unknown}"

  if [ "${GATEWAY_CODE}" -eq 0 ] && [ "${active}" = "active" ] \
    && [ "${pid:-0}" -gt 0 ] 2>/dev/null; then
    GATEWAY_STATUS="PASS"
  else
    GATEWAY_STATUS="FAIL"
  fi
}

format_uptime() {
  local active_since="$1"
  local entered_epoch now_epoch seconds days hours minutes
  entered_epoch="$(date -d "${active_since}" +%s 2>/dev/null || echo 0)"
  now_epoch="$(date +%s)"
  if [ "${entered_epoch}" -le 0 ] 2>/dev/null || [ "${now_epoch}" -lt "${entered_epoch}" ] 2>/dev/null; then
    printf 'unknown'
    return
  fi
  seconds=$((now_epoch - entered_epoch))
  days=$((seconds / 86400))
  hours=$(((seconds % 86400) / 3600))
  minutes=$(((seconds % 3600) / 60))
  printf '%sd %sh %sm' "${days}" "${hours}" "${minutes}"
}

check_control_plane_service() {
  local load_state properties active pid entered uptime
  load_state="$(systemctl show "${CONTROL_PLANE_UNIT}" --property=LoadState --value --no-pager 2>/dev/null)"
  local load_rc=$?

  if [ "${load_rc}" -ne 0 ] || [ -z "${load_state}" ] || [ "${load_state}" = "not-found" ]; then
    printf 'Control plane service is not installed; skipped.\n' | tee "${CONTROL_PLANE_OUT}"
    CONTROL_PLANE_STATUS="SKIP"
    CONTROL_PLANE_CODE=0
    return
  fi

  properties="$(systemctl show "${CONTROL_PLANE_UNIT}" \
    --property=ActiveState,MainPID,ActiveEnterTimestamp --no-pager 2>&1)"
  CONTROL_PLANE_CODE=$?
  printf '%s\n' "${properties}" >"${CONTROL_PLANE_OUT}"

  active="$(printf '%s\n' "${properties}" | sed -n 's/^ActiveState=//p' | head -1)"
  pid="$(printf '%s\n' "${properties}" | sed -n 's/^MainPID=//p' | head -1)"
  entered="$(printf '%s\n' "${properties}" | sed -n 's/^ActiveEnterTimestamp=//p' | head -1)"
  uptime="$(format_uptime "${entered:-}")"

  printf 'Active : %s\n' "${active:-unknown}"
  printf 'PID    : %s\n' "${pid:-unknown}"
  printf 'Uptime : %s\n' "${uptime}"

  if [ "${CONTROL_PLANE_CODE}" -eq 0 ] && [ "${active}" = "active" ] \
    && [ "${pid:-0}" -gt 0 ] 2>/dev/null; then
    CONTROL_PLANE_STATUS="PASS"
  else
    CONTROL_PLANE_STATUS="FAIL"
  fi
}

check_update_history() {
  if [ -d "${UPDATE_HISTORY_DIR}" ] && [ -r "${UPDATE_HISTORY_DIR}" ]; then
    UPDATE_HISTORY_STATUS="PASS"
  else
    UPDATE_HISTORY_STATUS="FAIL"
  fi
}

# Keep the final health policy centralized. Critical integration failures are
# unhealthy; optional operational history/control-plane failures are warnings.
evaluate_overall() {
  if [ "${GATEWAY_STATUS}" = "FAIL" ] \
    || [ "${PLUGIN_STATUS}" = "FAIL" ] \
    || [ "${EDITABLE_STATUS}" = "FAIL" ] \
    || [ "${COMPATIBILITY_STATUS}" = "FAIL" ] \
    || [ "${POLICY_STATUS}" = "FAIL" ] \
    || [ "${GIT_STATUS}" = "FAIL" ]; then
    OVERALL="❌ UNHEALTHY"
    return
  fi

  if [ "${CONTROL_PLANE_STATUS}" = "FAIL" ] \
    || [ "${UPDATE_HISTORY_STATUS}" = "FAIL" ] \
    || [ "${DAILY_STATUS}" = "FAIL" ]; then
    OVERALL="⚠ WARNING"
    return
  fi

  OVERALL="✅ HEALTHY"
}

# Exit codes retain existing compatibility semantics where possible. Diagnostic
# infrastructure failures receive doctor-only codes; no nonzero code triggers
# recovery from this script.
evaluate_exit_code() {
  if [ "${COMPATIBILITY_CODE}" -ne 0 ]; then
    FINAL_EXIT_CODE="${COMPATIBILITY_CODE}"
  elif [ "${GATEWAY_STATUS}" = "FAIL" ]; then
    FINAL_EXIT_CODE=40
  elif [ "${CONTROL_PLANE_STATUS}" = "FAIL" ]; then
    FINAL_EXIT_CODE=71
  elif [ "${UPDATE_HISTORY_STATUS}" = "FAIL" ] || [ "${DAILY_STATUS}" = "FAIL" ]; then
    FINAL_EXIT_CODE=72
  elif [ "${HISTORY_WRITE_CODE}" -ne 0 ]; then
    FINAL_EXIT_CODE=73
  else
    FINAL_EXIT_CODE=0
  fi
}

print_summary() {
  cat <<EOF
================================================
Production Health Summary
Gateway              ${GATEWAY_STATUS}
Hermeskill Plugin    ${PLUGIN_STATUS}
Editable Install     ${EDITABLE_STATUS}
Compatibility        ${COMPATIBILITY_STATUS}
Policy               ${POLICY_STATUS}
Git                  ${GIT_STATUS}
Control Plane        ${CONTROL_PLANE_STATUS}
Update History       ${UPDATE_HISTORY_STATUS}
Overall              ${OVERALL}
EOF
}

write_history() {
  local timestamp_local timestamp_file history_file history_tmp
  timestamp_local="$(date '+%Y-%m-%d %H:%M:%S %z')"
  timestamp_file="$(date '+%Y-%m-%d-%H%M')"
  history_file="${DOCTOR_HISTORY_DIR}/${timestamp_file}.md"

  mkdir -p "${DOCTOR_HISTORY_DIR}" 2>/dev/null || return 1
  history_tmp="$(mktemp "${DOCTOR_HISTORY_DIR}/.hermes-safe-doctor.XXXXXX")" || return 1

  {
    printf '# Hermeskill Production Health Doctor — %s\n\n' "${timestamp_local}"
    printf '## Run Metadata\n\n'
    printf '| Field | Value |\n|---|---|\n'
    printf '| Time (local) | `%s` |\n' "${timestamp_local}"
    printf '| Repository | `%s` |\n' "${REPO}"
    printf '| Doctor exit code | `%s` |\n\n' "${FINAL_EXIT_CODE}"

    printf '## Summary\n\n'
    printf '| Check | Result |\n|---|---|\n'
    printf '| Gateway | `%s` |\n' "${GATEWAY_STATUS}"
    printf '| Hermeskill Plugin | `%s` |\n' "${PLUGIN_STATUS}"
    printf '| Editable Install | `%s` |\n' "${EDITABLE_STATUS}"
    printf '| Compatibility | `%s` |\n' "${COMPATIBILITY_STATUS}"
    printf '| Policy | `%s` |\n' "${POLICY_STATUS}"
    printf '| Git | `%s` |\n' "${GIT_STATUS}"
    printf '| Control Plane | `%s` |\n' "${CONTROL_PLANE_STATUS}"
    printf '| Update History | `%s` |\n' "${UPDATE_HISTORY_STATUS}"
    printf '| Overall | **%s** |\n\n' "${OVERALL}"

    printf '## Step 1 — Production Baseline (exit `%s`)\n\n```text\n' "${BASELINE_CODE}"
    cat "${BASELINE_OUT}"
    printf '\n```\n\n'

    printf '## Step 2 — Compatibility (exit `%s`)\n\n```text\n' "${COMPATIBILITY_CODE}"
    cat "${COMPATIBILITY_OUT}"
    printf '\n```\n\n'

    printf '## Step 3 — Daily Health (exit `%s`)\n\n```text\n' "${DAILY_CODE}"
    cat "${DAILY_OUT}"
    printf '\n```\n\n'

    printf '## Step 4 — Gateway Service (exit `%s`)\n\n```text\n' "${GATEWAY_CODE}"
    cat "${GATEWAY_OUT}"
    printf '\n```\n\n'

    printf '## Step 5 — Control Plane Service (exit `%s`)\n\n```text\n' "${CONTROL_PLANE_CODE}"
    cat "${CONTROL_PLANE_OUT}"
    printf '\n```\n\n'

    printf '%s\n' '---'
    printf '_Generated by `hermes-safe-doctor`; it performs no updates, recovery, restart, configuration, policy, capability, lifecycle, or runtime changes._\n'
  } >"${history_tmp}"

  mv -f "${history_tmp}" "${history_file}" || {
    rm -f "${history_tmp}"
    return 1
  }
  printf 'Doctor history        : %s\n' "${history_file}"
  return 0
}

printf '================================================\n'
printf 'Hermeskill Production Health Doctor (read-only)\n'
printf '================================================\n'

printf '\nStep 1 — Production baseline\n'
if [ -f "${BASELINE_SCRIPT}" ]; then
  run_and_capture "${BASELINE_OUT}" bash "${BASELINE_SCRIPT}"
  BASELINE_CODE=$?
else
  printf 'Missing baseline script: %s\n' "${BASELINE_SCRIPT}" | tee "${BASELINE_OUT}"
  BASELINE_CODE=70
fi

printf '\nStep 2 — Compatibility\n'
if [ -f "${COMPATIBILITY_SCRIPT}" ]; then
  run_and_capture "${COMPATIBILITY_OUT}" bash "${COMPATIBILITY_SCRIPT}"
  COMPATIBILITY_CODE=$?
else
  printf 'Missing compatibility script: %s\n' "${COMPATIBILITY_SCRIPT}" | tee "${COMPATIBILITY_OUT}"
  COMPATIBILITY_CODE=70
fi
status_from_compatibility_output

printf '\nStep 3 — Daily health\n'
if [ -f "${DAILY_HEALTH_SCRIPT}" ]; then
  run_and_capture "${DAILY_OUT}" bash "${DAILY_HEALTH_SCRIPT}"
  DAILY_CODE=$?
  if [ "${DAILY_CODE}" -eq 0 ]; then
    DAILY_STATUS="PASS"
  else
    DAILY_STATUS="FAIL"
  fi
else
  printf 'daily-health-check.sh not present; skipped.\n' | tee "${DAILY_OUT}"
  DAILY_CODE=0
  DAILY_STATUS="SKIP"
fi

printf '\nStep 4 — Gateway service\n'
check_gateway_service >>"${GATEWAY_OUT}"
cat "${GATEWAY_OUT}"

printf '\nStep 5 — Control plane service\n'
check_control_plane_service >>"${CONTROL_PLANE_OUT}"
cat "${CONTROL_PLANE_OUT}"

check_update_history
evaluate_overall
evaluate_exit_code
printf '\n'
print_summary

if ! write_history; then
  HISTORY_WRITE_CODE=1
  evaluate_exit_code
  printf 'WARNING: unable to write doctor history under %s\n' "${DOCTOR_HISTORY_DIR}" >&2
  printf 'Final Exit Code       : %s\n' "${FINAL_EXIT_CODE}"
  exit "${FINAL_EXIT_CODE}"
fi

printf 'Final Exit Code       : %s\n' "${FINAL_EXIT_CODE}"
exit "${FINAL_EXIT_CODE}"
