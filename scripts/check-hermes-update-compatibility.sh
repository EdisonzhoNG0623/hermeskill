#!/usr/bin/env bash
# check-hermes-update-compatibility.sh
#
# READ-ONLY compatibility check: is Hermeskill still correctly integrated
# into the production Hermes Agent install? Designed to be run after any
# Hermes update or venv rebuild, and as the basis for the daily health
# cron.
#
# Strictly informational. Never installs, restarts, edits configs, or
# modifies Git state.
#
# Exit codes — IMPORTANT: recover-hermeskill-integration.sh only acts on
# exit code 10. Every other non-zero code is a HARD STOP requiring human
# review. Precedence is 60 > 50 > 40 > 30 > 20 > 10.
#
#   0  Healthy — full integration confirmed.
#  10  Editable install lost or import paths wrong — RECOVERABLE.
#      recover-hermeskill-integration.sh will reinstall editable.
#  20  Plugin entry exists in PluginManager but failed to load (e.g.
#      enabled but error != None), OR log patterns indicate load-time
#      failures since the current Gateway start.
#  30  Hermes plugin API / hook lifecycle appears incompatible
#      (e.g. required hooks missing, lifecycle/unsupported-hook errors
#      in current-session logs).
#  40  Hermes production Python missing OR Gateway service is not active.
#  50  Hermeskill policy != "permissive".
#  60  Hermeskill git repository or stable commit/tag is in an
#      unexpected state.

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_AGENT_DIR="/home/ai/.hermes/hermes-agent"
HERMES_PY="${HERMES_AGENT_DIR}/venv/bin/python"
CONFIG_FILE="${HOME}/.hermeskill/config.toml"
GATEWAY_UNIT="hermes-gateway.service"

# Per the spec, 07139f8 is the stable core fix that must be an ancestor
# of HEAD. 79cf830 is a historical baseline — NOT a future-HEAD constraint.
CORE_FIX_COMMIT="07139f8f27c9e036475e47f06a1d00d99e142575"
STABLE_TAG="hermeskill-session-reset-fixed-20260715"

REQUIRED_HOOKS="pre_tool_call post_tool_call pre_llm_call post_api_request on_session_reset on_session_end on_session_finalize"

# Reports directory. Created lazily. One report per run, UTC-timestamped.
REPORT_DIR="${HOME}/.hermeskill/update-reports"

# Redaction: any of these substrings in a line gets the line replaced.
# Keep this list narrow on purpose — better to over-log than to nuke
# useful diagnostic context.
REDACT_PATTERNS=(
  'sk_live_'
  'sk_test_'
  'sk-'
  'api_key='
  'apikey='
  'token='
  'access_token='
  'secret='
  'password='
  'passwd='
  'Bearer '
  'Authorization:'
  'wx_'
  'telegram_bot_token'
  'feishu_tenant_access_token'
)

if [ -t 1 ]; then
  C_OK="\033[32m"; C_FAIL="\033[31m"; C_WARN="\033[33m"; C_INFO="\033[36m"; C_RST="\033[0m"
else
  C_OK=""; C_FAIL=""; C_WARN=""; C_INFO=""; C_RST=""
fi

ok()    { local m="$1"; printf "  ✓ %s\n" "$m" | tee -a "$REPORT_TMP"; }
fail()  { local m="$1"; printf "  ✗ %s\n" "$m" | tee -a "$REPORT_TMP"; }
warn()  { local m="$1"; printf "  ! %s\n" "$m" | tee -a "$REPORT_TMP"; }
info()  { local m="$1"; printf "  · %s\n" "$m" | tee -a "$REPORT_TMP"; }
hdr()   {
  local m="$1"
  {
    printf "\n%s\n" "$m"
    printf '%.s─' $(seq 1 ${#m})
    printf '\n'
  } | tee -a "$REPORT_TMP"
}

# ----------------------------------------------------------------------
# Report writer. Captures everything we print, with redaction, plus
# contextual metadata, to ~/.hermeskill/update-reports/UTC-<name>.log.
# Returns the report path on stdout for callers that want to embed it.
# ----------------------------------------------------------------------
REPORT_PATH=""
REPORT_TMP=""

# Redact one line. We deliberately do NOT blank the whole line — we
# replace sensitive substrings with [REDACTED] so the surrounding
# structure (timestamps, source components, line numbers) is preserved.
redact_line() {
  local line="$1"
  local p
  for p in "${REDACT_PATTERNS[@]}"; do
    line="${line//${p}/[REDACTED]}"
  done
  printf '%s' "$line"
}

start_report() {
  local label="${1:-compatibility-check}"
  mkdir -p "$REPORT_DIR" 2>/dev/null || true
  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  REPORT_PATH="${REPORT_DIR}/${ts}-${label}.log"
  # Tee everything we print to a tmp file so we can redact at the end.
  REPORT_TMP="$(mktemp -t hkcheck.XXXXXX)"
  : > "$REPORT_TMP"
  # Truncate report file; we'll cat the redacted tmp into it at end_report.
  : > "$REPORT_PATH"
}

end_report() {
  if [ -z "${REPORT_TMP:-}" ] || [ -z "${REPORT_PATH:-}" ]; then
    return 0
  fi
  # Build the report with header + redacted body. The header includes
  # the report path itself so the file is self-describing.
  {
    printf 'hermeskill compatibility check — %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'report path: %s\n' "${REPORT_PATH}"
    printf 'exit code: %s\n' "${FINAL_CODE:-0}"
    printf 'host: %s\n' "$(hostname)"
    printf 'reporter: %s\n' "$(basename "$0")"
    printf -- '----------------------------------------\n'
    while IFS= read -r line; do
      redact_line "$line"
      printf '\n'
    done < "$REPORT_TMP"
  } > "$REPORT_PATH"
  rm -f "$REPORT_TMP"
  REPORT_TMP=""
  printf "  · Report: %s\n" "${REPORT_PATH}"
}

# Override printf-based helpers so all output is also captured.
# (Functions already defined above; this is a no-op marker for clarity.)

# ----------------------------------------------------------------------
# Section-local exit codes. We track per-section so the precedence
# resolution at the end picks 60 > 50 > 40 > 30 > 20 > 10.
# ----------------------------------------------------------------------
CODE_60=0  # git repo / stable commit
CODE_50=0  # policy
CODE_40=0  # Hermes Python missing OR Gateway inactive
CODE_30=0  # hooks missing / API incompatible
CODE_20=0  # plugin load failure / load-time log patterns
CODE_10=0  # editable install / import paths

# Note: 40 has dual trigger — Hermes Python missing OR Gateway inactive.
# Both set CODE_40. The precedence at the end picks whichever is highest
# already set.
set_60() { if [ "$1" -gt "$CODE_60" ]; then CODE_60=$1; fi; }
set_50() { if [ "$1" -gt "$CODE_50" ]; then CODE_50=$1; fi; }
set_40() { if [ "$1" -gt "$CODE_40" ]; then CODE_40=$1; fi; }
set_30() { if [ "$1" -gt "$CODE_30" ]; then CODE_30=$1; fi; }
set_20() { if [ "$1" -gt "$CODE_20" ]; then CODE_20=$1; fi; }
set_10() { if [ "$1" -gt "$CODE_10" ]; then CODE_10=$1; fi; }

# Final precedence resolver. Returns the highest code in priority order:
# 60 > 50 > 40 > 30 > 20 > 10.
final_code() {
  if [ "$CODE_60" -gt 0 ]; then echo 60; return; fi
  if [ "$CODE_50" -gt 0 ]; then echo 50; return; fi
  if [ "$CODE_40" -gt 0 ]; then echo 40; return; fi
  if [ "$CODE_30" -gt 0 ]; then echo 30; return; fi
  if [ "$CODE_20" -gt 0 ]; then echo 20; return; fi
  if [ "$CODE_10" -gt 0 ]; then echo 10; return; fi
  echo 0
}

# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
start_report "compatibility-check"

hdr "0. Hermeskill Repository State (code 60)"
if [ ! -d "$REPO/.git" ]; then
  fail "Not a git repo: $REPO"
  set_60 60
else
  BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  HEAD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
  HEAD_SHORT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if [ "$BRANCH" = "main" ]; then
    ok "Branch: main"
  else
    fail "Branch not main: $BRANCH"
    set_60 60
  fi
  info "HEAD: ${HEAD_SHORT}"
  info "Working tree status:"
  git -C "$REPO" status --porcelain 2>/dev/null | sed 's/^/      /' | tee -a "$REPORT_TMP" || true

  # Per spec: 07139f8 must be an ancestor of HEAD. 79cf830 is NOT a
  # future-HEAD constraint.
  if git -C "$REPO" merge-base --is-ancestor "$CORE_FIX_COMMIT" HEAD 2>/dev/null; then
    ok "Core fix ${CORE_FIX_COMMIT:0:7} is ancestor of HEAD"
  else
    fail "Core fix ${CORE_FIX_COMMIT:0:7} is NOT an ancestor of HEAD (${HEAD_SHORT})"
    set_60 60
  fi

  # Tag must exist AND point at the core fix.
  TAG_SHA="$(git -C "$REPO" rev-parse "${STABLE_TAG}^{commit}" 2>/dev/null || echo '')"
  if [ -z "$TAG_SHA" ]; then
    fail "Stable tag missing: ${STABLE_TAG}"
    set_60 60
  elif [ "$TAG_SHA" != "$CORE_FIX_COMMIT" ]; then
    fail "Stable tag ${STABLE_TAG} points at ${TAG_SHA:0:7} (expected ${CORE_FIX_COMMIT:0:7})"
    set_60 60
  else
    ok "Stable tag ${STABLE_TAG} → ${TAG_SHA:0:7}"
  fi
fi

hdr "1. Hermes Production Python (early bail — code 40)"
if [ ! -x "$HERMES_PY" ]; then
  fail "Hermes production python missing or not executable: $HERMES_PY"
  fail "Cannot run any python-dependent checks. Returning 40 immediately."
  set_40 40
  FINAL_CODE="$(final_code)"
  hdr "Summary"
  case "$FINAL_CODE" in
    0)  ok "All checks passed (only git/policy/gateway ran; python missing)." ;;
    40) fail "Hermes production python missing — code 40. Recovery NOT auto-triggered." ;;
    *)  fail "Composite code: $FINAL_CODE" ;;
  esac
  end_report
  exit "$FINAL_CODE"
fi
ok "Hermes python present: $HERMES_PY"
PY_VERSION="$("$HERMES_PY" --version 2>&1 | head -1 || echo unknown)"
info "$PY_VERSION"

# ----------------------------------------------------------------------
# 2-5. Editable install + import paths + entry point (code 10)
# ----------------------------------------------------------------------
hdr "2-5. Editable Install & Plugin Discovery (code 10)"

HK_FILE="$("$HERMES_PY" -c 'import hermeskill, os; print(os.path.realpath(hermeskill.__file__))' 2>/dev/null || echo '')"
HH_FILE="$("$HERMES_PY" -c 'import hermeskill_hermes, os; print(os.path.realpath(hermeskill_hermes.__file__))' 2>/dev/null || echo '')"

if [ -z "$HK_FILE" ]; then
  fail "import hermeskill FAILED"
  set_10 10
elif [[ "$HK_FILE" != "${REPO}/packages/hermeskill-sdk/"* ]]; then
  fail "hermeskill resolves outside repo: $HK_FILE"
  set_10 10
else
  ok "hermeskill → $HK_FILE"
fi

if [ -z "$HH_FILE" ]; then
  fail "import hermeskill_hermes FAILED"
  set_10 10
elif [[ "$HH_FILE" != "${REPO}/packages/hermeskill-hermes/"* ]]; then
  fail "hermeskill_hermes resolves outside repo: $HH_FILE"
  set_10 10
else
  ok "hermeskill_hermes → $HH_FILE"
fi

# Entry-point probe via importlib.metadata.
EP_JSON="$("$HERMES_PY" - <<'PY' 2>/dev/null
import json
try:
    from importlib.metadata import entry_points
    eps = entry_points(group='hermes_agent.plugins')
    matches = [ep for ep in eps if ep.name == 'hermeskill']
    if not matches:
        print(json.dumps({"ok": False, "error": "no entry point"}))
    else:
        ep = matches[0]
        print(json.dumps({"ok": True, "value": ep.value, "dist": ep.dist.name, "total": len(eps)}))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
PY
)"

if ! printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  fail "Entry-point probe parse failed: ${EP_JSON:0:200}"
  set_10 10
else
  EP_OK="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read())["ok"])')"
  EP_VAL="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("value",""))')"
  EP_DIST="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("dist",""))')"
  EP_TOTAL="$(printf '%s' "$EP_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("total","?"))')"
  if [ "$EP_OK" = "True" ] && [ "$EP_VAL" = "hermeskill_hermes" ]; then
    ok "Entry-point: hermes_agent.plugins / hermeskill → ${EP_VAL} (dist: ${EP_DIST}, total plugins: ${EP_TOTAL})"
  else
    fail "Entry-point probe reports: ok=${EP_OK}, value='${EP_VAL}'"
    set_10 10
  fi
fi

# ----------------------------------------------------------------------
# 6. PluginManager introspection (code 20 / 30)
# ----------------------------------------------------------------------
hdr "6. PluginManager (code 20 plugin error / code 30 plugin not registered)"
PM_JSON="$("$HERMES_PY" - <<'PY' 2>/dev/null
import json
try:
    from hermes_cli.plugins import _ensure_plugins_discovered
    pm = _ensure_plugins_discovered()
    info = None
    for p in pm.list_plugins():
        if p["name"] == "hermeskill":
            info = p
            break
    if info is None:
        print(json.dumps({"registered": False, "all_hooks": sorted(pm._hooks.keys()), "hook_counts": {k: len(v) for k, v in pm._hooks.items()}}))
    else:
        print(json.dumps({
            "registered": True,
            "info": info,
            "all_hooks": sorted(pm._hooks.keys()),
            "hook_counts": {k: len(v) for k, v in pm._hooks.items()},
        }))
except Exception as e:
    print(json.dumps({"registered": False, "error": f"introspection failed: {e}", "all_hooks": [], "hook_counts": {}}))
PY
)"

if ! printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
  fail "PluginManager introspection produced non-JSON: ${PM_JSON:0:200}"
  set_30 30  # Could not introspect — treat as API incompatible
else
  REGISTERED="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("registered",False))')"
  ERROR_MSG="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; e=json.loads(sys.stdin.read()).get("info",{}).get("error"); print(e if e else "")' 2>/dev/null || echo '')"
  INFO_KEY="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("info",{}).get("key",""))' 2>/dev/null || echo '')"
  INFO_ENABLED="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("info",{}).get("enabled",False))' 2>/dev/null || echo '')"

  if [ "$REGISTERED" = "False" ]; then
    fail "PluginManager: hermeskill NOT registered"
    set_30 30
  else
    if [ -n "$ERROR_MSG" ] && [ "$ERROR_MSG" != "None" ]; then
      fail "PluginManager reports hermeskill enabled=${INFO_ENABLED} but error: $ERROR_MSG"
      set_20 20
    else
      ok "PluginManager: hermeskill enabled=${INFO_ENABLED}, key=${INFO_KEY}, error=None"
    fi
  fi
fi

# ----------------------------------------------------------------------
# 7. Required hooks (code 30)
# ----------------------------------------------------------------------
hdr "7. Required Hooks (code 30)"
if [ -z "${PM_JSON:-}" ]; then
  fail "Skipped — PluginManager introspection did not run"
  set_30 30
else
  ALL_HOOKS="$(printf '%s' "$PM_JSON" | "$HERMES_PY" -c 'import json,sys; print(",".join(sorted(json.loads(sys.stdin.read()).get("all_hooks",[]))))')"
  MISSING=""
  for h in $REQUIRED_HOOKS; do
    case ",$ALL_HOOKS," in
      *",$h,"*) ;;
      *) MISSING="$MISSING $h" ;;
    esac
  done
  if [ -n "$MISSING" ]; then
    fail "Missing hooks:$MISSING"
    set_30 30
  else
    ok "All required hooks present: $ALL_HOOKS"
  fi
fi

# ----------------------------------------------------------------------
# 8. Policy (code 50)
# ----------------------------------------------------------------------
hdr "8. Policy (code 50)"
if [ -f "$CONFIG_FILE" ]; then
  POLICY="$(grep -E '^policy[[:space:]]*=' "$CONFIG_FILE" 2>/dev/null | tail -1 | sed -E 's/^policy[[:space:]]*=[[:space:]]*["'\'']?([^"'\''#]+)["'\'']?.*$/\1/' | tr -d ' \t' || echo '')"
  if [ "$POLICY" = "permissive" ]; then
    ok "policy = permissive"
  else
    fail "policy = '${POLICY:-<unset>}' (expected permissive)"
    set_50 50
  fi
else
  fail "config file missing: $CONFIG_FILE"
  set_50 50
fi

# ----------------------------------------------------------------------
# 9. Gateway state (code 40)
# ----------------------------------------------------------------------
hdr "9. Gateway (code 40)"
GW_OUT="$(systemctl --user show "$GATEWAY_UNIT" --property=MainPID,ActiveState,SubState,ActiveEnterTimestamp 2>&1 || echo 'systemctl_unavailable')"
if [[ "$GW_OUT" == *systemctl_unavailable* ]]; then
  fail "systemctl --user unavailable — cannot check gateway"
  set_40 40
else
  MAIN_PID="$(echo "$GW_OUT" | grep '^MainPID=' | head -1 | cut -d= -f2)"
  ACTIVE="$(echo "$GW_OUT" | grep '^ActiveState=' | head -1 | cut -d= -f2)"
  SUB="$(echo "$GW_OUT" | grep '^SubState=' | head -1 | cut -d= -f2)"
  ENTER="$(echo "$GW_OUT" | grep '^ActiveEnterTimestamp=' | head -1 | cut -d= -f2-)"
  info "MainPID=${MAIN_PID} ActiveState=${ACTIVE} SubState=${SUB}"
  info "ActiveEnterTimestamp=${ENTER}"
  if [ "$ACTIVE" = "active" ] && [ "$SUB" = "running" ]; then
    ok "Gateway active and running"
  else
    fail "Gateway not active (state: ${ACTIVE}/${SUB})"
    set_40 40
  fi
fi

# ----------------------------------------------------------------------
# 10. Recent logs — scoped to current Gateway session (ActiveEnterTimestamp)
# ----------------------------------------------------------------------
hdr "10. Recent Logs — current-session only (ActiveEnterTimestamp onward)"

# Define classification regexes per spec.
# Code 20: load-time failures
RE_20_LOAD=(
  'failed to load plugin'
  'hermeskill traceback'
  'traceback.*hermeskill'
  'client has been closed'
  'failed to rotate'
  'apoptosis'           # only when paired with permissive in current session
  'tool_scope_violation' # only when paired with permissive in current session
)
# Code 30: lifecycle / API incompatible
RE_30_API=(
  'unsupported hook'
  'invalid hook'
  'lifecycle api'
  'hook.*not registered'
  'unknown hook'
)

if [ -n "${ENTER:-}" ]; then
  # Convert ActiveEnterTimestamp (human-readable) to a `--since` string
  # that journalctl accepts. We pass it verbatim — journalctl understands
  # the same format that `date` produces without timezone marker.
  SINCE_ARG="--since=${ENTER}"
else
  # Fallback: last 10 minutes
  SINCE_ARG="--since=10 minutes ago"
fi

# Pull current-session log slice (bounded).
JOURNAL_OUT="$(journalctl --user -u "$GATEWAY_UNIT" $SINCE_ARG --no-pager -q -n 2000 2>/dev/null || true)"

if [ -z "$JOURNAL_OUT" ]; then
  info "No log entries since ${ENTER:-last 10 minutes}"
else
  COUNT_20=0
  COUNT_30=0
  COUNT_OTHER=0
  declare -A COUNT_20_PATTERN
  declare -A COUNT_30_PATTERN

  # Per spec: 旧会话中的 apoptosis 或 tool_scope_violation 不作为当前兼容性失败
  # — only count them when paired with permissive policy in current session.
  # Since we ALREADY checked policy above, we know whether permissive is
  # active. If policy=permissive AND apoptosis appears, count it as 20.
  APOPTOSIS_OK=0
  if [ "${CODE_50:-0}" = "0" ]; then
    # Policy section passed (permissive). If policy was unset/failed, we
    # cannot make a clean call here — be conservative and skip apoptosis
    # pattern.
    APOPTOSIS_OK=1
  fi

  while IFS= read -r line; do
    matched=0
    for re in "${RE_30_API[@]}"; do
      if echo "$line" | grep -qiE "$re"; then
        COUNT_30=$((COUNT_30 + 1))
        COUNT_30_PATTERN[$re]=$((${COUNT_30_PATTERN[$re]:-0} + 1))
        matched=1
        break
      fi
    done
    [ "$matched" = "1" ] && continue
    for re in "${RE_20_LOAD[@]}"; do
      # apoptosis / tool_scope_violation only count under permissive.
      if [[ "$re" == "apoptosis" || "$re" == "tool_scope_violation" ]]; then
        if [ "$APOPTOSIS_OK" = "1" ] && echo "$line" | grep -qiE "$re"; then
          COUNT_20=$((COUNT_20 + 1))
          COUNT_20_PATTERN[$re]=$((${COUNT_20_PATTERN[$re]:-0} + 1))
          matched=1
          break
        fi
      else
        if echo "$line" | grep -qiE "$re"; then
          COUNT_20=$((COUNT_20 + 1))
          COUNT_20_PATTERN[$re]=$((${COUNT_20_PATTERN[$re]:-0} + 1))
          matched=1
          break
        fi
      fi
    done
  done <<< "$JOURNAL_OUT"

  info "Lines scanned (current session): $(echo "$JOURNAL_OUT" | wc -l)"
  if [ "$COUNT_30" -gt 0 ]; then
    fail "Lifecycle / API errors in current-session logs: $COUNT_30"
    for k in "${!COUNT_30_PATTERN[@]}"; do
      info "  pattern '$k': ${COUNT_30_PATTERN[$k]}"
    done
    set_30 30
  else
    ok "No lifecycle/API errors in current-session logs"
  fi
  if [ "$COUNT_20" -gt 0 ]; then
    fail "Load-time failures in current-session logs: $COUNT_20"
    for k in "${!COUNT_20_PATTERN[@]}"; do
      info "  pattern '$k': ${COUNT_20_PATTERN[$k]}"
    done
    set_20 20
  else
    ok "No load-time failures in current-session logs"
  fi
fi

# ----------------------------------------------------------------------
# 11. Final verdict
# ----------------------------------------------------------------------
hdr "Summary"
FINAL_CODE="$(final_code)"
case "$FINAL_CODE" in
  0)  ok "All checks passed. Hermeskill ↔ Hermes integration is healthy."
      info "Auto-recovery: NOT triggered (not needed)."
      ;;
  10) fail "Editable install lost or import paths wrong."
      info "Auto-recovery: ELIGIBLE — recover-hermeskill-integration.sh will reinstall editable."
      ;;
  20) fail "Plugin failed to load (current-session log patterns or PluginManager error)."
      info "Auto-recovery: BLOCKED. Manual investigation required."
      ;;
  30) fail "Hook lifecycle / API incompatible (missing hooks or current-session lifecycle errors)."
      info "Auto-recovery: BLOCKED. Hermeskill code may need an update."
      ;;
  40) fail "Hermes Python missing OR Gateway not active."
      info "Auto-recovery: BLOCKED. Resolve environment / service first."
      ;;
  50) fail "Policy != permissive."
      info "Auto-recovery: BLOCKED. Auto-policy-switch is forbidden."
      ;;
  60) fail "Repository / stable commit / tag abnormal."
      info "Auto-recovery: BLOCKED. Local code may have been altered."
      ;;
  *)  fail "Composite code: $FINAL_CODE"
      ;;
esac

end_report
exit "$FINAL_CODE"