"""Intent classification (Phase 4A).

Architecture:
    IntentClassifier is a replaceable interface. Phase 4A ships only
    DeterministicIntentClassifier - an explicit rule + priority + ambiguity
    implementation used for tests. A future LLMIntentClassifier (OpenAI
    Responses API) can replace it behind the same Protocol without the
    workflow knowing which concrete model is used.

Important: Intent != Route (see router.py). The classifier answers "what does
the user want", never "which code path runs". Route is a router concern.

The deterministic classifier is documented as a TEST implementation, not a
production-grade NLP classifier:
    - markers are matched on the NFKC-normalized surface form;
    - refund request vs refund inquiry is resolved by action/question
      markers (never by a bare "refund" keyword);
    - when two different intents are equally supported the result is
      AMBIGUOUS with both candidates preserved, so the workflow asks instead
      of guessing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from app.agent.state import Intent


@dataclass(frozen=True)
class CandidateIntent:
    """One plausible intent plus its deterministic confidence/evidence."""

    intent: Intent
    confidence: float
    reason: str


@dataclass(frozen=True)
class IntentResult:
    """Classifier output for one message."""

    intent: Intent
    confidence: float
    reasoning: str
    candidates: tuple[CandidateIntent, ...] = ()

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "candidates": [
                {
                    "intent": candidate.intent.value,
                    "confidence": candidate.confidence,
                    "reason": candidate.reason,
                }
                for candidate in self.candidates
            ],
        }


class IntentClassifier(Protocol):
    """Interface implemented by deterministic and future LLM classifiers."""

    def classify(self, text: str) -> IntentResult:
        """Classify one user message into one (or AMBIGUOUS) intent."""
        ...


# ---------------------------------------------------------------------------
# Deterministic rule vocabulary (test implementation - not production NLP).
# ---------------------------------------------------------------------------

_HANDOFF_MARKERS = (
    "转人工",
    "人工客服",
    "人工坐席",
    "人工处理",
    "人工服务",
    "找客服",
    "人工介入",
)

_TICKET_MARKERS = (
    "投诉",
    "举报",
    "工单",
    "申诉",
    "客服介入",
    "要求处理",
    "给我一个说法",
    "专员处理",
    "要个说法",
)

_CANCEL_MARKERS = (
    "取消订单",
    "取消这个订单",
    "取消这笔订单",
    "取消我的订单",
    "取消该订单",
    "把订单取消",
    "把单子取消",
    "撤销订单",
)

_ORDER_MARKERS = (
    "订单状态",
    "查询订单",
    "查一下订单",
    "查订单",
    "查看订单",
    "看看订单",
    "看看我的订单",
    "我的订单",
    "订单情况",
    "订单进度",
    "订单在哪",
    "订单在哪里",
    "订单是什么状态",
    "订单什么状态",
)

_LOGISTICS_NOUNS = (
    "物流",
    "快递",
    "包裹",
    "配送",
    "派送",
    "运单",
    "揽收",
    "运输",
    "货运",
    "在途",
    "我的包",
)

_DELIVERY_PROGRESS = (
    "到哪了",
    "到哪儿了",
    "到哪里了",
    "送到了吗",
    "怎么还没到",
    "还没到货",
    "没到货",
    "什么时候到",
    "多久能到",
    "发货了吗",
    "发货没有",
    "签收了吗",
    "派送中",
    "运送中",
)

_REFUND_DOMAIN = ("退款", "退货", "退换")

_REFUND_REQUEST_MARKERS = (
    "申请退款",
    "申请退货",
    "我要退款",
    "我想退款",
    "想要退款",
    "要求退款",
    "请退款",
    "麻烦退款",
    "帮我退款",
    "帮我退货",
    "退钱",
    "退货款",
    "办理退款",
    "发起退款",
    "直接退款",
    "给我退款",
    "全额退款",
    "立即退款",
)

# Action phrasing that may not contain the bare words 退款/退货, e.g.
# "帮我把这笔订单退了" or "帮我把 ORD-1001 退款".
_REFUND_ACTION_RE = re.compile(
    r"(?:帮我把|麻烦把|请把|把|将).{0,20}(?:退了|退掉|退款|退货|退单)"
)

_REFUND_INQUIRY_MARKERS = (
    "退款政策",
    "退款条件",
    "退款规则",
    "退款流程",
    "退款标准",
    "退款要求",
    "什么时候能退款",
    "多久能退款",
    "能退款吗",
    "可以退款吗",
    "能否退款",
    "是否支持退款",
    "符合退款",
    "退款到账",
    "还没到账",
    "没有到账",
    "没到账",
    "为什么我的退款",
    "退多少钱",
    "能退多少",
    "退款审核",
    "退款进度",
    "退款状态",
    "退货政策",
    "退货条件",
    "退货流程",
    "如何退货",
    "怎么退货",
    "怎么退款",
    "如何退款",
    "支持退货吗",
    "可以退货吗",
    "能退货吗",
)

_REFUND_QUESTION_WORDS = (
    "为什么",
    "怎么",
    "如何",
    "吗",
    "呢",
    "能不能",
    "可以",
    "是否",
    "什么",
    "多久",
    "条件",
    "政策",
    "规则",
    "流程",
    "标准",
    "需要满足",
    "到账",
    "进度",
    "状态",
    "什么时候",
)

_KNOWLEDGE_MARKERS = (
    "运费",
    "优惠券",
    "代金券",
    "满减",
    "保修",
    "发票",
    "七天无理由",
    "售后政策",
    "客服电话",
    "营业时间",
    "工作时间",
    "售后",
    "退换货政策",
)

_UNSUPPORTED_EXPLICIT = (
    "写一份python",
    "python教程",
    "写代码",
    "写一段代码",
    "翻译一下",
    "帮我写一份",
)


def _surface(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def _matches_any(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker in text]


def _confidence(hits: int, base: float = 0.55, per_hit: float = 0.12) -> float:
    return round(min(0.98, base + per_hit * min(4, hits)), 4)


class DeterministicIntentClassifier:
    """Deterministic rule-based classifier - architecture/test implementation.

    Not a production NLP classifier. Decision order (after surface
    normalization): handoff -> ticket complaint -> refund request -> cancel ->
    refund inquiry / bare-refund ambiguity -> logistics / order -> knowledge
    FAQ -> unsupported. Refund-context words shadow generic logistics wording
    so that "我的退款还没到账" is never read as parcel tracking.
    """

    name = "deterministic_rules"
    version = "1.0.0"

    def classify(self, text: str) -> IntentResult:
        surface = _surface(text)
        if not surface:
            return IntentResult(
                intent=Intent.AMBIGUOUS,
                confidence=0.0,
                reasoning="Empty message: not enough information to classify.",
            )

        ticket_hits = _matches_any(surface, _TICKET_MARKERS)
        cancel_hits = _matches_any(surface, _CANCEL_MARKERS)
        request_hits = self._refund_request_hits(surface)
        domain_hits = [marker for marker in _REFUND_DOMAIN if marker in surface]

        # 1) explicit human handoff -> escalate (outside automated scope)
        handoff = _matches_any(surface, _HANDOFF_MARKERS)
        if handoff:
            return self._single(
                Intent.UNSUPPORTED,
                _confidence(len(handoff), base=0.9, per_hit=0.02),
                "Explicit human handoff requested; automated agent escalates.",
                "matched markers: " + ", ".join(handoff),
            )

        # 2) complaint / ticket action alone
        if ticket_hits and not request_hits and not cancel_hits:
            return self._single(
                Intent.CREATE_TICKET,
                _confidence(len(ticket_hits), base=0.85, per_hit=0.02),
                "Message expresses a complaint / ticket action.",
                "matched markers: " + ", ".join(ticket_hits),
            )

        # 3) a refund action and a cancel action together -> ambiguous
        if request_hits and cancel_hits:
            return self._ambiguous(
                "Both a refund action and a cancel action are expressed.",
                self._candidates(
                    [
                        (Intent.REFUND_REQUEST, request_hits, "refund action markers"),
                        (Intent.CANCEL_ORDER, cancel_hits, "cancel action markers"),
                    ]
                ),
            )

        # 4) refund request (does not require the bare word 退款/退货)
        if request_hits:
            if ticket_hits:
                return self._ambiguous(
                    "Both a refund action and a complaint are expressed.",
                    self._candidates(
                        [
                            (Intent.REFUND_REQUEST, request_hits, "refund action markers"),
                            (Intent.CREATE_TICKET, ticket_hits, "complaint markers"),
                        ]
                    ),
                )
            return self._single(
                Intent.REFUND_REQUEST,
                _confidence(len(request_hits), base=0.85, per_hit=0.03),
                "Refund request action markers detected.",
                "matched markers: " + ", ".join(request_hits[:6]),
            )

        # 5) cancel order action
        if cancel_hits:
            return self._single(
                Intent.CANCEL_ORDER,
                _confidence(len(cancel_hits), base=0.85, per_hit=0.03),
                "Cancel order action markers detected.",
                "matched markers: " + ", ".join(cancel_hits),
            )

        # 6) refund domain without a request action -> inquiry or ambiguous
        if domain_hits:
            inquiry_hits = _matches_any(surface, _REFUND_INQUIRY_MARKERS)
            question_hits = _matches_any(surface, _REFUND_QUESTION_WORDS)
            if inquiry_hits or question_hits:
                evidence = inquiry_hits or question_hits
                return self._single(
                    Intent.REFUND_INQUIRY,
                    _confidence(
                        len(inquiry_hits) + len(question_hits), base=0.7, per_hit=0.08
                    ),
                    "Refund domain question / inquiry (not an action request).",
                    "matched markers: " + ", ".join(evidence[:6]),
                )
            return self._ambiguous(
                "Refund domain mentioned without an action or a question - "
                "cannot tell a request from an inquiry.",
                self._candidates(
                    [
                        (Intent.REFUND_INQUIRY, ["refund domain"], "may be an inquiry"),
                        (Intent.REFUND_REQUEST, ["refund domain"], "may be a request"),
                    ]
                ),
                base_confidence=0.3,
            )

        # 7) logistics tracking vs order status
        has_order_ref = ("订单" in surface) or ("单子" in surface) or bool(
            re.search(r"(?i)(?:ord|order)[-_ ]?\d{3,}", surface)
        )
        logistics_nouns = _matches_any(surface, _LOGISTICS_NOUNS)
        progress_hits = _matches_any(surface, _DELIVERY_PROGRESS)
        if logistics_nouns or progress_hits:
            evidence_parts = logistics_nouns + progress_hits
            return self._single(
                Intent.LOGISTICS_TRACKING,
                _confidence(len(evidence_parts), base=0.75, per_hit=0.06),
                "Logistics / delivery tracking context detected.",
                "matched markers: " + ", ".join(evidence_parts[:6]),
            )

        order_markers = _matches_any(surface, _ORDER_MARKERS)
        if order_markers or (has_order_ref and _contains_query_verb(surface)):
            evidence = order_markers or ["order reference present"]
            return self._single(
                Intent.ORDER_STATUS,
                _confidence(len(order_markers), base=0.75, per_hit=0.08),
                "Order lookup intent detected.",
                "matched markers: " + ", ".join(evidence[:6]),
            )

        # 8) static knowledge FAQ
        knowledge_hits = _matches_any(surface, _KNOWLEDGE_MARKERS)
        if knowledge_hits:
            return self._single(
                Intent.KNOWLEDGE_QA,
                _confidence(len(knowledge_hits), base=0.7, per_hit=0.06),
                "Static knowledge / FAQ topic detected.",
                "matched markers: " + ", ".join(knowledge_hits),
            )

        # 9) explicit out-of-scope asks, otherwise unsupported
        unsupported_hits = _matches_any(surface, _UNSUPPORTED_EXPLICIT)
        if unsupported_hits:
            return self._single(
                Intent.UNSUPPORTED,
                0.9,
                "Explicitly outside the e-commerce after-sales scope.",
                "matched markers: " + ", ".join(unsupported_hits),
            )

        return self._single(
            Intent.UNSUPPORTED,
            0.5,
            "No supported customer-service intent matched; treat as unsupported.",
            "",
        )

    # -- helpers ------------------------------------------------------------

    @classmethod
    def _refund_request_hits(cls, surface: str) -> list[str]:
        literal_hits = _matches_any(surface, _REFUND_REQUEST_MARKERS)
        if _REFUND_ACTION_RE.search(surface):
            literal_hits.append("action-phrase")
        return sorted(set(literal_hits))

    @staticmethod
    def _single(
        intent: Intent, confidence: float, reasoning: str, evidence: str
    ) -> IntentResult:
        candidate = CandidateIntent(
            intent=intent, confidence=confidence, reason=evidence or reasoning
        )
        return IntentResult(
            intent=intent,
            confidence=confidence,
            reasoning=reasoning,
            candidates=(candidate,),
        )

    @staticmethod
    def _candidates(groups: list[tuple[Intent, list[str], str]]) -> list[CandidateIntent]:
        result: list[CandidateIntent] = []
        for intent, hits, reason in groups:
            result.append(
                CandidateIntent(
                    intent=intent,
                    confidence=_confidence(len(hits), base=0.6, per_hit=0.1),
                    reason=reason,
                )
            )
        return result

    @classmethod
    def _ambiguous(
        cls,
        reasoning: str,
        candidates: list[CandidateIntent],
        *,
        base_confidence: float = 0.35,
    ) -> IntentResult:
        confidence = round(min(0.6, base_confidence + 0.05 * len(candidates)), 4)
        return IntentResult(
            intent=Intent.AMBIGUOUS,
            confidence=confidence,
            reasoning=reasoning,
            candidates=tuple(candidates),
        )


_QUERY_VERBS = ("查", "查询", "看看", "查一下", "帮我查", "问一下", "什么状态")


def _contains_query_verb(text: str) -> bool:
    return any(verb in text for verb in _QUERY_VERBS)