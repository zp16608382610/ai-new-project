"""Phase 7C Evaluation runner.

Runs every dataset case through the SAME real agent path the Chat UI uses
(app.demo.service.run_chat / finalize_approval) against an isolated seeded
SQLite database, then computes the report with app.evaluation.metrics.

Why an isolated database:
    - refund/cancel executions inside the runner stay inside a throw-away
      file, so repeated evaluation runs never dirty the interview demo DB;
    - the workflow (Risk Gate / Approval / Execute / Verify) is unchanged.

LLM: with use_llm=False the runner forces the deterministic offline path
(llm_enabled=False). With use_llm=True the configured DeepSeek provider is
used exactly like /demo/chat - results then depend on the live model.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from app.evaluation.dataset import EvaluationCase, get_dataset
from app.evaluation.metrics import evaluate_case, summarize


def _default_database_url() -> tuple[str, str]:
    """Create a throw-away SQLite file and return (url, path)."""
    fd, path = tempfile.mkstemp(prefix="acsa-eval-", suffix=".db")
    os.close(fd)
    url = "sqlite+pysqlite:///" + path.replace("\\", "/")
    return url, path


def run_evaluation(
    *,
    database_url: str | None = None,
    case_ids: list[str] | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Execute the dataset against the real workflow and return the report."""
    from app.demo.demo_seed import prepare_demo_database
    from app.demo.service import finalize_approval, run_chat
    from app.demo.store import DemoRunStore
    from app.db.session import create_db_engine, create_session_factory

    own_path: str | None = None
    url = database_url
    if url is None:
        url, own_path = _default_database_url()
    prepare_demo_database(url)
    engine = create_db_engine(url)
    session = create_session_factory(engine)()
    cases = get_dataset(case_ids)

    case_results: list[dict[str, Any]] = []
    try:
        for case in cases:
            result = _run_case(session, case, use_llm=use_llm)
            case_results.append(result)
    finally:
        session.close()
        engine.dispose()
        if own_path is not None:
            try:
                os.unlink(own_path)
            except OSError:
                pass

    summary = summarize(case_results)
    return {
        "version": "phase7c",
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "deterministic" if not use_llm else "live",
        "llm_enabled": bool(use_llm),
        "total_cases": summary["total_cases"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "na": summary["na"],
        "metrics": summary["metrics"],
        "cases": case_results,
    }


def _run_case(session, case: EvaluationCase, *, use_llm: bool) -> dict[str, Any]:
    """Run one case end-to-end; never fabricate a result on failure."""
    # Phase 9C: the after-sales window must be reproducible, so a case can pin
    # the reference time instead of depending on the wall clock.
    from app.demo.service import finalize_approval, run_chat
    from app.demo.store import DemoRunStore

    store = DemoRunStore()
    session_id = f"eval-{case.case_id}"
    initial_payload: dict[str, Any] | None = None
    final_payload: dict[str, Any] | None = None
    error: str | None = None
    try:
        initial_payload = run_chat(
            session,
            store,
            message=case.user_message,
            user_id=case.user_id,
            session_id=session_id,
            user_confirmed=case.user_confirmed,
            use_llm=use_llm,
            investigation_reference_time=_reference_time(case),
        )
        final_payload = initial_payload
        approval = initial_payload.get("approval")
        if case.resolve_approval and isinstance(approval, dict) and approval.get("id") is not None:
            final_payload = finalize_approval(
                session,
                store,
                int(approval["id"]),
                approved=True,
                resolved_by="evaluation-runner",
                use_llm=use_llm,
            )
    except Exception as exc:  # a failing case is reported, never hidden
        error = f"{type(exc).__name__}: {exc}"

    if error is not None:
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
                "case_status": case.expected_case_status,
                "eligible": case.expected_eligible,
                "failed_rules": (
                    list(case.expected_failed_rules)
                    if case.expected_failed_rules is not None
                    else None
                ),
            },
            "actual": {
                "intent": None,
                "route": None,
                "order_id": None,
                "risk_level": None,
                "risk_action": None,
                "requires_approval": None,
                "execution_success": None,
                "verification_success": None,
                "outcome": "ERROR",
                "run_status": None,
                "agent_status": None,
                "case_status": None,
                "eligible": None,
                "failed_rules": None,
            },
            "status": "FAIL",
            "failure_reasons": [f"RUN_ERROR: {error}"],
            "checks": [],
        }

    return evaluate_case(case, initial_payload or {}, final_payload or {})


def _reference_time(case: EvaluationCase) -> datetime | None:
    """Parse a case's pinned reference time (None => the real clock)."""
    if not case.reference_time:
        return None
    return datetime.fromisoformat(case.reference_time)
