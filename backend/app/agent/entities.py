"""Entity extraction for tool argument preparation (Phase 4A).

Design:
    EntityExtractor is a replaceable interface. Phase 4A ships a deterministic
    regular-expression implementation only; a future LLM/NER-based extractor
    can replace it behind the same interface without touching the workflow.

Scope (Phase 4A):
    - order_id          explicit order references (ORD-1234 / 订单号 1234)
    - tracking_number   carrier-prefixed tracking numbers (mock carriers)
    User identity is NOT extracted from free text: it comes from the session /
    auth context (AgentState.user_id). Guessing an identity from text would be
    a security anti-pattern.

"No guessing" rule:
    If a message references several distinct orders (or tracking numbers), the
    extractor does not silently pick one - the flags below let the router send
    the request to CLARIFY instead.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ExtractedEntities:
    """Entities found in the user message (deterministic subset)."""

    order_id: str | None = None
    tracking_number: str | None = None
    has_multiple_order_ids: bool = False
    has_multiple_tracking_numbers: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "order_id": self.order_id,
            "tracking_number": self.tracking_number,
            "has_multiple_order_ids": self.has_multiple_order_ids,
            "has_multiple_tracking_numbers": self.has_multiple_tracking_numbers,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ExtractedEntities":
        return cls(
            order_id=data.get("order_id"),
            tracking_number=data.get("tracking_number"),
            has_multiple_order_ids=bool(data.get("has_multiple_order_ids", False)),
            has_multiple_tracking_numbers=bool(
                data.get("has_multiple_tracking_numbers", False)
            ),
        )


class EntityExtractor(Protocol):
    """Interface implemented by the deterministic and future real extractors."""

    def extract(self, text: str) -> ExtractedEntities:
        """Return typed entities found in the message (never guesses)."""
        ...


class DeterministicEntityExtractor:
    """Deterministic regex extractor - architecture/test implementation."""

    name = "deterministic_regex"

    # Canonical agent-facing order reference: ORD-<digits>.
    _ORDER_ID_PATTERNS = (
        re.compile(r"(?i)(?<![a-z0-9])(?:ORD|ORDER)[-_ ]?(\d{3,})(?![a-z0-9])"),
        re.compile(r"订单号\s*[:：]?\s*(\d{3,})"),
        re.compile(r"(?i)order\s*(?:number|no\.?|#)?\s*[:：]?\s*(\d{3,})"),
    )
    # Mock carriers in the Phase 2 seed: SF / YT / ZT / ZTO ...
    _TRACKING_PATTERNS = (
        re.compile(r"(?i)(?<![a-z0-9])(?:SF|YTO|YT|ZT|ZTO|EMS|JD|STO)[-_ ]?(\d{4,})(?![a-z0-9])"),
    )

    @staticmethod
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text)

    def extract(self, text: str) -> ExtractedEntities:
        normalized = self._normalize(text or "")
        order_ids = self._match_ids(normalized, self._ORDER_ID_PATTERNS, prefix="ORD")
        tracking = self._match_ids(normalized, self._TRACKING_PATTERNS, prefix=None)
        return ExtractedEntities(
            order_id=None if len(order_ids) != 1 else order_ids[0],
            tracking_number=None if len(tracking) != 1 else tracking[0],
            has_multiple_order_ids=len(order_ids) > 1,
            has_multiple_tracking_numbers=len(tracking) > 1,
        )

    @staticmethod
    def _match_ids(text: str, patterns: tuple[re.Pattern[str], ...], *, prefix: str | None) -> list[str]:
        seen: list[str] = []
        seen_set: set[str] = set()
        for pattern in patterns:
            for match in pattern.finditer(text):
                digits = match.group(1)
                if prefix is not None:
                    canonical = f"{prefix}-{digits}"
                else:
                    leading = re.match(r"[a-zA-Z]+", match.group(0))
                    marker = leading.group(0) if leading else ""
                    canonical = f"{marker}{digits}"
                canonical = canonical.upper()
                if canonical not in seen_set:
                    seen_set.add(canonical)
                    seen.append(canonical)
        return seen