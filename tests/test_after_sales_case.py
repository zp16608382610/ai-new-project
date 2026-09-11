"""Phase 9A after-sales case domain model tests.

Strategy: in-memory SQLite (FK on) + the deterministic dev seed, same as the
Phase 2 data-layer tests. The domain model is exercised through
AfterSalesService; one extra test runs the REAL Alembic chain in a subprocess
because the rest of the suite only uses ``Base.metadata.create_all``.

Spec coverage: create / get / update status / collected_information /
missing_information / ai_summary / RiskLevel compatibility / migration +
initialisation / existing schema and seed unaffected.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.enums import (
    AfterSalesCaseStatus,
    AfterSalesCaseType,
    AfterSalesRequestedAction,
)
from app.db.models import AfterSalesCase, Logistics, Order, Refund, Ticket, User
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.risk.types import RiskLevel
from app.services.after_sales_service import AfterSalesCaseView, AfterSalesService
from app.services.errors import ConflictError, InvalidOperationError, NotFoundError


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    assert seed_dev_data(session) is True
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db_session, email: str = "alice@example.com") -> User:
    user = db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


def _order(db_session, user: User) -> Order:
    order = db_session.scalar(
        select(Order).where(Order.user_id == user.id).order_by(Order.id)
    )
    assert order is not None
    return order


def _create(db_session, **overrides) -> AfterSalesCaseView:
    """Create a case for Alice with deterministic defaults."""
    user = _user(db_session)
    order = _order(db_session, user)
    kwargs = {
        "user_id": user.id,
        "order_id": order.id,
        "case_type": AfterSalesCaseType.QUALITY_ISSUE,
        "requested_action": AfterSalesRequestedAction.REFUND,
        "problem_description": "Product arrived damaged",
    }
    kwargs.update(overrides)
    return AfterSalesService(db_session).create_case(**kwargs)


# ---- 1. create -----------------------------------------------------------


def test_create_case_persists_all_fields(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    case = AfterSalesService(db_session).create_case(
        user_id=user.id,
        order_id=order.id,
        case_type=AfterSalesCaseType.LOGISTICS_DISPUTE,
        requested_action=AfterSalesRequestedAction.EXCHANGE,
        problem_description="Parcel arrived two weeks late and the box was opened",
        collected_information={"order_id": order.id, "carrier": "ZTO"},
        missing_information=["purchase_proof"],
    )

    assert case.id is not None
    assert case.case_id.startswith("CASE-")
    assert case.user_id == user.id
    assert case.order_id == order.id
    assert case.case_type == AfterSalesCaseType.LOGISTICS_DISPUTE.value
    assert case.requested_action == AfterSalesRequestedAction.EXCHANGE.value
    assert case.status == AfterSalesCaseStatus.INFORMATION_COLLECTION.value
    assert case.risk_level == RiskLevel.LOW.value
    assert case.created_at is not None and case.updated_at is not None

    row = db_session.get(AfterSalesCase, case.id)
    assert row is not None
    assert row.case_id == case.case_id
    assert row.problem_description == case.problem_description


def test_create_case_without_order_is_allowed(db_session):
    case = _create(db_session, order_id=None, missing_information=["order_id"])
    assert case.order_id is None
    assert case.missing_information == ["order_id"]


def test_create_case_rejects_unknown_user_or_order(db_session):
    service = AfterSalesService(db_session)
    with pytest.raises(NotFoundError) as user_err:
        service.create_case(user_id=99999, problem_description="x")
    assert user_err.value.code == "USER_NOT_FOUND"

    with pytest.raises(NotFoundError) as order_err:
        service.create_case(
            user_id=_user(db_session).id, order_id=99999, problem_description="x"
        )
    assert order_err.value.code == "ORDER_NOT_FOUND"


def test_explicit_duplicate_case_id_is_rejected(db_session):
    service = AfterSalesService(db_session)
    user = _user(db_session)
    service.create_case(
        user_id=user.id, problem_description="first", case_id="CASE-FIXED-1"
    )
    with pytest.raises(ConflictError) as err:
        service.create_case(
            user_id=user.id, problem_description="second", case_id="CASE-FIXED-1"
        )
    assert err.value.code == "CASE_ALREADY_EXISTS"


# ---- 2. get --------------------------------------------------------------


def test_get_case_returns_persisted_case(db_session):
    created = _create(db_session)
    fetched = AfterSalesService(db_session).get_case(created.case_id)
    assert fetched == created


def test_get_unknown_case_raises_not_found(db_session):
    with pytest.raises(NotFoundError) as err:
        AfterSalesService(db_session).get_case("CASE-DOES-NOT-EXIST")
    assert err.value.code == "CASE_NOT_FOUND"


# ---- 3. update status ----------------------------------------------------


def test_update_case_status_progression(db_session):
    service = AfterSalesService(db_session)
    case = _create(db_session)

    step1 = service.update_case(
        case.case_id, status=AfterSalesCaseStatus.ELIGIBILITY_CHECK
    )
    assert step1.status == AfterSalesCaseStatus.ELIGIBILITY_CHECK.value

    step2 = service.update_case(case.case_id, status="PENDING_HUMAN")
    assert step2.status == AfterSalesCaseStatus.PENDING_HUMAN.value

    step3 = service.update_case(case.case_id, status=AfterSalesCaseStatus.COMPLETED)
    assert step3.status == AfterSalesCaseStatus.COMPLETED.value

    # partial update: untouched fields survive
    assert step3.problem_description == case.problem_description
    assert step3.case_type == case.case_type
    assert step3.updated_at >= step3.created_at
    assert db_session.get(AfterSalesCase, case.id).status is AfterSalesCaseStatus.COMPLETED


def test_update_unknown_case_raises_not_found(db_session):
    with pytest.raises(NotFoundError) as err:
        AfterSalesService(db_session).update_case("CASE-NOPE", status="COMPLETED")
    assert err.value.code == "CASE_NOT_FOUND"


# ---- 4. collected_information -------------------------------------------


def test_collected_information_is_persisted(db_session):
    payload = {"order_id": 4, "damaged": True, "photos": ["front.jpg", "back.jpg"]}
    case = _create(db_session, collected_information=payload)
    assert case.collected_information == payload
    assert (
        AfterSalesService(db_session).get_case(case.case_id).collected_information
        == payload
    )
    assert db_session.get(AfterSalesCase, case.id).collected_information == payload


def test_update_replaces_collected_information(db_session):
    service = AfterSalesService(db_session)
    case = _create(db_session, collected_information={"damaged": True})
    updated = service.update_case(
        case.case_id, collected_information={"damaged": True, "order_id": 4}
    )
    assert updated.collected_information == {"damaged": True, "order_id": 4}


# ---- 5. missing_information ---------------------------------------------


def test_missing_information_is_persisted(db_session):
    case = _create(db_session, missing_information=["order_id", "purchase_proof"])
    assert case.missing_information == ["order_id", "purchase_proof"]
    assert db_session.get(AfterSalesCase, case.id).missing_information == [
        "order_id",
        "purchase_proof",
    ]


def test_missing_information_can_be_cleared_with_empty_list(db_session):
    service = AfterSalesService(db_session)
    case = _create(db_session, missing_information=["order_id"])
    updated = service.update_case(case.case_id, missing_information=[])
    assert updated.missing_information == []


# ---- 6. ai_summary -------------------------------------------------------


def test_ai_summary_is_persisted(db_session):
    summary = (
        "Customer reports the earbuds stopped charging within two days. "
        "Order verified; refund eligibility not yet decided. Needs human review."
    )
    case = _create(db_session, ai_summary=summary)
    assert case.ai_summary == summary
    assert AfterSalesService(db_session).get_case(case.case_id).ai_summary == summary
    assert db_session.get(AfterSalesCase, case.id).ai_summary == summary


def test_ai_summary_is_only_written_when_provided(db_session):
    service = AfterSalesService(db_session)
    case = _create(db_session, ai_summary="first draft")
    updated = service.update_case(case.case_id, status=AfterSalesCaseStatus.PROCESSING)
    assert updated.ai_summary == "first draft"


# ---- 7. risk_level compatibility ----------------------------------------


def test_risk_level_reuses_phase5_vocabulary(db_session):
    service = AfterSalesService(db_session)
    user = _user(db_session)
    for level in RiskLevel:
        case = service.create_case(
            user_id=user.id, problem_description="x", risk_level=level
        )
        assert case.risk_level == level.value
        assert RiskLevel(case.risk_level) is level

    from_string = service.create_case(
        user_id=user.id, problem_description="x", risk_level="CRITICAL"
    )
    assert from_string.risk_level == RiskLevel.CRITICAL.value
    assert db_session.get(AfterSalesCase, from_string.id).risk_level == "CRITICAL"


def test_invalid_risk_level_is_rejected(db_session):
    with pytest.raises(InvalidOperationError) as err:
        _create(db_session, risk_level="SUPER_DANGEROUS")
    assert err.value.code == "INVALID_RISK_LEVEL"


def test_invalid_enum_values_and_payloads_are_rejected(db_session):
    service = AfterSalesService(db_session)
    user = _user(db_session)

    with pytest.raises(InvalidOperationError) as status_err:
        service.create_case(user_id=user.id, problem_description="x", status="BOGUS")
    assert status_err.value.code == "INVALID_STATUS"

    with pytest.raises(InvalidOperationError) as type_err:
        service.create_case(user_id=user.id, problem_description="x", case_type="BOGUS")
    assert type_err.value.code == "INVALID_CASE_TYPE"

    with pytest.raises(InvalidOperationError) as action_err:
        service.create_case(
            user_id=user.id, problem_description="x", requested_action="BOGUS"
        )
    assert action_err.value.code == "INVALID_REQUESTED_ACTION"

    with pytest.raises(InvalidOperationError) as collected_err:
        service.create_case(
            user_id=user.id,
            problem_description="x",
            collected_information=["not", "a", "dict"],
        )
    assert collected_err.value.code == "INVALID_COLLECTED_INFORMATION"

    with pytest.raises(InvalidOperationError) as missing_err:
        service.create_case(
            user_id=user.id,
            problem_description="x",
            missing_information={"not": "a list"},
        )
    assert missing_err.value.code == "INVALID_MISSING_INFORMATION"


# ---- list ----------------------------------------------------------------


def test_list_cases_filters_by_user_status_and_type(db_session):
    service = AfterSalesService(db_session)
    alice = _user(db_session)
    bob = _user(db_session, "bob@example.com")

    alice_case = _create(db_session)
    bob_case = service.create_case(
        user_id=bob.id,
        problem_description="late delivery",
        case_type=AfterSalesCaseType.LOGISTICS_DISPUTE,
    )
    service.update_case(alice_case.case_id, status=AfterSalesCaseStatus.PENDING_HUMAN)

    assert {c.case_id for c in service.list_cases(user_id=alice.id)} == {
        alice_case.case_id
    }
    assert [c.case_id for c in service.list_cases(status="PENDING_HUMAN")] == [
        alice_case.case_id
    ]
    assert [c.case_id for c in service.list_cases(user_id=bob.id)] == [bob_case.case_id]
    assert [
        c.case_id
        for c in service.list_cases(case_type=AfterSalesCaseType.LOGISTICS_DISPUTE)
    ] == [bob_case.case_id]


# ---- 8. database initialisation / migration -----------------------------


def test_new_table_does_not_affect_existing_seed(db_session):
    counts_before = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in (User, Order, Refund, Ticket, Logistics)
    }
    case = _create(db_session)
    counts_after = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in (User, Order, Refund, Ticket, Logistics)
    }
    assert counts_before == counts_after
    assert case.order_id is not None


def test_database_fk_and_check_constraints_are_enforced(db_session):
    user = _user(db_session)
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.add(
            AfterSalesCase(
                case_id="CASE-FK-VIOLATION",
                user_id=99999,
                problem_description="ghost user",
            )
        )
        db_session.flush()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO after_sales_cases "
                "(case_id, user_id, case_type, requested_action, problem_description, status, risk_level) "
                "VALUES ('CASE-BAD-STATUS', :u, 'OTHER', 'UNKNOWN', 'x', 'BOGUS', 'LOW')"
            ),
            {"u": user.id},
        )
    db_session.rollback()


def test_alembic_chain_creates_after_sales_cases(tmp_path):
    """Run the real migration chain on a scratch SQLite file (not create_all)."""
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    db_path = tmp_path / "migrated.db"
    url = f"sqlite+pysqlite:///{db_path.as_posix()}"
    env = {**os.environ, "DATABASE_URL": url, "LLM_ENABLED": "false"}

    def run_alembic(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
        )

    def schema_snapshot():
        engine = create_db_engine(url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            columns = (
                {c["name"] for c in inspector.get_columns("after_sales_cases")}
                if "after_sales_cases" in tables
                else set()
            )
            indexes = (
                inspector.get_indexes("after_sales_cases")
                if "after_sales_cases" in tables
                else []
            )
            return tables, columns, indexes
        finally:
            engine.dispose()

    first = run_alembic("upgrade", "head")
    assert first.returncode == 0, first.stderr
    second = run_alembic("upgrade", "head")  # idempotent: already at head
    assert second.returncode == 0, second.stderr

    tables, columns, indexes = schema_snapshot()
    expected_columns = {
        "id",
        "case_id",
        "user_id",
        "order_id",
        "case_type",
        "requested_action",
        "problem_description",
        "status",
        "risk_level",
        "collected_information",
        "missing_information",
        "ai_summary",
        "created_at",
        "updated_at",
    }
    assert expected_columns <= columns
    assert {
        "users",
        "products",
        "orders",
        "order_items",
        "logistics",
        "refunds",
        "tickets",
        "approval_requests",
        "knowledge_documents",
        "knowledge_chunks",
    } <= tables
    unique_case_ids = [
        index for index in indexes if index["name"] == "ix_after_sales_cases_case_id"
    ]
    # SQLite reports the flag as 1 instead of True, so compare truthiness.
    assert unique_case_ids and unique_case_ids[0]["unique"]

    downgraded = run_alembic("downgrade", "-1")
    assert downgraded.returncode == 0, downgraded.stderr
    tables_after_downgrade, _, _ = schema_snapshot()
    assert "after_sales_cases" not in tables_after_downgrade
    assert "orders" in tables_after_downgrade

    reupgraded = run_alembic("upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    tables_after_reupgrade, _, _ = schema_snapshot()
    assert "after_sales_cases" in tables_after_reupgrade
