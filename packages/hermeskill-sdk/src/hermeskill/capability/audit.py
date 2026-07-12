from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import CapabilityResult


@dataclass(frozen=True)
class PermissionAuditRecord:

    timestamp: str
    profile: str
    capability: str
    decision: str
    risk: str | None
    reason: str | None


def create_audit_record(
    *,
    profile: str,
    result: CapabilityResult,
) -> PermissionAuditRecord:

    return PermissionAuditRecord(
        timestamp=datetime.now(
            UTC
        ).isoformat(),
        profile=profile,
        capability=result.capability,
        decision=result.decision.value,
        risk=result.risk,
        reason=result.reason,
    )
