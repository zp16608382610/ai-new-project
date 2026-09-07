"""Phase 7B LLM provider tests (offline, no real API key / network).

Covers the LLM abstraction requirements:
    - successful response
    - timeout
    - provider error / auth error / rate limit
    - malformed structured output
    - missing API key
    - API key never leaks into exceptions
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm.base import LLMProvider
from app.llm.deepseek import DeepSeekProvider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.nlu import (
    LLMIntentExtractor,
    canonical_order_id,
    parse_proposal_text,
)

TEST_KEY = "sk-test-not-a-real-key"


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def _provider(handler: httpx.MockTransport) -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=TEST_KEY,
        transport=httpx.MockTransport(handler),
    )


def _chat_completion(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_generate_success_returns_text() -> None:
    provider = _provider(lambda request: _completion("你好,我是客服。"))
    messages = [{"role": "user", "content": "hi"}]
    assert provider.generate(messages) == "你好,我是客服。"


def test_generate_json_mode_sets_response_format() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return _completion('{"intent": "ORDER_STATUS"}')

    provider = _provider(handler)
    provider.generate(
        [{"role": "user", "content": "x"}], json_mode=True
    )
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["stream"] is False
    assert seen["payload"]["model"] == "deepseek-chat"


def test_timeout_maps_to_llm_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    provider = _provider(handler)
    with pytest.raises(LLMError) as exc_info:
        provider.generate([{"role": "user", "content": "x"}])
    assert exc_info.value.code is LLMErrorCode.LLM_TIMEOUT
    assert TEST_KEY not in str(exc_info.value)


def test_auth_error_maps_and_never_leaks_key() -> None:
    provider = _provider(lambda request: httpx.Response(401, json={"error": "bad"}))
    with pytest.raises(LLMError) as exc_info:
        provider.generate([{"role": "user", "content": "x"}])
    assert exc_info.value.code is LLMErrorCode.LLM_AUTH_ERROR
    assert TEST_KEY not in str(exc_info.value)


def test_rate_limited_maps_to_rate_limit() -> None:
    provider = _provider(lambda request: httpx.Response(429, json={}))
    with pytest.raises(LLMError) as exc_info:
        provider.generate([{"role": "user", "content": "x"}])
    assert exc_info.value.code is LLMErrorCode.LLM_RATE_LIMITED


def test_provider_5xx_maps_to_provider_error() -> None:
    provider = _provider(lambda request: httpx.Response(503, json={}))
    with pytest.raises(LLMError) as exc_info:
        provider.generate([{"role": "user", "content": "x"}])
    assert exc_info.value.code is LLMErrorCode.LLM_PROVIDER_ERROR


def test_malformed_completion_is_invalid_output() -> None:
    provider = _provider(
        lambda request: httpx.Response(200, json={"choices": [{"message": {}}]})
    )
    with pytest.raises(LLMError) as exc_info:
        provider.generate([{"role": "user", "content": "x"}])
    assert exc_info.value.code is LLMErrorCode.LLM_INVALID_OUTPUT


def test_missing_api_key_is_config_error_without_network() -> None:
    with pytest.raises(LLMError) as exc_info:
        DeepSeekProvider(api_key="   ")
    assert exc_info.value.code is LLMErrorCode.LLM_CONFIG_ERROR


def test_structured_parse_accepts_fenced_json() -> None:
    raw = '```json\n{"intent": "REFUND_INQUIRY", "confidence": 0.9, "order_id": "ORD-1001"}\n```'
    proposal = parse_proposal_text(raw)
    assert proposal is not None
    assert proposal.intent == "REFUND_INQUIRY"
    assert canonical_order_id(proposal.order_id) == "ORD-1001"


def test_structured_parse_rejects_malformed_output() -> None:
    assert parse_proposal_text("sorry, i cannot do that") is None
    assert parse_proposal_text("") is None
    assert parse_proposal_text('{"intent": "MADE_UP"}') is None
    assert parse_proposal_text('{"intent": "ORDER_STATUS", "confidence": 2.5}') is None


def test_extractor_returns_none_when_provider_fails() -> None:
    class FailingProvider:
        provider_name = "Failing"
        model = "x"

        def generate(self, messages, *, temperature=0.0, max_tokens=300, json_mode=False):
            raise LLMError(LLMErrorCode.LLM_PROVIDER_ERROR, "boom")

    extractor = LLMIntentExtractor(FailingProvider())
    assert extractor.understand("退款规则是什么?") is None


def test_extractor_parses_valid_json_proposal() -> None:
    class JsonProvider:
        provider_name = "Json"
        model = "x"
        seen = []

        def generate(self, messages, *, temperature=0.0, max_tokens=300, json_mode=False):
            self.seen.append((messages, json_mode))
            return json.dumps(
                {
                    "intent": "LOGISTICS_TRACKING",
                    "confidence": 0.95,
                    "reasoning": "delivery question",
                    "order_id": "ORD-1001",
                    "tracking_number": None,
                },
                ensure_ascii=False,
            )

    fake = JsonProvider()
    extractor = LLMIntentExtractor(fake)
    proposal = extractor.understand("我的包裹到哪了?")
    assert proposal is not None
    assert proposal.intent == "LOGISTICS_TRACKING"
    assert canonical_order_id(proposal.order_id) == "ORD-1001"
    assert fake.seen[0][1] is True


def test_canonical_order_id_supports_any_digit_length() -> None:
    for ref in ("ORD-1", "ORD-2", "ORD-10", "ORD-100", "ORD-1001", "ORD-2001"):
        assert canonical_order_id(ref) == ref
    for bare in ("1", "2", "10", "100", "1001", "2001"):
        assert canonical_order_id(bare) == f"ORD-{bare}"


def test_canonical_order_id_rejects_non_order_references() -> None:
    for ref in ("", "ORD-", "ORD-x", "12a", "-2", "order id"):
        assert canonical_order_id(ref) is None


def test_provider_name_and_model_metadata() -> None:
    provider = DeepSeekProvider(api_key=TEST_KEY, model="deepseek-reasoner")
    assert provider.provider_name == "DeepSeek"
    assert provider.model == "deepseek-reasoner"
