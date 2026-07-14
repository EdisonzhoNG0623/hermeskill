#!/usr/bin/env bash
# show-production-baseline.sh
#
# Phase 3 — Stable Reference (READ-ONLY)
#
# Prints a single-screen snapshot of the production baseline state.
# Never modifies anything: no installs, no restarts, no git operations
# beyond read-only inspection.
#
# Output is plain text, key=value style, one line per metric. Designed
# to be both human-readable and greppable for scripting.

set -u

REPO="/opt/ai/projects/hermes-upgrades/hermeskill"
HERMES_AGENT_DIR="/home/ai/.hermes/hermes-agent"
HERMES_PY="${HERMES_AGENT_DIR}/venv/bin/python"
GATEWAY_UNIT="hermes-gateway.service"
STABLE_TAG="hermeskill-session-reset-fixed-20260715"
CORE_FIX_COMMIT="07139f8f27c9e036475e47f06a1d00d99e142575"

# --- Stable Tag / Stable Commit ---------------------------------------------
STABLE_TAG_SHA="$(git -C "$REPO" rev-parse "${STABLE_TAG}^{commit}" 2>/dev/null || echo MISSING)"

# --- Current Commit / Current Branch ----------------------------------------
CURRENT_COMMIT="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
CURRENT_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
CURRENT_COMMIT_SHORT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# --- Ahead/Behind Stable -----------------------------------------------------
if [ "$STABLE_TAG_SHA" = "MISSING" ]; then
  AHEAD_BEHIND="tag-missing"
else
  AHEAD_BEHIND="$(git -C "$REPO" rev-list --left-right --count "${STABLE_TAG_SHA}...HEAD" 2>/dev/null | tr -s ' \t' ' ' || echo unknown)"
fi

# --- Git Status (porcelain, joined with commas) ------------------------------
GIT_STATUS="$(git -C "$REPO" status --porcelain 2>/dev/null | tr '\n' ';' | sed 's/;$//' || echo unknown)"

# --- Editable Install (does pip show point inside $REPO?) --------------------
EDITABLE_LOC="$("$HERMES_PY" -m pip show hermeskill 2>/dev/null | awk -F': ' '/^Editable project location:/{print $2; exit}' | sed 's/[[:space:]]*$//' || true)"
if [ -z "$EDITABLE_LOC" ]; then
  EDITABLE_INSTALL="NOT_EDITABLE"
elif [[ "$EDITABLE_LOC/" == "${REPO}/"* ]]; then
  EDITABLE_INSTALL="EDITABLE"
else
  EDITABLE_INSTALL="EDITABLE_OUTSIDE_REPO"
fi

# --- Gateway PID / Uptime ----------------------------------------------------
GW_PID="$(systemctl --user show "$GATEWAY_UNIT" --property=MainPID --value 2>/dev/null || echo 0)"
GW_ACTIVE_SINCE="$(systemctl --user show "$GATEWAY_UNIT" --property=ActiveEnterTimestamp --value 2>/dev/null || echo unknown)"
GW_UPTIME="n/a"
if [ -n "$GW_ACTIVE_SINCE" ] && [ "$GW_ACTIVE_SINCE" != "unknown" ]; then
  # Convert ActiveEnterTimestamp to epoch seconds, then compute uptime.
  ENTER_EPOCH="$(date -d "$GW_ACTIVE_SINCE" +%s 2>/dev/null || echo 0)"
  NOW_EPOCH="$(date +%s)"
  if [ "$ENTER_EPOCH" -gt 0 ] 2>/dev/null; then
    SECS=$((NOW_EPOCH - ENTER_EPOCH))
    DAYS=$((SECS / 86400))
    HRS=$(((SECS % 86400) / 3600))
    MINS=$(((SECS % 3600) / 60))
    GW_UPTIME="${DAYS}d ${HRS}h ${MINS}m"
  fi
fi

# --- Hermeskill Version (from setuptools-scm or hardcoded fallback) ---------
HK_VERSION="$("$HERMES_PY" - <<'PY' 2>/dev/null || true
try:
    from importlib.metadata import version
    print(version("hermeskill"))
except Exception:
    print("unknown")
PY
)"

# --- Hermes Version (top-level 'hermes' CLI) ---------------------------------
HERMES_VERSION="$(hermes version 2>&1 | grep -v 'Bitwarden\|Run\|Token' | head -1 | sed 's/^[[:space:]]*//' || echo unknown)"

# --- Emit --------------------------------------------------------------------
cat <<EOF
Stable Tag        : ${STABLE_TAG}
Stable Commit     : ${STABLE_TAG_SHA}
Current Commit    : ${CURRENT_COMMIT_SHORT}
Current Branch    : ${CURRENT_BRANCH}
Ahead/Behind Stbl : ${AHEAD_BEHIND}
Git Status        : ${GIT_STATUS:-clean}
Editable Install  : ${EDITABLE_INSTALL} (${EDITABLE_LOC:-n/a})
Gateway PID       : ${GW_PID}
Gateway Uptime    : ${GW_UPTIME}
Hermeskill Ver    : ${HK_VERSION}
Hermes Ver        : ${HERMES_VERSION}
EOF

exit 0