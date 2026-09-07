# Decision Records

> 记录当前已确定的架构决策。新决策追加编号,不改写历史条目;如需推翻,新增一条「Supersedes」记录。

| ID | 主题 | 状态 | 日期 |
| --- | --- | --- | --- |
| 001 | Static knowledge vs dynamic data | Accepted | 2026-09-06 |
| 002 | LangGraph | Accepted | 2026-09-06 |
| 003 | MCP | Accepted | 2026-09-06 |
| 004 | Risk Control | Accepted | 2026-09-06 |
| 005 | Human-in-the-loop | Accepted | 2026-09-06 |
| 006 | Knowledge versioning | Accepted | 2026-09-06 |
| 007 | Knowledge chunking strategy | Accepted | 2026-09-06 |
| 008 | Ingestion idempotency | Accepted | 2026-09-06 |
| 009 | Embedding abstraction | Accepted | 2026-09-06 |
| 010 | Hybrid dense+sparse retrieval | Accepted | 2026-09-06 |
| 011 | BM25 implementation (stdlib) | Accepted | 2026-09-06 |
| 012 | RRF fusion | Accepted | 2026-09-06 |
| 013 | pgvector integration strategy | Accepted | 2026-09-06 |
| 014 | Retrieval metadata/lifecycle filtering | Accepted | 2026-09-06 |
| 015 | Retrieval vs reranking separation | Accepted | 2026-09-06 |
| 016 | Context assembly independence | Accepted | 2026-09-06 |
| 017 | Token budget owned by context layer | Accepted | 2026-09-06 |
| 018 | Deterministic reranker (architecture/test only) | Accepted | 2026-09-06 |
| 019 | Intent and Route separation | Accepted | 2026-09-06 |
| 020 | AgentState as domain state (not framework state) | Accepted | 2026-09-06 |
| 021 | Agent layer has no direct database access | Accepted | 2026-09-06 |
| 022 | Static knowledge vs dynamic data / business actions (agent level) | Accepted | 2026-09-06 |
| 023 | Ambiguous requests are never guessed (CLARIFY) | Accepted | 2026-09-06 |
| 024 | LangGraph is orchestration only; Phase 4A stays framework-agnostic | Accepted | 2026-09-06 |
| 025 | Tool Registry is an explicit allowlist | Accepted | 2026-09-06 |
| 026 | Tool arguments are schema-validated | Accepted | 2026-09-06 |
| 027 | user_id comes from the trusted execution context | Accepted | 2026-09-06 |
| 028 | Agent never reaches the DB; tools are the execution boundary | Accepted | 2026-09-06 |
| 029 | Tools do not carry business rules | Accepted | 2026-09-06 |
| 030 | ToolResult uses stable domain schemas | Accepted | 2026-09-06 |
| 031 | Phase 4B executes one tool call first (refund = eligibility + conditional refund) | Accepted | 2026-09-06 |
## Decision 001 — Static knowledge vs dynamic data

**Decision:**
Static knowledge uses RAG.
Dynamic business data uses Tools.

**Reason:**
Business data changes frequently and should come from authoritative systems.

## Decision 002 — LangGraph

**Decision:**
LangGraph is the orchestration/runtime layer.

**Reason:**
The project contains stateful multi-step workflows, branching, retries, interruption and resume.

补充约束:业务逻辑不应重度依赖 LangGraph(编排层可替换,见 ARCHITECTURE §10)。

## Decision 003 — MCP

**Decision:**
MCP is used for standardized tool exposure.

**Reason:**
Decouple Agent orchestration from business tool services.

补充约束:MCP 不负责 Agent decision logic(决策在 Orchestrator 层)。

## Decision 004 — Risk Control

**Decision:**
High-risk operations cannot directly execute without risk evaluation.

**Reason:**
退款/取消等操作影响资金与履约,必须先经风控评估;等级定义见 PRD §7 与 ARCHITECTURE §6。

## Decision 005 — Human-in-the-loop

**Decision:**
High-risk or anomalous operations may interrupt the workflow and require human approval.

**Reason:**
对 CRITICAL 或风控命中/异常操作,以 Interrupt → Approval → Resume 流程转人工审批,全程留痕。

## Decision 006 — Knowledge versioning

**Decision:**
Knowledge documents are versioned and immutable: the natural key is (category, title, version); old versions are never overwritten.

**Reason:**
Enterprise knowledge bases change over time; retrieval must distinguish active vs archived versions and effective dates. Policy evolution happens by creating a new version, not by mutating history.

补充约束:同版本、不同内容在同一自然键下被拒绝(`KnowledgeIngestionError`),引导走「新建版本」路径。

## Decision 007 — Knowledge chunking strategy

**Decision:**
Phase 3A uses a deterministic, section-aware chunker (normalize → split by `##`/`###` section boundaries → split long sections by paragraph then characters), implemented with the Python standard library only.

**Reason:**
Prefer semantic/section-aware chunking over blind fixed-N character splits; traceable chunks (document_id + section) are the goal, not maximum sophistication. Keeping it dependency-free makes it easy to replace later without changing the ingestion interface.

## Decision 008 — Ingestion idempotency

**Decision:**
Idempotency is based on a document checksum: sha256 of the normalized UTF-8 text is stored on `knowledge_documents.checksum`; re-ingesting identical content is a no-op and never duplicates chunks.

**Reason:**
Deterministic seed knowledge must be safely repeatable (development, CI, re-seed) without uncontrolled duplicate rows.

## Decision 009 — Embedding abstraction

**Decision:**
Embedding access goes through an `EmbeddingProvider` interface (`embed(texts) -> list[list[float]]`). Phase 3A ships only a deterministic local stub (`DeterministicEmbeddingProvider`) used for interface verification; no vendor or vector framework is bound, and no real embedding is stored.

**Reason:**
Do not couple the knowledge model or ingestion pipeline to one embedding vendor before Phase 3B; keep the swap point clean and tests offline/deterministic. pgvector / real vectors belong to Phase 3B.

## Decision 010 — Hybrid dense+sparse retrieval

**Decision:**
Retrieval runs dense (vector similarity) and sparse (BM25) in parallel and fuses the ranked lists; the result exposes `retrieval_methods` per candidate so callers can audit which method(s) produced the hit.

**Reason:**
Dense captures paraphrase-overlap; sparse guarantees exact-term recall for policy keywords. Neither alone is sufficient for a mixed FAQ/policy corpus; fusing keeps the boundary testable per method and hybrid.

## Decision 011 — BM25 implementation / dependency

**Decision:**
BM25 is implemented in pure Python (standard library only): Okapi BM25 over a deterministic CJK tokenizer (sliding character bigrams with function-word bigram filtering and a minimum-shared-term gate).

**Reason:**
No dependency is justified for ~60 lines of BM25; LangChain (or any ranking library) solely for BM25 would violate dependency discipline. Char-bigram tokenization needs no external segmenter and is deterministic for the fixed Chinese corpus.

## Decision 012 — RRF fusion

**Decision:**
Fusion uses Reciprocal Rank Fusion (RRF, k=60): score(chunk) = Σ 1/(k + rank_in_method).

**Reason:**
Dense cosine and BM25 scores live on different scales; naive summation requires calibration. RRF is rank-based, needs no score normalization, is deterministic, and boosts chunks found by both retrievers.

## Decision 013 — pgvector integration strategy

**Decision:**
PostgreSQL + pgvector is the production vector path; in this environment (no Docker/PostgreSQL) the vector database boundary stays clean and unverified:
- `DenseRetriever` is the swap interface for a future pgvector-backed implementation.
- Local dev/tests use the deterministic `EmbeddingProvider` path; no vector column/migration is added and no unrelated vector DB is substituted.
- Real embedding model/provider selection and `knowledge_chunks` embedding column land together with pgvector in a later phase, and pgvector must not be claimed working until actually integration-tested.

**Reason:**
Avoid fabricating integration results; keep retrieval independently testable now and swappable later.

## Decision 014 — Retrieval metadata / lifecycle filtering

**Decision:**
Filtering uses a simple typed `RetrievalFilter(status/category/language)` — no filtering DSL. The repository layer enforces `status == ACTIVE` by default (Phase 3A lifecycle rule), so ARCHIVED/DRAFT documents cannot silently compete with ACTIVE versions; explicit `status=None` is an internal/test-only override.

**Reason:**
Simple, typed, and testable; lifecycle correctness (active-version-only retrieval) is enforced at the single data-access boundary instead of being re-implemented per retriever.

## Decision 015 — Retrieval vs reranking separation

**Decision:**
Retrieval (candidate generation) and reranking are separate layers with separate interfaces: retrieval produces typed, traceable candidates; reranking is a pluggable pass that re-orders those candidates for the final Top-N.

**Reason:**
Retrieval optimizes recall with cheap/fast methods (dense + BM25 + RRF); reranking optimizes precision for what finally matters. Splitting the layers keeps each independently testable and lets a real Cross-Encoder / LLM-based reranker / provider reranking API replace the local implementation without touching retrieval or context assembly.

## Decision 016 — Context assembly independence

**Decision:**
A dedicated context assembly layer decides what finally reaches the LLM: dedupe, ACTIVE-version preference, ordering, chunk-count cap, token budget, and per-item citation/traceability.

**Reason:**
「Retrieval 找候选,Reranker 负责相关性排序,Context Assembly 负责决定最终给模型什么」。Keeping assembly independent prevents mixing search logic with prompt construction and grounds any future generation strictly in the assembled context (Grounding Boundary).

## Decision 017 — Token budget owned by the context layer

**Decision:**
Token budget lives in the context layer as a typed `ContextBudget` (max_tokens / reserve_tokens). Without a tokenizer dependency, `estimate_tokens()` is a deterministic approximation (documented as approximation, not a real tokenizer); budget numbers never scatter across callers.

**Reason:**
Centralizing the budget prevents over-budget contexts, reserves tokens for future system/user prompts and the answer, and gives one swap point for a tokenizer-backed implementation later.

## Decision 018 — Deterministic reranker is architecture/test implementation only

**Decision:**
Phase 3C ships only `DeterministicReranker`, a deterministic, standard-library, lexical scoring implementation. It is explicitly NOT a semantic reranker and must not be described as one.

**Reason:**
The phase focuses on architecture and testability, not model quality. Keeping the `Reranker` interface as the swap point avoids adding heavyweight ML dependencies (transformers / torch / sentence-transformers) without a proven need; a semantic reranker can be introduced behind the same interface in a later phase.
## Decision 019 — Intent and Route are separate

**Decision:**
Intent (what the user wants) and Route (which pipeline step runs next) are separate typed enums
(`app/agent/state.py`). The router only maps Intent + extracted entities onto a Route; it never
contains order / refund / RAG business logic. Examples: REFUND_INQUIRY → RAG while
REFUND_REQUEST → REFUND_TOOL; UNSUPPORTED → ESCALATE; AMBIGUOUS → CLARIFY.

**Reason:**
Classification and routing are different concerns with different future implementations
(LLM classifier vs model/rule router). Keeping them separate avoids `if intent == ...` business
logic piling up in the router and keeps both independently replaceable and testable.

## Decision 020 — AgentState is domain state, not framework state

**Decision:**
`app/agent/state.py` defines our own domain `AgentState` / `AgentResult` dataclasses. Business and
orchestration code depend on these types; they never depend on a LangGraph state object. A later
LangGraph adapter maps domain state onto graph state (adapter layer), never the reverse.

**Reason:**
The orchestration framework must stay replaceable (Decision 002 constraint); domain state is the
stable contract across phases (observability / evaluation / HITL later read the same fields).

## Decision 021 — Agent layer has no direct database access

**Decision:**
The agent package never imports SQLAlchemy / `app.db` / `app.services` / `app.api` and never opens
a session. Knowledge reaches the workflow only through the existing `RetrievalPipeline` entry
point (injected as the `RetrievalRunner` interface); dynamic business data/actions are reached only
through planned `ToolRequest`s executed later by Tool → Service → Repository.

**Reason:**
Enforces Decision 001/003 boundaries at the Agent layer: no LLM → SQL, no RAG → order database,
no writing order state into the knowledge base, and no Agent code path that can bypass Service
validation (architecture test asserts the package imports stay clean).

## Decision 022 — Static knowledge vs dynamic data / business actions (agent level)

**Decision:**
At the Agent layer: static knowledge questions route to RAG; dynamic business data and business
actions route to the business tool interface. Phase 4A plans a `ToolRequest` but never executes a
tool; Phase 4B executes it through the Tool layer under Service rules (Risk Control / HITL in Phase 5).

**Reason:**
Restates Decision 001 at the agent boundary: policy content must never substitute authoritative
order / refund / logistics data, and business actions must never execute outside business rules.

## Decision 023 — Ambiguous requests are never guessed

**Decision:**
When a required entity is missing (e.g. refund / cancel without an explicit order id) or the
message references several distinct orders, the workflow routes to CLARIFY and returns a
`needs_clarification` Response State. The agent never picks a "most recent" order or the first id.

**Reason:**
Guessing order references on high-impact actions creates wrong-target risk. Asking is
deterministic, cheap and auditable, and matches PRD "do not guess" requirements for after-sales
actions.

## Decision 024 — LangGraph is orchestration only; Phase 4A stays framework-agnostic

**Decision:**
LangGraph remains the target orchestration / runtime layer (Decision 002), but Phase 4A adds no
LangGraph dependency. Phase 4A ships a small deterministic state machine
(`app/agent/workflow.py`) whose nodes are orchestration only; `AgentState` can be mapped onto
LangGraph state through a later adapter without rewriting business code.

**Reason:**
Phase 4A has no exercised need for an external graph runtime; adding LangGraph now would add
complexity without value and violate the dependency discipline (AGENTS.md "do not add unnecessary
dependencies"). The adapter seam keeps Decision 002 reachable later.

## Decision 025 — Tool Registry is an explicit allowlist

**Decision:**
All business tools are registered in one `ToolRegistry` (`backend/app/tools/registry.py`).
The Agent / ToolExecutor resolves tools by name from the registry only; there is no
`getattr` / `eval` / dynamic `importlib` dispatch. Duplicate registration raises
explicitly; unknown names produce the stable `UNKNOWN_TOOL` error.

**Reason:**
The registry is a security boundary: it makes the full set of callable tools
auditable and prevents a model or agent from invoking arbitrary functions.

## Decision 026 — Tool arguments must pass schema validation

**Decision:**
Every tool defines an explicit Pydantic input schema (`backend/app/tools/definitions.py`).
Model- or agent-produced arguments are never trusted: the ToolExecutor validates them
before any handler runs (missing fields / wrong types / empty strings / invalid order
references all normalize to `VALIDATION_ERROR`).

**Reason:**
"JSON came out of a model" is not an excuse to skip validation; invalid arguments must
fail fast with a stable, structured result instead of reaching the Service layer.

## Decision 027 — user_id uses the trusted execution context

**Decision:**
`ToolExecutionContext.user_id` comes from the authenticated Agent / session context.
The ToolExecutor always overrides any model-supplied `user_id` in arguments with the
trusted context value before validation.

**Reason:**
A model cannot escalate privileges by passing `user_id="another_user"`; identity is a
session property, never an argument.

## Decision 028 — Agent never reaches the DB; tools are the execution boundary

**Decision:**
The Agent layer never imports SQLAlchemy / `app.db` / `app.services` / `app.api`
(architecture test enforced since Phase 4A). Phase 4B business actions run through an
injected ToolExecutor: Agent → Tool → Service → Repository → Database.

**Reason:**
Keeps Decision 021 enforceable now that tools actually execute: there is still no Agent
code path that can bypass Service validation or write directly to the database.

## Decision 029 — Tools do not carry business rules

**Decision:**
Refund eligibility, authoritative refund amounts, the cancellation state machine,
duplicate-refund detection and ticket entity validation stay in the Service layer.
Tool handlers only normalize input/output and enforce the execution-time authorization
boundary (an order that exists but belongs to another user is rejected).

**Reason:**
Business rules must have exactly one owner (the Service layer) so Mock (Phase 2B) and
real backends behave identically; the Tool layer is a thin, replaceable adapter.

## Decision 030 — ToolResult uses stable domain schemas

**Decision:**
Tools never return ORM objects. Each tool declares an output schema
(`OrderToolOutput` / `LogisticsToolOutput` / `RefundEligibilityToolOutput` /
`RefundToolOutput` / `CancelOrderToolOutput` / `TicketToolOutput`) and the executor
stores JSON-serializable dicts in `AgentState.tool_results`.

**Reason:**
A stable, schema-bound result keeps Agent state serializable, prevents internal
database fields / sensitive data from leaking and gives observability / evaluation a
fixed contract to consume.

## Decision 031 — Phase 4B executes one tool call first (refund is conditional)

**Decision:**
Phase 4B keeps execution simple: a normal request plans one tool call, executes it once
and stores one ToolResult (the loop is data-driven and can grow later). REFUND_REQUEST
is the one explicit exception: it runs `check_refund_eligibility` first and only appends
`create_refund` when eligibility says eligible=True; otherwise the refund service is
never reached. Full Risk Control / Human-in-the-loop lands in Phase 5.

**Reason:**
Fidelity to the refund requirement (never create a refund on an ineligible order)
matters more than mechanical one-call symmetry; the sequence stays readable and the
second step is not a general agent loop.

## Decision 032 — Risk Engine is pure and policy-driven

**Decision:**
Phase 5 introduces `app/risk` (RiskEngine / RiskPolicy / RiskLevel / RiskAction /
RiskDecision / RiskContext). The mapping from operation (+ business context) to a
decision lives in policy data (including `high_value_refund_threshold = 500`),
never inside Tool Handlers. Operations without a registered rule are BLOCKed
(fail-closed) instead of auto-executed.

**Reason:**
Rules become auditable and changeable without touching tool or agent code, and an
unregistered operation can never silently execute.

## Decision 033 — Refund execution defaults to Human Approval

**Decision:**
`create_refund` is HIGH (HUMAN_APPROVAL); refunds at or above the configured
threshold are CRITICAL (still HUMAN_APPROVAL). Eligibility checks stay LOW /
AUTO_EXECUTE.

**Reason:**
A refund is real financial loss; the MVP never lets the agent execute a
money-affecting operation unconditionally, even after an eligibility check passes.

## Decision 034 — Approval binds the original ToolRequest and resumes its snapshot

**Decision:**
`approval_requests` persists tool_name + tool_arguments as an immutable snapshot
plus request_id / risk_level / reason / status / timestamps / resolved_by.
Approving resumes exactly that stored snapshot through the Tool Executor; the LLM
is never asked to re-plan or re-generate arguments after approval.

**Reason:**
Prevents the reviewed action (order X) silently becoming a different action
(order Y) at execution time, and keeps resume deterministic and auditable.

## Decision 035 — Execute → Verify re-checks authoritative business state

**Decision:**
After a successful `create_refund` / `cancel_order`, the workflow re-queries the
Repository / DB and requires the expected authoritative state (refund row exists
with status PENDING and amount == order.total_amount; order.status == CANCELLED).
Any mismatch ends the run with VERIFICATION_FAILED.

**Reason:**
Tool output can be stale or wrong; the database is the source of truth, so a fake
"tool success" can never be reported to the user as a completed refund/cancel.

## Decision 036 — Refund amount is never user- or agent-controlled

**Decision:**
Tool input schemas do not accept an amount field; the Service derives the refund
amount from the order's authoritative total_amount. The workflow passes only the
eligibility result amount into the RiskContext for classification and never into
execution.

**Reason:**
Neither the end user nor the model can influence financial amounts; there is one
authoritative computation of the refund amount.