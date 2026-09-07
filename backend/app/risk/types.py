"""Risk Control vocabulary (Phase 5 MVP).

Owns the stable vocabulary shared by the risk policy / engine and the agent
workflow:

    RiskLevel    LOW / MEDIUM / HIGH / CRITICAL
    RiskAction   AUTO_EXECUTE / USER_CONFIRM / HUMAN_APPROVAL / BLOCK
    RiskDecision (risk_level, action, reason, policy_id)
    RiskContext  business context assembled by the Workflow - never a
                 user-controlled amount

Boundary rules:
    - The risk layer is pure: it only depends on the standard library. It
      never touches the Repository / Database / Service layers.
    - RiskContext.refund_amount is the authoritative amount provided by the
      eligibility ToolResult / Service. The engine must never receive a
      user-provided amount as trusted input (docs/RISK_CONTROL.md).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

JsonDict = dict[str, Any]


class RiskLevel(str, enum.Enum):
    """Static risk classification for an operation."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskAction(str, enum.Enum):
    """What the Risk Gate must do with a ToolRequest."""

    AUTO_EXECUTE = "AUTO_EXECUTE"
    USER_CONFIRM = "USER_CONFIRM"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class RiskDecision:
    """Output of the Risk Engine for one operation."""

    risk_level: RiskLevel
    action: RiskAction
    reason: str
    policy_id: str

    def to_dict(self) -> JsonDict:
        return {
            "risk_level": self.risk_level.value,
            "action": self.action.value,
            "reason": self.reason,
            "policy_id": self.policy_id,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "RiskDecision":
        return cls(
            risk_level=RiskLevel(str(data["risk_level"])),
            action=RiskAction(str(data["action"])),
            reason=str(data.get("reason", "")),
            policy_id=str(data.get("policy_id", "")),
        )


@dataclass(frozen=True)
class RiskContext:
    """Business context evaluated by the engine (read-only, workflow-assembled).

    Fields:
        request_id:    current agent request (for audit / diagnostics)
        user_id:       trusted session identity
        order_id:      target order id when known
        refund_amount: authoritative refund amount already computed by the
                       Service / eligibility ToolResult (never user input)
    """

    request_id: str = ""
    user_id: int | None = None
    order_id: int | None = None
    refund_amount: Decimal | None = None

    def to_dict(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "order_id": self.order_id,
            "refund_amount": (
                float(self.refund_amount) if self.refund_amount is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "RiskContext":
        amount = data.get("refund_amount")
        return cls(
            request_id=str(data.get("request_id", "")),
            user_id=data.get("user_id"),
            order_id=data.get("order_id"),
            refund_amount=Decimal(str(amount)) if amount is not None else None,
        )
