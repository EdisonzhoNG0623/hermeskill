#!/usr/bin/env bash
# hermes-safe-status.sh
#
# Compact read-only production snapshot for the Hermeskill ↔ Hermes integration.
# It deliberately avoids deep checks, network calls, recovery, updates, tests,
# and history writes. Use hermes-safe-doctor for a full diagnostic.

set -u

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
REPO="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
HERMES_PY="${HOME}/.hermes/hermes-agent/venv/bin/python"
GATEWAY_UNIT="hermes-gateway.service"
CONFIG_FILE="${HOME}/.hermeskill/config.toml"
DOCTOR_HISTORY_DIR="${HOME}/.hermes/doctor-history"
UPDATE_HISTORY_DIR="${HOME}/.hermes/update-history"
STABLE_TAG="hermeskill-session-reset-fixed-20260715"

GATEWAY_STATUS="FAIL"
PLUGIN_STATUS="FAIL"
EDITABLE_STATUS="FAIL"
POLICY_VALUE="missing"
BRANCH="unknown"
COMMIT="unknown"
STABLE_TAG_VALUE="MISSING"
WORKING_TREE="unknown"
GATEWAY_PID="unknown"
GATEWAY_UPTIME="unknown"
LAST_DOCTOR="none"
LAST_UPDATE="none"
OVERALL="WARNING"
RUNTIME_CAP="unresolved"

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

latest_history_time() {
  local directory="$1"
  local file epoch latest_epoch=0
  if [ ! -d "${directory}" ] || [ ! -r "${directory}" ]; then
    printf 'none'
    return
  fi
  for file in "${directory}"/*.md; do
    [ -f "${file}" ] || continue
    epoch="$(stat -c %Y "${file}" 2>/dev/null || echo 0)"
    if [ "${epoch}" -gt "${latest_epoch}" ] 2>/dev/null; then
      latest_epoch="${epoch}"
    fi
  done
  if [ "${latest_epoch}" -gt 0 ] 2>/dev/null; then
    date -d "@${latest_epoch}" '+%Y-%m-%d %H:%M'
  else
    printf 'none'
  fi
}

# One systemd query supplies active state, process id, and activation time.
gateway_properties="$(systemctl --user show "${GATEWAY_UNIT}" \
  --property=ActiveState,MainPID,ActiveEnterTimestamp --no-pager 2>/dev/null)"
gateway_query_code=$?
gateway_active="$(printf '%s\n' "${gateway_properties}" | sed -n 's/^ActiveState=//p' | head -1)"
gateway_pid="$(printf '%s\n' "${gateway_properties}" | sed -n 's/^MainPID=//p' | head -1)"
gateway_started="$(printf '%s\n' "${gateway_properties}" | sed -n 's/^ActiveEnterTimestamp=//p' | head -1)"
GATEWAY_PID="${gateway_pid:-unknown}"
GATEWAY_UPTIME="$(format_uptime "${gateway_started:-}")"
if [ "${gateway_query_code}" -eq 0 ] && [ "${gateway_active}" = "active" ] \
  && [ "${gateway_pid:-0}" -gt 0 ] 2>/dev/null; then
  GATEWAY_STATUS="PASS"
fi

# One Python process verifies importability and PEP 660 metadata without
# loading PluginManager or interacting with Gateway/Control Plane.
python_probe=""
if [ -x "${HERMES_PY}" ]; then
  python_probe="$("${HERMES_PY}" - "${REPO}" <<'PY' 2>/dev/null
import json
import sys
from importlib.metadata import distribution

repo = sys.argv[1]
try:
    import hermeskill_hermes  # noqa: F401
    plugin = "PASS"
except Exception:
    plugin = "FAIL"

try:
    direct_url = json.loads(distribution("hermeskill").read_text("direct_url.json") or "{}")
    editable = direct_url.get("dir_info", {}).get("editable") is True
    location = direct_url.get("url", "")
    editable_status = "PASS" if editable and location.startswith("file://" + repo + "/") else "FAIL"
except Exception:
    editable_status = "FAIL"

print(f"{plugin} {editable_status}")
PY
)"
fi
read -r PLUGIN_STATUS EDITABLE_STATUS <<<"${python_probe:-FAIL FAIL}"

# The status command reads the selected policy; it does not resolve or alter it.
if [ -r "${CONFIG_FILE}" ]; then
  POLICY_VALUE="$(awk -F= '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*policy[[:space:]]*=/ {
      value=$2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      gsub(/^'"'"'|'"'"'$/, "", value)
      print value
      exit
    }
  ' "${CONFIG_FILE}")"
  POLICY_VALUE="${POLICY_VALUE:-missing}"
fi

# Resolve the selected policy in the production Hermes Python. This reads the
# SDK's sole shipped-policy source; it neither changes configuration nor starts
# a watcher.
if [ -x "${HERMES_PY}" ]; then
  RUNTIME_CAP="$("${HERMES_PY}" - "${POLICY_VALUE}" <<'PY' 2>/dev/null
import sys
from hermeskill.policies import resolve_policy

try:
    print(resolve_policy(sys.argv[1]).thresholds.max_runtime_seconds)
except Exception:
    print("unresolved")
PY
)"
  RUNTIME_CAP="${RUNTIME_CAP:-unresolved}"
fi

BRANCH="$(git -C "${REPO}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
COMMIT="$(git -C "${REPO}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if git -C "${REPO}" rev-parse -q --verify "refs/tags/${STABLE_TAG}" >/dev/null 2>&1; then
  STABLE_TAG_VALUE="${STABLE_TAG}"
fi
if git -C "${REPO}" diff --quiet --ignore-submodules -- 2>/dev/null \
  && git -C "${REPO}" diff --cached --quiet --ignore-submodules -- 2>/dev/null \
  && [ -z "$(git -C "${REPO}" ls-files --others --exclude-standard 2>/dev/null)" ]; then
  WORKING_TREE="clean"
else
  WORKING_TREE="dirty"
fi

LAST_DOCTOR="$(latest_history_time "${DOCTOR_HISTORY_DIR}")"
LAST_UPDATE="$(latest_history_time "${UPDATE_HISTORY_DIR}")"

if [ "${GATEWAY_STATUS}" = "PASS" ] \
  && [ "${PLUGIN_STATUS}" = "PASS" ] \
  && [ "${EDITABLE_STATUS}" = "PASS" ] \
  && [ "${POLICY_VALUE}" = "permissive" ] \
  && [ "${RUNTIME_CAP}" = "86400" ] \
  && [ "${WORKING_TREE}" = "clean" ] \
  && [ "${STABLE_TAG_VALUE}" != "MISSING" ]; then
  OVERALL="HEALTHY"
fi

printf 'Hermes Gateway : %s\n' "${GATEWAY_STATUS}"
printf 'Hermeskill     : %s\n' "${PLUGIN_STATUS}"
printf 'Editable       : %s\n' "${EDITABLE_STATUS}"
printf 'Policy         : %s\n' "${POLICY_VALUE}"
printf 'Runtime Cap    : %ss\n' "${RUNTIME_CAP}"
printf 'Branch         : %s\n' "${BRANCH}"
printf 'Commit         : %s\n' "${COMMIT}"
printf 'Stable Tag     : %s\n' "${STABLE_TAG_VALUE}"
printf 'Working Tree   : %s\n' "${WORKING_TREE}"
printf 'Gateway PID    : %s\n' "${GATEWAY_PID}"
printf 'Gateway Uptime : %s\n' "${GATEWAY_UPTIME}"
printf 'Doctor         : %s\n' "${LAST_DOCTOR}"
printf 'Update         : %s\n' "${LAST_UPDATE}"
printf 'Overall        : %s\n' "${OVERALL}"

if [ "${OVERALL}" = "HEALTHY" ]; then
  exit 0
fi
exit 1
