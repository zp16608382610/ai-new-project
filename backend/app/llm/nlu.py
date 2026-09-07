"""LLM intent understanding (Phase 7B).

The LLM is used ONLY for understanding (intent + explicit entity extraction).
Its output is a structured proposal that is validated with Pydantic before it
can influence the workflow. On ANY failure (timeout / auth / rate limit /
provider error / malformed JSON / invalid enum) the extractor returns None and
the workflow falls back to the deterministic classifier - never a free-text
`if "refund" in text` heuristic.

The returned proposal only ever carries the user''s intent and explicit
entities. It cannot name a tool, cannot carry a user id, and cannot express an
amount, so it can never be turned into an unguarded tool execution.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.entities import ExtractedEntities
from app.agent.state import Intent
from app.llm.base import LLMProvider
from app.llm.errors import LLMError
from app.llm.prompts import NLU_TASK_INSTRUCTIONS

logger = logging.getLogger(__name__)

_ORDER_REF_RE = re.compile(r"(?i)^(?:ORD-?)?(\d{3,})$")
_ALLOWED_INTENT_VALUES = frozenset(item.value for item in Intent)


class LLMIntentProposal(BaseModel):
    """Validated structured LLM understanding output."""

    model_config = ConfigDict(extra="ignore")

    intent: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    reasoning: str = ""
    order_id: str | None = None
    tracking_number: str | None = None

    @field_validator("intent")
    @classmethod
    def _intent_must_be_known(cls, value: str) -> str:
        if value not in _ALLOWED_INTENT_VALUES:
            raise ValueError(f"Unknown intent: {value}")
        return value

    @field_validator("order_id", "tracking_number")
    @classmethod
    def _empty_string_is_null(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @property
    def intent_enum(self) -> Intent:
        return Intent(self.intent)


def parse_proposal_text(raw: str) -> LLMIntentProposal | None:
    """Strict JSON -> Pydantic validation. Returns None on any invalid output."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data: Any = json.loads(text[start : end + 1])
        if not isinstance(data, dict):
            return None
        return LLMIntentProposal.model_validate(data)
    except Exception:
        return None


def canonical_order_id(value: str | None) -> str | None:
    """Normalize an LLM-provided order reference to ORD-<digits> when possible."""
    if not value:
        return None
    match = _ORDER_REF_RE.match(str(value).strip())
    if match is None:
        return None
    return f"ORD-{match.group(1)}"


def proposal_entities(proposal: LLMIntentProposal | None) -> ExtractedEntities | None:
    """Map a validated proposal onto the agent entity vocabulary."""
    if proposal is None:
        return None
    return ExtractedEntities(
        order_id=canonical_order_id(proposal.order_id),
        tracking_number=proposal.tracking_number,
    )


class LLMIntentExtractor:
    """Understand one message through a provider; None means 'use fallback'."""

    def __init__(self, provider: LLMProvider, *, max_tokens: int = 200) -> None:
        self._provider = provider
        self._max_tokens = max_tokens

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    def understand(self, user_message: str) -> LLMIntentProposal | None:
        """Return a validated proposal or None (never raises to the caller)."""
        messages = [
            {"role": "system", "content": NLU_TASK_INSTRUCTIONS},
            {"role": "user", "content": f"用户消息:\n{user_message}\n\n请只输出上述 JSON 对象。"},
        ]
        try:
            raw = self._provider.generate(
                messages,
                temperature=0.0,
                max_tokens=self._max_tokens,
                json_mode=True,
            )
        except LLMError as exc:
            logger.warning("LLM intent extraction unavailable (%s)", exc.code.value)
            return None
        except Exception as exc:  # defensive: never let the LLM break the workflow
            logger.warning("LLM intent extraction failed: %s", type(exc).__name__)
            return None
        proposal = parse_proposal_text(raw)
        if proposal is None:
            logger.warning("LLM intent extraction returned invalid structured output")
        return proposal
