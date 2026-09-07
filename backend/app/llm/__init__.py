"""LLM abstraction layer (Phase 7B).

Public surface used by the Agent workflow / demo wiring:
    - LLMProvider            minimal OpenAI-compatible interface
    - DeepSeekProvider       real provider (httpx, no Agent framework)
    - LLMIntentExtractor     NLU -> validated IntentProposal (fallback-safe)
    - FinalResponder         grounded final response from authoritative evidence
    - LLMError / LLMErrorCode normalized errors (never leak to the user)
"""
from app.llm.base import LLMProvider
from app.llm.deepseek import DeepSeekProvider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.nlu import LLMIntentExtractor, LLMIntentProposal
from app.llm.respond import FinalResponder

__all__ = [
    "LLMError",
    "LLMErrorCode",
    "LLMIntentExtractor",
    "LLMIntentProposal",
    "LLMProvider",
    "DeepSeekProvider",
    "FinalResponder",
]
