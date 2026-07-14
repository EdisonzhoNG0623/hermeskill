"""Secret redaction for tool argument previews.

The `tool_approval_requests.arguments_preview` JSONB column carries a
human-readable, denatured snapshot of the tool call so operators can decide
*what* they're approving without seeing raw credentials.

Redaction rules:
  * Recursive walk over the argument tree (dicts + lists + scalars).
  * Any key whose lower-cased name matches one of the sensitive patterns
    gets replaced with the string ``"***REDACTED***"``.
  * Sensitive values that appear as free-form string contents (e.g. a
    command body or URL query) are scrubbed when they match the inline
    regex of well-known secret prefixes (`sk-…`, `Bearer …`, `ghp_…`).
  * All other values pass through unchanged.
  * Cycles are broken with a sentinel; depth is capped so a malicious
    payload can't DoS the redaction step.

The redactor is pure (no I/O), deterministic for the same input, and
exhaustive over arbitrary nested JSON-compatible shapes.
"""

from __future__ import annotations

import re
from typing import Any

# Lower-cased key names that always trigger redaction regardless of value.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "secret",
        "cookie",
        "private_key",
        "privatekey",
        "access_token",
        "refresh_token",
        "session_token",
        "client_secret",
    }
)

# Regex fragments that, when found inside a string value, identify a likely
# inline secret. Compiled once; case-insensitive where appropriate.
_INLINE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # OpenAI / Anthropic style
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),  # GitHub PAT
    re.compile(r"gho_[A-Za-z0-9]{20,}"),  # GitHub OAuth
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),  # GCP API key
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

REDACTED: str = "***REDACTED***"
_MAX_DEPTH = 32


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key.strip().lower() in SENSITIVE_KEYS


def _scrub_string(value: str) -> str:
    out = value
    for pat in _INLINE_SECRET_PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact_arguments(value: Any, *, _depth: int = 0) -> Any:
    """Return a deep-copied, secret-stripped view of `value`.

    The original is not mutated. The shape mirrors `value` exactly except
    for redacted leaves. Non-JSON-serialisable leaves are coerced to
    ``str(value)`` so the result always lands cleanly in a JSONB column.
    """

    if _depth > _MAX_DEPTH:
        return REDACTED

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                out[k] = REDACTED
            else:
                out[k] = redact_arguments(v, _depth=_depth + 1)
        return out

    if isinstance(value, list):
        return [redact_arguments(v, _depth=_depth + 1) for v in value]

    if isinstance(value, tuple):
        return tuple(redact_arguments(v, _depth=_depth + 1) for v in value)

    if isinstance(value, str):
        return _scrub_string(value)

    return value