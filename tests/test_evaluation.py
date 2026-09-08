"""Phase 7C Evaluation tests (dataset, metrics, runner, HTTP API).

The Evaluation runner executes the REAL agent path against an isolated seeded
SQLite database, so these tests stay deterministic (use_llm=False) and never
touch the interview demo database.
"""
import pytest

from app.evaluation.dataset import EVALUATION_DATASET, get_case, get_dataset
from app.evaluation.metrics import (
    evaluate_case,
    normalize_order_ref,
    summarize,
)
from app.evaluation.service import run_evaluation

REQUIRED_CATEGORIES = {
    "FAQ / RAG",
    "Order Status",
    "Logistics",
    "Cancel Order",
    "Refund",
    "Unauthorized Order",
    "Business Rule Rejection",
    "Missing Information",
    "Unsafe / Prompt Injection",
}


def test_dataset_contains_all_required_categories():
    cases = list(EVALUATION_DATASET)
    assert len(cases) >= 9
    categories = {case.category for case in cases}
    assert REQUIRED_CATEGORIES <= categories
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        assert case.user_message
        assert case.expected_intent
        assert case.expected_route
        assert case.expected_outcome


def test_dataset_loader_and_unknown_case():
    assert get_case("rag-faq").case_id == "rag-faq"
    with pytest.raises(KeyError):
        get_case("does-not-exist")
    subset = get_dataset(["rag-faq", "order-status"])
    assert [case.case_id for case in subset] == ["rag-faq", "order-status"]


def test_metrics_na_and_pass_fail_are_not_fabricated():
    faq = get_case("rag-faq")
    payload = {
        "run_status": "COMPLETED",
        "agent_status": "success",
        "intent": "REFUND_INQUIRY",
        "route": "RAG",
        "entities": {"order_id": None},
        "steps": [],
        "risk": None,
        "approval": None,
        "approval_resolution": None,
    }
    result = evaluate_case(faq, payload)
    assert result["status"] == "PASS"
    checks = {c["metric"]: c for c in result["checks"]}
    assert checks["RISK"]["passed"] is None  # no tool -> risk is N/A
    assert checks["EXECUTION"]["passed"] is None
    assert checks["APPROVAL"]["passed"] is True

    wrong = dict(payload)
    wrong["intent"] = "ORDER_STATUS"
    bad = evaluate_case(faq, wrong)
    assert bad["status"] == "FAIL"
    assert any("INTENT" in reason for reason in bad["failure_reasons"])


def test_metric_aggregation_rates():
    faq = get_case("rag-faq")
    ok_payload = {
        "run_status": "COMPLETED",
        "agent_status": "success",
        "intent": "REFUND_INQUIRY",
        "route": "RAG",
        "entities": {"order_id": None},
        "steps": [],
        "risk": None,
        "approval": None,
        "approval_resolution": None,
    }
    good = evaluate_case(faq, ok_payload)
    bad_payload = dict(ok_payload)
    bad_payload["intent"] = "ORDER_STATUS"
    bad = evaluate_case(faq, bad_payload)
    summary = summarize([good, bad])
    assert summary["total_cases"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    intent = summary["metrics"]["INTENT"]
    assert intent["applicable"] == 2
    assert intent["passed"] == 1
    assert intent["rate"] == 50.0


def test_normalize_order_ref():
    assert normalize_order_ref("ORD-1001") == "ORD-1001"
    assert normalize_order_ref(1001) == "ORD-1001"
    assert normalize_order_ref("1001") == "ORD-1001"
    assert normalize_order_ref(None) is None


def test_run_evaluation_planning_cases_deterministic():
    report = run_evaluation(
        case_ids=[
            "rag-faq",
            "order-status",
            "cancel-request",
            "missing-info",
            "unsafe-bypass",
        ],
        use_llm=False,
    )
    assert report["total_cases"] == 5
    assert report["passed"] == 5
    assert report["failed"] == 0
    assert report["mode"] == "deterministic"
    assert report["metrics"]["INTENT"]["rate"] == 100.0
    for row in report["cases"]:
        assert row["expected"]["outcome"]
        assert row["actual"]["outcome"]


def test_run_evaluation_execute_and_verify_cases_deterministic():
    report = run_evaluation(
        case_ids=[
            "cancel-execute",
            "refund-execute",
            "business-rule-reject",
            "unauthorized-order",
        ],
        use_llm=False,
    )
    assert report["total_cases"] == 4
    assert report["passed"] == 4
    assert report["failed"] == 0
    execution = report["metrics"]["EXECUTION"]
    verification = report["metrics"]["VERIFICATION"]
    assert execution["rate"] == 100.0
    assert verification["rate"] == 100.0
