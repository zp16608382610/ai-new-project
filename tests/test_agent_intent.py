"""Intent classification tests (Phase 4A).

Covers the Phase 4A checklist: knowledge question, order status, logistics,
refund inquiry vs request, cancel, ticket, unsupported, ambiguous, missing /
explicit order references, multiple-candidate ambiguity, confidence bounds and
deterministic output. DeterministicIntentClassifier is a rule-based TEST
implementation (not a production NLP classifier).
"""
import pytest

from app.agent.entities import DeterministicEntityExtractor
from app.agent.intent import (
    CandidateIntent,
    DeterministicIntentClassifier,
    IntentResult,
)
from app.agent.state import Intent


@pytest.fixture()
def classifier() -> DeterministicIntentClassifier:
    return DeterministicIntentClassifier()


@pytest.fixture()
def extractor() -> DeterministicEntityExtractor:
    return DeterministicEntityExtractor()


INTENT_CASES = [
    # checklist 1-9: intent classification of representative messages
    ("客服电话是多少？", Intent.KNOWLEDGE_QA),
    ("查一下订单状态", Intent.ORDER_STATUS),
    ("我的包怎么还没到？", Intent.LOGISTICS_TRACKING),
    ("为什么退款需要满足条件？", Intent.REFUND_INQUIRY),
    ("帮我把这笔订单退了", Intent.REFUND_REQUEST),
    ("帮我取消订单", Intent.CANCEL_ORDER),
    ("我要投诉", Intent.CREATE_TICKET),
    ("帮我写一份 Python 教程", Intent.UNSUPPORTED),
    ("我要退款，也想投诉", Intent.AMBIGUOUS),
    # spec section nine examples
    ("这个订单什么时候能退款？", Intent.REFUND_INQUIRY),
    ("为什么我的退款还没到账？", Intent.REFUND_INQUIRY),
    ("我要退款", Intent.REFUND_REQUEST),
    ("帮我把 ORD-1001 退款", Intent.REFUND_REQUEST),
    ("帮我查订单 ORD-1001 到哪了", Intent.LOGISTICS_TRACKING),
]


@pytest.mark.parametrize("message,expected", INTENT_CASES)
def test_classify_expected_intent(classifier, message, expected):
    result = classifier.classify(message)
    assert isinstance(result, IntentResult)
    assert result.intent is expected, (message, result.to_dict())
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.reasoning, str) and result.reasoning


def test_refund_inquiry_not_refund_request(classifier):
    """A refund question must not be routed to REFUND_REQUEST."""
    for message in ("退款政策是什么？", "什么时候能退款？", "退款怎么还没到账？"):
        result = classifier.classify(message)
        assert result.intent is Intent.REFUND_INQUIRY, message


def test_ambiguous_keeps_candidates(classifier):
    result = classifier.classify("帮我取消订单，也想申请退款")
    assert result.intent is Intent.AMBIGUOUS
    intents = {candidate.intent for candidate in result.candidates}
    assert Intent.CANCEL_ORDER in intents
    assert Intent.REFUND_REQUEST in intents
    assert len(result.candidates) >= 2


def test_empty_message_is_ambiguous(classifier):
    result = classifier.classify("   ")
    assert result.intent is Intent.AMBIGUOUS
    assert result.confidence == 0.0


def test_confidence_is_deterministic_and_bounded(classifier):
    first = classifier.classify("我要退款")
    second = classifier.classify("我要退款")
    assert first == second
    assert 0.5 <= first.confidence <= 1.0
    for candidate in first.candidates:
        assert 0.0 <= candidate.confidence <= 1.0


def test_explicit_order_id_extraction(extractor):
    entities = extractor.extract("帮我查订单 ORD-1001 到哪了")
    assert entities.order_id == "ORD-1001"
    assert entities.tracking_number is None
    assert entities.has_multiple_order_ids is False


def test_tracking_number_extraction(extractor):
    entities = extractor.extract("快递 SF10020002 到哪了")
    assert entities.tracking_number == "SF10020002"
    assert entities.order_id is None


def test_multiple_order_references_are_not_guessed(extractor):
    entities = extractor.extract("退 ORD-1001 和 ORD-1002")
    assert entities.order_id is None
    assert entities.has_multiple_order_ids is True


def test_no_entity_is_not_guessed(extractor):
    entities = extractor.extract("我要退款")
    assert entities.order_id is None
    assert entities.tracking_number is None
    assert entities.has_multiple_order_ids is False


@pytest.mark.parametrize(
    "message,expected",
    [
        ("帮我取消订单 ORD-1", "ORD-1"),
        ("帮我取消订单 ORD-2", "ORD-2"),
        ("帮我查订单 ORD-10 到哪了", "ORD-10"),
        ("ORD-100 到哪里了？", "ORD-100"),
        ("帮我取消订单 ORD-1001", "ORD-1001"),
        ("ORD-2001 到哪里了？", "ORD-2001"),
    ],
)
def test_short_and_long_order_references_extracted(extractor, message, expected):
    entities = extractor.extract(message)
    assert entities.order_id == expected
    assert entities.has_multiple_order_ids is False


def test_mixed_short_and_long_order_references_are_not_guessed(extractor):
    entities = extractor.extract("退 ORD-1001 和 ORD-2")
    assert entities.order_id is None
    assert entities.has_multiple_order_ids is True


def test_intent_result_serialization_roundtrip(classifier):
    result = classifier.classify("我要退款")
    data = result.to_dict()
    assert data["intent"] == "REFUND_REQUEST"
    assert isinstance(data["candidates"], list)
    assert data["candidates"][0]["intent"] == "REFUND_REQUEST"


def test_candidate_intent_schema():
    candidate = CandidateIntent(
        intent=Intent.ORDER_STATUS, confidence=0.8, reason="order markers"
    )
    assert candidate.intent is Intent.ORDER_STATUS
