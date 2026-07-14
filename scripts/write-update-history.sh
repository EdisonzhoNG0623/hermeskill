#!/usr/bin/env bash
# write-update-history.sh
#
# Phase 1 — Update History
#
# Invoked by hermes-safe-update after a successful update flow.
# Generates a single, self-contained Markdown record of what happened.
# Does NOT depend on the console log being available.
#
# Output: ~/.hermes/update-history/YYYY-MM-DD-HHMM.md
#
# All inputs are passed via environment variables (set by the caller).
# The script is pure shell; it never invokes the Hermes python, never
# touches git, never restarts anything.

set -u

# ----------------------------------------------------------------------------
# Input contract (all env vars, all optional; safe defaults applied).
# ----------------------------------------------------------------------------
HK_TS_LOCAL="$(date '+%Y-%m-%d %H:%M:%S %z')"
HK_DATE_LOCAL="$(date '+%Y-%m-%d-%H%M')"

HERMES_VERSION_BEFORE="${HERMES_VERSION_BEFORE:-unknown}"
HERMES_VERSION_AFTER="${HERMES_VERSION_AFTER:-unknown}"
HERMES_HEAD_BEFORE="${HERMES_HEAD_BEFORE:-unknown}"
HERMES_HEAD_AFTER="${HERMES_HEAD_AFTER:-unknown}"

HK_HEAD="${HK_HEAD:-unknown}"
HK_BRANCH="${HK_BRANCH:-unknown}"
HK_TAG="${HK_TAG:-none}"
HK_COMMIT_AT_TAG="${HK_COMMIT_AT_TAG:-unknown}"

GW_PID_BEFORE="${GW_PID_BEFORE:-unknown}"
GW_PID_AFTER="${GW_PID_AFTER:-unknown}"
GW_ENTER_TIMESTAMP="${GW_ENTER_TIMESTAMP:-unknown}"

HK_SDK_PATH="${HK_SDK_PATH:-unknown}"
HK_PLUGIN_PATH="${HK_PLUGIN_PATH:-unknown}"
EDITABLE_OK="${EDITABLE_OK:-unknown}"
PM_STATUS="${PM_STATUS:-unknown}"

CHECK_EXIT_CODE="${CHECK_EXIT_CODE:-?}"
RECOVERY_PERFORMED="${RECOVERY_PERFORMED:-false}"
FINAL_STATUS="${FINAL_STATUS:-PASS}"

# ----------------------------------------------------------------------------
# Redaction: scrub any secret-looking substring before writing to the file.
# ----------------------------------------------------------------------------
redact() {
  local s="${1:-}"
  for p in 'sk_live_' 'sk_test_' 'sk-' 'api_key=' 'apikey=' \
           'token=' 'access_token=' 'secret=' 'password=' 'passwd=' \
           'Bearer ' 'Authorization:' 'wx_' 'telegram_bot_token' \
           'feishu_tenant_access_token'; do
    s="${s//${p}/[REDACTED]}"
  done
  printf '%s' "$s"
}

redact_all() {
  while IFS= read -r line; do
    redact "$line"
    printf '\n'
  done
}

# ----------------------------------------------------------------------------
# Output target.
# ----------------------------------------------------------------------------
HISTORY_DIR="${HOME}/.hermes/update-history"
mkdir -p "${HISTORY_DIR}" 2>/dev/null || {
  printf 'FATAL: cannot create %s\n' "${HISTORY_DIR}" >&2
  exit 1
}

HISTORY_FILE="${HISTORY_DIR}/${HK_DATE_LOCAL}.md"

# ----------------------------------------------------------------------------
# Compose the Markdown report. Single-pass, no temp files, no dependencies.
# ----------------------------------------------------------------------------
{
  printf '# Hermeskill Update History — %s\n\n' "${HK_TS_LOCAL}"

  printf '## Summary\n\n'
  printf '| Field | Value |\n'
  printf '|-------|-------|\n'
  printf '| Update time (local) | `%s` |\n' "$(redact "${HK_TS_LOCAL}")"
  printf '| Final status | `%s` |\n' "${FINAL_STATUS}"
  printf '| Recovery performed | `%s` |\n' "${RECOVERY_PERFORMED}"
  printf '| Check exit code | `%s` |\n' "${CHECK_EXIT_CODE}"
  printf '| Report path | *(embedded in next line — see `update-reports/` for full redaction-safe log)* |\n'
  printf '\n'

  printf '## Hermes Version\n\n'
  printf '| Stage | Version | Git HEAD |\n'
  printf '|-------|---------|----------|\n'
  printf '| Before update | `%s` | `%s` |\n' \
    "$(redact "${HERMES_VERSION_BEFORE}")" "$(redact "${HERMES_HEAD_BEFORE}")"
  printf '| After update  | `%s` | `%s` |\n' \
    "$(redact "${HERMES_VERSION_AFTER}")" "$(redact "${HERMES_HEAD_AFTER}")"
  printf '\n'

  printf '## Hermeskill State\n\n'
  printf '| Field | Value |\n'
  printf '|-------|-------|\n'
  printf '| HEAD | `%s` |\n' "$(redact "${HK_HEAD}")"
  printf '| Branch | `%s` |\n' "$(redact "${HK_BRANCH}")"
  printf '| Stable tag | `%s` |\n' "$(redact "${HK_TAG}")"
  printf '| Tag points at | `%s` |\n' "$(redact "${HK_COMMIT_AT_TAG}")"
  printf '| SDK import path | `%s` |\n' "$(redact "${HK_SDK_PATH}")"
  printf '| Plugin import path | `%s` |\n' "$(redact "${HK_PLUGIN_PATH}")"
  printf '| Editable install | `%s` |\n' "${EDITABLE_OK}"
  printf '| PluginManager | `%s` |\n' "$(redact "${PM_STATUS}")"
  printf '\n'

  printf '## Gateway\n\n'
  printf '| Field | Value |\n'
  printf '|-------|-------|\n'
  printf '| MainPID before | `%s` |\n' "${GW_PID_BEFORE}"
  printf '| MainPID after | `%s` |\n' "${GW_PID_AFTER}"
  printf '| ActiveEnterTimestamp | `%s` |\n' "$(redact "${GW_ENTER_TIMESTAMP}")"
  printf '\n'

  printf '## Outcome\n\n'
  case "${FINAL_STATUS}" in
    PASS)
      printf 'Update completed successfully. '
      if [ "${RECOVERY_PERFORMED}" = "true" ]; then
        printf 'Auto-recovery ran (code 10 → reinstalled editable).\n'
      else
        printf 'No auto-recovery needed.\n'
      fi
      ;;
    FAIL)
      printf 'Update completed with non-zero final code. '
      printf 'See `update-reports/` for full redaction-safe log.\n'
      ;;
    *)
      printf 'Final status: %s\n' "${FINAL_STATUS}"
      ;;
  esac
  printf '\n'

  printf '%s\n' '---'
  printf '%s\n' '_Generated by write-update-history.sh — no console log dependency._'
} > "${HISTORY_FILE}"

# ----------------------------------------------------------------------------
# Sanity: ensure file was actually written.
# ----------------------------------------------------------------------------
if [ ! -s "${HISTORY_FILE}" ]; then
  printf 'FATAL: history file not written: %s\n' "${HISTORY_FILE}" >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# Notify the console where the file went. This is the only stdout we print.
# ----------------------------------------------------------------------------
printf '  · History: %s\n' "${HISTORY_FILE}"

exit 0