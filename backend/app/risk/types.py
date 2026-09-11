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

    Phase 9F fields (after_sales_case_id .. days_since_delivery) are the
    after-sales case + eligibility facts. They are only ever read from the
    persisted case / deterministic eligibility result; no caller may put a
    user- or model-supplied value in them.
    """

    request_id: str = ""
    user_id: int | None = None
    order_id: int | None = None
    refund_amount: Decimal | None = None
    # Phase 9F: after-sales facts copied from the PERSISTED case and the
    # deterministic eligibility conclusion the workflow already computed. They
    # exist so the policy can recognise a standard, already-verified low-risk
    # refund. Every field is a business fact; None means "unknown", and an
    # unknown fact can never make an operation low risk.
    after_sales_case_id: str | None = None
    case_type: str | None = None
    requested_action: str | None = None
    eligibility_passed: bool | None = None
    order_status: str | None = None
    items_returnable: bool | None = None
    active_refund_count: int | None = None
    days_since_delivery: int | None = None

    def to_dict(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "order_id": self.order_id,
            "refund_amount": (
                float(self.refund_amount) if self.refund_amount is not None else None
            ),
            "after_sales_case_id": self.after_sales_case_id,
            "case_type": self.case_type,
            "requested_action": self.requested_action,
            "eligibility_passed": self.eligibility_passed,
            "order_status": self.order_status,
            "items_returnable": self.items_returnable,
            "active_refund_count": self.active_refund_count,
            "days_since_delivery": self.days_since_delivery,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "RiskContext":
        amount = data.get("refund_amount")
        return cls(
            request_id=str(data.get("request_id", "")),
            user_id=data.get("user_id"),
            order_id=data.get("order_id"),
            refund_amount=Decimal(str(amount)) if amount is not None else None,
            after_sales_case_id=data.get("after_sales_case_id"),
            case_type=data.get("case_type"),
            requested_action=data.get("requested_action"),
            eligibility_passed=data.get("eligibility_passed"),
            order_status=data.get("order_status"),
            items_returnable=data.get("items_returnable"),
            active_refund_count=data.get("active_refund_count"),
            days_since_delivery=data.get("days_since_delivery"),
        )
