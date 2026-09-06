"""Agent layer (Phase 4A) - state, intent, routing and workflow.

Phase 4A scope:
    AgentState
    + Intent Classification (deterministic test implementation)
    + Workflow Routing (Intent != Route)
    + RAG Branch (delegates to the existing retrieval pipeline)
    + Business Tool Branch interface (ToolRequest only - never executed)
    + Response State (AgentResult)

Boundaries honored by this package:
    - No MCP / HITL / Risk Control / refund execution / real LLM / Redis /
      LangSmith / OpenTelemetry / Evaluation Dashboard / Frontend Agent UI.
    - The agent layer never opens a database session and never writes SQL;
      the RAG branch only calls the existing RetrievalPipeline entry point.
    - LangGraph is deliberately NOT imported yet: workflow logic is
      framework-agnostic so business code never depends on a framework state
      object (see docs/DECISIONS.md Decision 019+).
"""
from app.agent.entities import DeterministicEntityExtractor, EntityExtractor, ExtractedEntities
from app.agent.intent import (
    CandidateIntent,
    DeterministicIntentClassifier,
    IntentClassifier,
    IntentResult,
)
from app.agent.router import (
    RouteDecision,
    RuleBasedRouter,
    WorkflowRouter,
)
from app.agent.state import (
    AgentResult,
    AgentResultStatus,
    AgentState,
    Intent,
    Route,
    ToolRequest,
    ToolRequestStatus,
    WorkflowStage,
)
from app.agent.workflow import AgentWorkflow, RetrievalRunner

__all__ = [
    "AgentResult",
    "AgentResultStatus",
    "AgentState",
    "AgentWorkflow",
    "CandidateIntent",
    "DeterministicEntityExtractor",
    "DeterministicIntentClassifier",
    "EntityExtractor",
    "ExtractedEntities",
    "Intent",
    "IntentClassifier",
    "IntentResult",
    "RetrievalRunner",
    "Route",
    "RouteDecision",
    "RuleBasedRouter",
    "ToolRequest",
    "ToolRequestStatus",
    "WorkflowRouter",
    "WorkflowStage",
]