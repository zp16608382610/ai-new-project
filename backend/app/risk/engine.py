"""Risk Engine (Phase 5 MVP).

The engine turns a ToolRequest + Business Context into a RiskDecision. It is
intentionally pure:

    Risk Engine: ToolRequest + RiskContext -> RiskDecision

It never reaches the Repository / Database. Order amounts and other business
facts are provided by the Workflow / Service as an already-queried RiskContext.
"""
from __future__ import annotations

from app.risk.policy import RiskPolicy
from app.risk.types import RiskContext, RiskDecision


class RiskEngine:
    """Stateless evaluator delegating to the current RiskPolicy."""

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self._policy = policy or RiskPolicy()

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    def evaluate(self, operation: str, context: RiskContext | None = None) -> RiskDecision:
        """Classify one operation (tool name or intent label) with context."""
        return self._policy.decide(operation, context)
