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

import contextlib
import os
import tempfile
import unittest.mock
from datetime import datetime, timezone
from typing import Any

from app.evaluation.dataset import (
    FAULT_EXECUTE_FAILURE,
    FAULT_VERIFY_FAILURE,
    EvaluationCase,
    get_dataset,
)
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

    cases = get_dataset(case_ids)

    case_results: list[dict[str, Any]] = []
    if database_url is None:
        # Phase 9D: every case gets its own throw-away database. Cases are not
        # independent when they share one DB (e.g. the refund cases leave an
        # in-flight refund for ORD-1003, which then trips `no_active_refund`
        # for unrelated after-sales cases). Isolating per case keeps every
        # case deterministic and order-independent.
        for case in cases:
            case_results.append(_run_case_isolated(case, use_llm=use_llm))
    else:
        # Caller pinned an explicit database: run the whole set against it
        # (the caller owns that DB's lifecycle).
        prepare_demo_database(database_url)
        engine = create_db_engine(database_url)
        session = create_session_factory(engine)()
        try:
            for case in cases:
                case_results.append(_run_case(session, case, use_llm=use_llm))
        finally:
            session.close()
            engine.dispose()

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


def _run_case_isolated(case: EvaluationCase, *, use_llm: bool) -> dict[str, Any]:
    """Run one case against its own freshly seeded, disposable SQLite file."""
    from app.db.session import create_db_engine, create_session_factory
    from app.demo.demo_seed import prepare_demo_database

    url, path = _default_database_url()
    try:
        prepare_demo_database(url)
        engine = create_db_engine(url)
        session = create_session_factory(engine)()
        try:
            return _run_case(session, case, use_llm=use_llm)
        finally:
            session.close()
            engine.dispose()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


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
    with _case_faults(case):
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
            if (
                case.resolve_approval is not None
                and isinstance(approval, dict)
                and approval.get("id") is not None
            ):
                # resolve_approval=True approves, False rejects: both are real
                # human decisions the workflow must honour.
                final_payload = finalize_approval(
                    session,
                    store,
                    int(approval["id"]),
                    approved=bool(case.resolve_approval),
                    resolved_by="evaluation-runner",
                    use_llm=use_llm,
                )
            if case.retry_same_case:
                # Phase 9D: replay the SAME message against the SAME case so
                # ticket idempotency is really exercised (never a second ticket).
                final_payload = _retry_same_case(
                    session, store, case, final_payload or {}, use_llm=use_llm
                )
            if case.retry_after_completion:
                # Phase 9E: repeat the request after the case finished. The
                # business constraint (RefundService active-refund protection)
                # must refuse a second refund - proven by the refund count.
                final_payload = _replay_message(session, store, case, use_llm=use_llm)
        except Exception as exc:  # a failing case is reported, never hidden
            error = f"{type(exc).__name__}: {exc}"

    # Measured from this case's own isolated database: how many real refunds
    # exist after the run (the only honest proof of duplicate protection).
    refund_count = _count_refunds(session, case)

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
                "treatment_action": case.expected_treatment_action,
                "ticket_created": case.expected_ticket_created,
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
                "treatment_action": None,
                "ticket_created": None,
            },
            "status": "FAIL",
            "failure_reasons": [f"RUN_ERROR: {error}"],
            "checks": [],
        }

    return evaluate_case(
        case,
        initial_payload or {},
        final_payload or {},
        refund_count=refund_count,
    )


def _retry_same_case(session, store, case: EvaluationCase, payload: dict, *, use_llm: bool):
    """Replay one case's message against the SAME after-sales case.

    A duplicate-ticket risk only appears when the same case is handled twice
    (e.g. a retried request): the case state machine re-investigates a case
    only while it is in ELIGIBILITY_CHECK, so the retry restores that state and
    replays the identical message. The run store is reused on purpose: the
    session -> case link lives there.
    """
    from app.demo.service import run_chat

    case_block = (payload or {}).get("case") or {}
    case_id = case_block.get("case_id")
    if not case_id:
        return payload
    from app.after_sales.eligibility import STATUS_ELIGIBILITY_CHECK
    from app.services.after_sales_service import AfterSalesService

    AfterSalesService(session).update_case(case_id, status=STATUS_ELIGIBILITY_CHECK)
    return run_chat(
        session,
        store,
        message=case.user_message,
        user_id=case.user_id,
        session_id=f"eval-{case.case_id}",
        user_confirmed=case.user_confirmed,
        use_llm=use_llm,
        investigation_reference_time=_reference_time(case),
    )


@contextlib.contextmanager
def _case_faults(case: EvaluationCase):
    """Inject one deterministic, REAL failure into the existing chain.

    Evaluation only: the demo path never injects a fault. The injected failure
    happens inside the real RefundService / BusinessVerifier, so the runner
    observes a genuine failure path instead of a fabricated result.
    """
    stack = contextlib.ExitStack()
    with stack:
        if case.fault == FAULT_EXECUTE_FAILURE:
            from app.services.refund_service import RefundService

            def boom(*args, **kwargs):  # noqa: ANN002, ANN003 - test stub
                raise RuntimeError("simulated refund backend failure")

            stack.enter_context(
                unittest.mock.patch.object(RefundService, "create_refund", boom)
            )
        elif case.fault == FAULT_VERIFY_FAILURE:
            from app.services.errors import VerificationFailedError
            from app.services.verification import BusinessVerifier

            def refuse(self, tool_name, data):  # noqa: ANN001 - test stub
                if tool_name == "create_refund":
                    raise VerificationFailedError(
                        "simulated verification failure: refund not confirmed"
                    )

            stack.enter_context(
                unittest.mock.patch.object(BusinessVerifier, "verify", refuse)
            )
        yield


def _count_refunds(session, case: EvaluationCase) -> int:
    """Real refund rows for the case's order in its isolated database.

    Counting only the case's own order keeps the seed's unrelated refunds out of
    the duplicate-execution measurement.
    """
    from sqlalchemy import select

    from app.db.models import Refund

    rows = list(session.scalars(select(Refund)).all())
    order_ref = case.expected_order_id
    if not order_ref:
        return len(rows)
    try:
        order_id = int(str(order_ref).upper().replace("ORD-", "").replace("ORD", ""))
    except ValueError:
        return len(rows)
    return sum(1 for row in rows if row.order_id == order_id)


def _replay_message(session, store, case: EvaluationCase, *, use_llm: bool):
    """Send the same message again after the case reached a terminal state."""
    from app.demo.service import run_chat

    return run_chat(
        session,
        store,
        message=case.user_message,
        user_id=case.user_id,
        session_id=f"eval-{case.case_id}",
        user_confirmed=case.user_confirmed,
        use_llm=use_llm,
        investigation_reference_time=_reference_time(case),
    )


def _reference_time(case: EvaluationCase) -> datetime | None:
    """Parse a case's pinned reference time (None => the real clock)."""
    if not case.reference_time:
        return None
    return datetime.fromisoformat(case.reference_time)
