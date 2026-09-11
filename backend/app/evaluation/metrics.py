"""Evaluation metric helpers (Phase 7C).

Pure functions that turn one dataset case + the REAL run payload(s) into:

    - per-metric pass / fail / N/A checks
    - normalized actual fields (intent, entities, route, risk, approval,
      execution, verification, outcome)
    - a whole-report summary with the headline metrics (Phase 9C adds CASE:
      after-sales case state + deterministic eligibility, kept separate from
      "the agent answered successfully")

No magic numbers and no fabricated scores: a metric is only counted when the
case carries an explicit expectation (otherwise it is N/A).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.evaluation.dataset import NO_ACTION, EvaluationCase

JsonDict = dict[str, Any]

METRICS = (
    "INTENT",
    "ENTITY",
    "ROUTE",
    "RISK",
    "APPROVAL",
    "EXECUTION",
    "VERIFICATION",
    "CASE",
    # Phase 9D: after-sales treatment plan + ticket creation.
    "treatment_plan_accuracy",
    "ticket_creation_success",
    "ticket_id_presence",
    "duplicate_ticket_rate",
    "execution_not_triggered",
)

# The write/read operation each business route is expected to reach.
ROUTE_PRIMARY_TOOL = {
    "ORDER_TOOL": "get_order",
    "LOGISTICS_TOOL": "get_logistics",
    "REFUND_TOOL": "create_refund",
    "CANCEL_TOOL": "cancel_order",
    "TICKET_TOOL": "create_ticket",
}

_REJECT_MARKERS = (
    "cannot be cancelled",
    "cannot be refunded",
    "not refundable",
    "not been paid",
    "not been delivered",
    "already has a pending refund",
    "has already been refunded",
    "already been cancelled",
    "cancelled orders cannot be refunded",
    "contains non-returnable items",
    "order is not refundable",
)

# Phase 9D: tools that MUTATE business state. None of them may run in 9D; the
# record only exists to prove it (execution belongs to Phase 9E behind the
# Risk Gate / Human-in-the-loop layers).
_WRITE_TOOLS = ("create_refund", "cancel_order", "execute_exchange", "execute_repair")


def execution_triggered(payload: JsonDict) -> bool:
    """True only when a real business write actually executed in this run."""
    for step in payload.get("steps") or []:
        if step.get("label") in _WRITE_TOOLS and step.get("state") == "success":
            return True
    return False


def normalize_order_ref(value: Any) -> str | None:
    """Normalize an order id/ref to ORD-<id> for comparisons."""
    if value is None or str(value).strip() in ("", "None"):
        return None
    text = str(value).strip()
    if text.upper().startswith("ORD"):
        return text.upper()
    return f"ORD-{text}"


def _risk_gate_steps(steps: list[JsonDict]) -> list[JsonDict]:
    return [step for step in steps if step.get("label") == "Risk Gate"]


def _actual_risk(payload: JsonDict) -> tuple[str | None, str | None]:
    """Best-effort actual risk decision from the run payload."""
    approval_resolution = payload.get("approval_resolution")
    if isinstance(approval_resolution, dict) and approval_resolution.get("risk_level"):
        return (
            str(approval_resolution["risk_level"]).upper(),
            "HUMAN_APPROVAL",
        )
    risk = payload.get("risk")
    if isinstance(risk, dict) and risk.get("level"):
        return str(risk["level"]).upper(), str(risk.get("action") or "").upper()
    steps = _risk_gate_steps(payload.get("steps") or [])
    for step in reversed(steps):
        if step.get("risk_level"):
            return str(step["risk_level"]).upper(), str(step.get("risk_action") or "").upper()
    return None, None


def actual_outcome(payload: JsonDict) -> str:
    """Classify the run payload into the normalized outcome vocabulary."""
    run_status = payload.get("run_status") or ""
    agent_status = payload.get("agent_status") or ""
    case = payload.get("case")
    eligibility = payload.get("eligibility")
    if isinstance(eligibility, dict) and eligibility:
        # Phase 9C: the after-sales conclusion is its own outcome. A successful
        # answer is NOT the same as a completed after-sales case.
        if eligibility.get("eligible") is True:
            return "ELIGIBILITY_PROCESSING"
        if eligibility.get("eligible") is False:
            return "ELIGIBILITY_REJECTED"
        if isinstance(case, dict) and case.get("status") == "INFORMATION_COLLECTION":
            return "INFORMATION_COLLECTION"
        return "INVESTIGATION"
    if run_status == "WAITING_USER_CONFIRMATION":
        return "USER_CONFIRMATION"
    if run_status == "WAITING_HUMAN_APPROVAL":
        return "HUMAN_APPROVAL"
    if agent_status == "needs_clarification":
        return "CLARIFY"
    if run_status in ("FAILED", "VERIFICATION_FAILED") or agent_status == "error":
        details = " ".join(
            str(step.get("detail") or "") for step in (payload.get("steps") or [])
        ).lower()
        if "not authorized" in details:
            return "ACCESS_DENIED"
        if any(marker in details for marker in _REJECT_MARKERS):
            return "RULE_REJECTED"
        return "ERROR"
    return "ANSWERED"


def _tool_step(payload: JsonDict, tool_name: str) -> JsonDict | None:
    for step in payload.get("steps") or []:
        if step.get("label") == tool_name:
            return step
    return None


def actual_execution(payload: JsonDict, case: EvaluationCase) -> bool | None:
    """Whether the case's primary write/read operation succeeded.

    Returns True/False only when the tool actually ran; returns None when the
    run stopped before it (gate / clarification / RAG).
    """
    tool = ROUTE_PRIMARY_TOOL.get(case.expected_route or "")
    if tool is None:
        return None
    step = _tool_step(payload, tool)
    if step is None:
        return None
    if step.get("state") == "success":
        return True
    if step.get("state") == "failed":
        return False
    return None


def actual_verification(payload: JsonDict) -> bool | None:
    step = None
    for item in payload.get("steps") or []:
        if item.get("label") == "Verify":
            step = item
    if step is None:
        return None
    if step.get("state") == "success":
        return True
    if step.get("state") == "failed":
        return False
    return None


def actuals_from_payload(case: EvaluationCase, payload: JsonDict) -> JsonDict:
    """Normalized actual values shown in the per-case table."""
    entities = payload.get("entities") or {}
    risk_level, risk_action = _actual_risk(payload)
    approval_resolution = payload.get("approval_resolution")
    execution = actual_execution(payload, case)
    verification = actual_verification(payload)
    case_view = payload.get("case")
    eligibility = payload.get("eligibility")
    treatment = payload.get("treatment")
    ticket = payload.get("ticket")
    treatment_view = treatment if isinstance(treatment, dict) else {}
    ticket_view = ticket if isinstance(ticket, dict) else {}
    return {
        "intent": payload.get("intent"),
        "route": payload.get("route"),
        "order_id": normalize_order_ref(entities.get("order_id")),
        "risk_level": risk_level,
        "risk_action": risk_action,
        "requires_approval": bool(
            payload.get("approval")
            or (isinstance(approval_resolution, dict) and bool(approval_resolution.get("approval_id")))
        ),
        "execution_success": execution,
        "verification_success": verification,
        "outcome": actual_outcome(payload),
        "run_status": payload.get("run_status"),
        "agent_status": payload.get("agent_status"),
        # Phase 9C: after-sales case state + deterministic eligibility result.
        "case_status": (
            case_view.get("status") if isinstance(case_view, dict) else None
        ),
        "eligible": (
            eligibility.get("eligible") if isinstance(eligibility, dict) else None
        ),
        "failed_rules": (
            list(eligibility.get("failed_rules") or [])
            if isinstance(eligibility, dict)
            else None
        ),
        # Phase 9D: deterministic treatment plan + after-sales ticket.
        "treatment_action": treatment_view.get("action"),
        "ticket_created": (
            bool(ticket_view.get("created"))
            if ticket_view.get("id") is not None
            else False
        ),
        "ticket_id": ticket_view.get("id"),
        "ticket_count": ticket_view.get("count"),
        "execution_triggered": execution_triggered(payload),
    }


def _check(passed: bool | None, actual: Any) -> JsonDict:
    return {"passed": passed, "actual": actual}


def evaluate_case(
    case: EvaluationCase,
    initial_payload: JsonDict,
    final_payload: JsonDict | None = None,
) -> JsonDict:
    """Compare one case's expectations to the real run payload(s)."""
    final = final_payload if final_payload is not None else initial_payload
    initial = initial_payload
    # Intent/entities/route/risk/approval describe the planning decision, so
    # they are read from the initial message. Execution/verification/outcome
    # describe what finally happened (they may include a resumed approval).
    plan = actuals_from_payload(case, initial)
    done = actuals_from_payload(case, final)

    checks: list[JsonDict] = []
    # INTENT
    if case.expected_intent is None:
        checks.append({"metric": "INTENT", "expected": None, **{"passed": None, "actual": plan["intent"]}})
    else:
        checks.append({
            "metric": "INTENT", "expected": case.expected_intent,
            "passed": plan["intent"] == case.expected_intent, "actual": plan["intent"],
        })
    # ENTITY (order reference only; applies when an order is expected)
    if case.expected_order_id is None:
        checks.append({"metric": "ENTITY", "expected": None, "passed": None, "actual": plan["order_id"]})
    else:
        checks.append({
            "metric": "ENTITY", "expected": case.expected_order_id,
            "passed": plan["order_id"] == normalize_order_ref(case.expected_order_id),
            "actual": plan["order_id"],
        })
    # ROUTE
    checks.append({
        "metric": "ROUTE", "expected": case.expected_route,
        "passed": plan["route"] == case.expected_route, "actual": plan["route"],
    })
    # RISK decision accuracy
    if case.expected_risk_level is None:
        checks.append({"metric": "RISK", "expected": None, "passed": None, "actual": plan["risk_level"]})
    else:
        risk_ok = plan["risk_level"] == case.expected_risk_level
        if case.expected_risk_action is not None and plan["risk_action"] is not None:
            risk_ok = risk_ok and plan["risk_action"] == case.expected_risk_action
        checks.append({
            "metric": "RISK", "expected": case.expected_risk_level,
            "passed": risk_ok, "actual": plan["risk_level"],
        })
    # APPROVAL decision accuracy
    if case.expected_requires_approval is None:
        checks.append({"metric": "APPROVAL", "expected": None, "passed": None, "actual": plan["requires_approval"]})
    else:
        checks.append({
            "metric": "APPROVAL", "expected": case.expected_requires_approval,
            "passed": plan["requires_approval"] == case.expected_requires_approval,
            "actual": plan["requires_approval"],
        })
    # EXECUTION success
    if case.expected_execution_success is None:
        checks.append({"metric": "EXECUTION", "expected": None, "passed": None, "actual": done["execution_success"]})
    else:
        checks.append({
            "metric": "EXECUTION", "expected": case.expected_execution_success,
            "passed": done["execution_success"] == case.expected_execution_success,
            "actual": done["execution_success"],
        })
    # VERIFICATION success
    if case.expected_verification_success is None:
        checks.append({"metric": "VERIFICATION", "expected": None, "passed": None, "actual": done["verification_success"]})
    else:
        checks.append({
            "metric": "VERIFICATION", "expected": case.expected_verification_success,
            "passed": done["verification_success"] == case.expected_verification_success,
            "actual": done["verification_success"],
        })
    # OUTCOME (part of the overall pass/fail, reported separately)
    # CASE (Phase 9C): after-sales case state + deterministic eligibility.
    if case.expected_case_status is None:
        checks.append({
            "metric": "CASE", "expected": None, "passed": None,
            "actual": done["case_status"],
        })
    else:
        case_ok = done["case_status"] == case.expected_case_status
        if case.expected_eligible is not None:
            case_ok = case_ok and done["eligible"] == case.expected_eligible
        if case.expected_failed_rules is not None:
            case_ok = case_ok and (
                list(done["failed_rules"] or []) == list(case.expected_failed_rules)
            )
        checks.append({
            "metric": "CASE", "expected": case.expected_case_status,
            "passed": case_ok,
            "actual": (
                f"{done['case_status']} eligible={done['eligible']} "
                f"failed_rules={done['failed_rules']}"
            ),
        })
    # --- Phase 9D: treatment plan + ticket ----------------------------------
    if case.expected_treatment_action is None:
        checks.append({
            "metric": "treatment_plan_accuracy", "expected": None,
            "passed": None, "actual": done["treatment_action"],
        })
    else:
        checks.append({
            "metric": "treatment_plan_accuracy",
            "expected": case.expected_treatment_action,
            "passed": (done["treatment_action"] or NO_ACTION)
            == case.expected_treatment_action,
            "actual": done["treatment_action"],
        })
    if case.expected_ticket_created is None:
        checks.append({
            "metric": "ticket_creation_success", "expected": None,
            "passed": None, "actual": done["ticket_created"],
        })
    else:
        checks.append({
            "metric": "ticket_creation_success",
            "expected": case.expected_ticket_created,
            "passed": bool(done["ticket_created"]) == case.expected_ticket_created,
            "actual": done["ticket_created"],
        })
    if case.expected_ticket_id_present is None:
        checks.append({
            "metric": "ticket_id_presence", "expected": None,
            "passed": None, "actual": done["ticket_id"],
        })
    else:
        checks.append({
            "metric": "ticket_id_presence",
            "expected": case.expected_ticket_id_present,
            "passed": (done["ticket_id"] is not None)
            == case.expected_ticket_id_present,
            "actual": done["ticket_id"],
        })
    if case.expected_single_ticket is None:
        checks.append({
            "metric": "duplicate_ticket_rate", "expected": None,
            "passed": None, "actual": done["ticket_count"],
        })
    else:
        checks.append({
            "metric": "duplicate_ticket_rate",
            "expected": case.expected_single_ticket,
            "passed": (done["ticket_count"] == 1) == case.expected_single_ticket,
            "actual": done["ticket_count"],
        })
    if case.expected_execution_not_triggered is None:
        checks.append({
            "metric": "execution_not_triggered", "expected": None,
            "passed": None, "actual": done["execution_triggered"],
        })
    else:
        checks.append({
            "metric": "execution_not_triggered",
            "expected": case.expected_execution_not_triggered,
            "passed": (not done["execution_triggered"])
            == case.expected_execution_not_triggered,
            "actual": done["execution_triggered"],
        })
    checks.append({
        "metric": "OUTCOME", "expected": case.expected_outcome,
        "passed": done["outcome"] == case.expected_outcome, "actual": done["outcome"],
    })

    applicable = [c for c in checks if c["metric"] != "OUTCOME" and c.get("expected") is not None]
    failures = [c for c in applicable if c.get("passed") is False]
    all_na = len(applicable) == 0
    passed_total = all(c.get("passed") for c in applicable) and checks[-1].get("passed")
    status = "N/A" if all_na else ("PASS" if passed_total else "FAIL")
    reasons = [f"{c['metric']}: expected={c.get('expected')} actual={c.get('actual')}" for c in failures]
    if not all_na and checks[-1].get("passed") is False:
        reasons.append(f"OUTCOME: expected={case.expected_outcome} actual={checks[-1].get('actual')}")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "scenario": case.scenario,
        "user_message": case.user_message,
        "expected": {
            "intent": case.expected_intent,
            "route": case.expected_route,
            "order_id": case.expected_order_id,
            "risk_level": case.expected_risk_level,
            "risk_action": case.expected_risk_action,
            "requires_approval": case.expected_requires_approval,
            "execution_success": case.expected_execution_success,
            "verification_success": case.expected_verification_success,
            "outcome": case.expected_outcome,
            "treatment_action": case.expected_treatment_action,
            "ticket_created": case.expected_ticket_created,
        },
        "actual": done,
        "status": status,
        "failure_reasons": reasons,
        "checks": checks,
    }


def summarize(case_results: list[JsonDict]) -> JsonDict:
    """Aggregate pass/fail/N/A counts and the headline metrics."""
    metric_buckets: dict[str, list[JsonDict]] = {name: [] for name in METRICS}
    for case_result in case_results:
        for check in case_result["checks"]:
            metric = check.get("metric")
            if metric in metric_buckets:
                metric_buckets[metric].append(check)

    metrics_summary: dict[str, JsonDict] = {}
    for metric in METRICS:
        checks = metric_buckets[metric]
        applicable = [c for c in checks if c.get("passed") is not None]
        passed = sum(1 for c in applicable if c["passed"] is True)
        metrics_summary[metric] = {
            "label": {
                "INTENT": "Intent Accuracy",
                "ENTITY": "Entity Accuracy",
                "ROUTE": "Route Accuracy",
                "RISK": "Risk Decision Accuracy",
                "APPROVAL": "Approval Decision Accuracy",
                "EXECUTION": "Execution Success",
                "VERIFICATION": "Verification Success",
                "CASE": "After-sales Case / Eligibility",
                "treatment_plan_accuracy": "Treatment Plan Accuracy",
                "ticket_creation_success": "Ticket Creation Success",
                "ticket_id_presence": "Ticket ID Presence",
                "duplicate_ticket_rate": "Duplicate Ticket Rate",
                "execution_not_triggered": "Execution Not Triggered",
            }[metric],
            "passed": passed,
            "applicable": len(applicable),
            "total": len(checks),
            "rate": (round(100.0 * passed / len(applicable), 1) if applicable else None),
        }

    passed_cases = sum(1 for r in case_results if r["status"] == "PASS")
    failed_cases = sum(1 for r in case_results if r["status"] == "FAIL")
    na_cases = sum(1 for r in case_results if r["status"] == "N/A")
    return {
        "total_cases": len(case_results),
        "passed": passed_cases,
        "failed": failed_cases,
        "na": na_cases,
        "metrics": metrics_summary,
    }
