from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass(frozen=True)
class Capability:
    name: str
    risk: str
    domain: str
    description: str


@dataclass(frozen=True)
class CapabilityResult:
    capability: str
    decision: PermissionDecision
    risk: str | None = None
    reason: str | None = None
