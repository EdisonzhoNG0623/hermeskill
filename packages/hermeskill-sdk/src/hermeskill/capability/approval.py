from __future__ import annotations

from .models import PermissionDecision


def evaluate_risk(
    risk: str,
) -> PermissionDecision:

    if risk == "low":
        return PermissionDecision.ALLOW

    if risk in {
        "medium",
        "high",
    }:
        return PermissionDecision.APPROVAL_REQUIRED

    return PermissionDecision.DENY
